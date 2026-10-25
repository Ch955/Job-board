"""Skill names and how they're written, for reading resumes and postings.

Shared by resume extraction (which skills you have) and per-user matching (which
skills a job asks for). Plain regexes, case-insensitive. Add entries freely; a
skill only counts for you once you've confirmed it in your profile.
"""

from __future__ import annotations

import re
from functools import lru_cache

# name: patterns (regex, case-insensitive)
VOCAB: dict[str, list[str]] = {
    # Data engineering / platforms
    "Azure Data Factory": [r"azure data factory", r"\badf\b"],
    "Databricks": [r"databricks"],
    "PySpark": [r"pyspark"],
    "Spark": [r"\bspark\b", r"spark sql"],
    "ADLS Gen2": [r"\badls\b", r"adls gen ?2", r"azure data lake"],
    "Synapse": [r"synapse"],
    "Microsoft Fabric": [r"microsoft fabric"],
    "Delta Lake": [r"delta lake", r"delta tables?", r"lakehouse"],
    "Snowflake": [r"snowflake"],
    "BigQuery": [r"bigquery", r"big query"],
    "Redshift": [r"redshift"],
    "AWS Glue": [r"aws glue", r"\bglue\b"],
    "EMR": [r"\bemr\b"],
    "Athena": [r"\bathena\b"],
    "Kinesis": [r"kinesis"],
    "Dataflow": [r"\bdataflow\b"],
    "Dataproc": [r"dataproc"],
    "Airflow": [r"airflow", r"cloud composer"],
    "dbt": [r"\bdbt\b"],
    "Kafka": [r"\bkafka\b"],
    "Event Hubs": [r"event ?hubs?"],
    "Hadoop": [r"hadoop", r"\bhdfs\b"],
    "Hive": [r"\bhive\b"],
    "Informatica": [r"informatica"],
    "SSIS": [r"\bssis\b"],
    "Talend": [r"talend"],
    "ETL/ELT": [r"\betl\b", r"\belt\b"],
    "Data Warehousing": [r"data ?warehous", r"\bdwh?\b", r"\bedw\b"],
    "Data Modeling": [r"data model(l)?ing", r"dimensional model", r"star schema"],
    "SCD": [r"\bscd\b", r"slowly changing dimension"],
    "Incremental Load": [r"incremental (load|ingestion|refresh)", r"change data capture", r"\bcdc\b", r"watermark"],
    "Data Migration": [r"data migration", r"cloud migration"],
    "Data Governance": [r"data governance", r"unity catalog", r"purview", r"collibra"],
    "Data Quality": [r"data quality", r"great expectations"],
    "Streaming": [r"stream(ing)? (data|processing|pipelines?)", r"structured streaming", r"real[- ]time (data|pipelines?)"],
    # Databases
    "SQL": [r"\bsql\b", r"t-sql", r"tsql"],
    "SQL Server": [r"sql server", r"\bmssql\b"],
    "PostgreSQL": [r"postgres(ql)?"],
    "MySQL": [r"mysql"],
    "Oracle": [r"\boracle\b", r"pl/?sql"],
    "MongoDB": [r"mongo ?db"],
    "Cassandra": [r"cassandra"],
    "Cosmos DB": [r"cosmos ?db"],
    "Redis": [r"\bredis\b"],
    "Elasticsearch": [r"elastic ?search", r"opensearch"],
    # Languages
    "Python": [r"\bpython\b"],
    "Scala": [r"\bscala\b"],
    "Java": [r"\bjava\b(?!\s*script)"],
    "JavaScript": [r"javascript", r"\bjs\b"],
    "TypeScript": [r"typescript"],
    "Go": [r"\bgolang\b", r"\bgo\b(?= (developer|engineer|language))"],
    "C#": [r"c#", r"\.net\b", r"dotnet"],
    "C++": [r"c\+\+"],
    "R": [r"\br\b(?= (programming|language|studio))", r"rstudio"],
    "Bash": [r"\bbash\b", r"shell script"],
    # Cloud / DevOps
    "Azure": [r"\bazure\b"],
    "AWS": [r"\baws\b", r"amazon web services"],
    "GCP": [r"\bgcp\b", r"google cloud"],
    "Azure DevOps": [r"azure devops"],
    "CI/CD": [r"ci ?/ ?cd", r"continuous (integration|delivery|deployment)"],
    "Git": [r"\bgit\b", r"github", r"gitlab", r"bitbucket"],
    "Docker": [r"docker", r"container(s|i[sz]ation)?\b"],
    "Kubernetes": [r"kubernetes", r"\bk8s\b", r"\baks\b", r"\beks\b", r"\bgke\b"],
    "Terraform": [r"terraform"],
    "Linux": [r"\blinux\b", r"\bunix\b"],
    "Key Vault": [r"key ?vault"],
    "Azure Functions": [r"azure functions?", r"function apps?"],
    "Logic Apps": [r"logic apps?"],
    "Lambda": [r"\blambda\b"],
    "S3": [r"\bs3\b"],
    # Analytics / BI / ML
    "Power BI": [r"power ?bi"],
    "Tableau": [r"tableau"],
    "Looker": [r"looker"],
    "Excel": [r"\bexcel\b"],
    "Pandas": [r"pandas"],
    "NumPy": [r"numpy"],
    "Machine Learning": [r"machine learning", r"\bml\b"],
    "MLflow": [r"mlflow"],
    "Statistics": [r"statistic(s|al)"],
    # Software
    "REST APIs": [r"rest(ful)? apis?", r"\bapis?\b"],
    "Microservices": [r"micro-?services"],
    "React": [r"\breact(\.js|js)?\b"],
    "Node.js": [r"node(\.js|js)?\b"],
    "Django": [r"django"],
    "Flask": [r"\bflask\b"],
    "FastAPI": [r"fastapi"],
    "Spring Boot": [r"spring ?boot", r"\bspring\b"],
    "Agile": [r"\bagile\b", r"\bscrum\b"],
    "Jira": [r"\bjira\b"],
}

