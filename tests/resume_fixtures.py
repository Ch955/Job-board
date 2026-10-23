"""Synthetic resumes (made-up person) built in code: a text PDF and a DOCX."""

import io
import zipfile

LINES = [
    "Asha Rao", "asha.rao@example.com | +91 98765 43210 | linkedin.com/in/asha-rao-demo", "",
    "Professional Summary",
    "Data Engineer with 3 years of experience building batch and streaming pipelines.",
    "Experience",
    "Data Engineer, Northwind Analytics", "Jun 2022 - Present",
    "- Built Airflow and dbt pipelines loading Snowflake models used by 40 analysts every day.",
    "- Migrated nightly SQL Server jobs to PySpark on Databricks, cutting runtime from 6 hours to 50 minutes.",
    "Associate Engineer, Contoso", "Jan 2021 - May 2022",
    "- Wrote Python ETL jobs and REST APIs for the reporting team.",
    "Education",
    "B.Tech in Computer Science, Example Institute of Technology, 2020",
    "Certifications",
    "Microsoft Certified: Azure Data Engineer Associate (DP-203)",
    "Projects",
    "Real-time clickstream pipeline with Kafka and Spark Structured Streaming",
    "Skills",
    "Python, SQL, PySpark, Airflow, dbt, Snowflake, Kafka, Docker, Git",
]


def pdf_bytes(lines=LINES) -> bytes:
    def esc(s): return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    ops = ["BT", "/F1 10 Tf", "14 TL", "50 800 Td"] + [f"({esc(l)}) Tj T*" for l in lines] + ["ET"]
    stream = "\n".join(ops).encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + o + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    return out.getvalue()


def docx_bytes(lines=LINES) -> bytes:
    from xml.sax.saxutils import escape
    body = "".join(f"<w:p><w:r><w:t>{escape(l)}</w:t></w:r></w:p>" for l in lines)
    doc = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
           f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", doc)
    return buf.getvalue()