DEGREES = [
    (r"\b(ph\.?d|doctorate)\b", "PhD"),
    (r"\b(m\.?\s?tech|m\.?e\.?|m\.?s\.?|msc|m\.?sc|master'?s?|mca|mba)\b", "Master's"),
    (r"\b(b\.?\s?tech|b\.?e\.?|b\.?s\.?|bsc|b\.?sc|bachelor'?s?|bca|b\.?com)\b", "Bachelor's"),
    (r"\bdiploma\b", "Diploma"),
]


# Having the first skill means you have these too (only clear-cut cases: PySpark *is* Spark)
IMPLIES = {
    "pyspark": ["Spark"],
    "spark sql": ["Spark"],
    "fabric": ["Microsoft Fabric"],
    "microsoft fabric": ["Fabric"],
    "adf": ["Azure Data Factory"],
    "azure data factory": ["ADF"],
    "azure databricks": ["Databricks"],
    "adls": ["ADLS Gen2"],
    "adls gen2": ["ADLS"],
    "azure data lake storage": ["ADLS Gen2"],
}


def covered(skills: list[str]) -> set[str]:
    """Lower-cased names of every skill your profile covers, including the ones implied above."""
    out = {s.lower() for s in skills or []}
    for s in list(out):
        out.update(x.lower() for x in IMPLIES.get(s, []))
    return out


@lru_cache(maxsize=None)
def _compiled() -> list[tuple[str, list[re.Pattern]]]:
    return [(name, [re.compile(p, re.I) for p in pats]) for name, pats in VOCAB.items()]


def find_skills(text: str) -> list[str]:
    """Known skills mentioned in `text`, in vocabulary order."""
    return [name for name, pats in _compiled() if any(p.search(text or "") for p in pats)]


def patterns_for(name: str) -> list[str]:
    """Regexes for a skill name; an unknown (user-typed) skill matches as a whole phrase."""
    return VOCAB.get(name) or [r"(?<![a-z0-9])" + re.escape(name.lower()) + r"(?![a-z0-9])"]


def canonical(name: str) -> str:
    low = name.strip().lower()
    for k in VOCAB:
        if k.lower() == low:
            return k
    hits = find_skills(name)
    return hits[0] if len(hits) == 1 and len(name) <= 30 else name.strip()
