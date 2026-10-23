"""Command line entry point.

    python -m jobradar run                 # fetch everything once, write docs/jobs.json
    python -m jobradar run --offline       # same, against bundled fixtures (no network, no keys)
    python -m jobradar run --sources greenhouse,lever --no-write
    python -m jobradar run --notify        # ...and send phone alerts for strong new jobs
    python -m jobradar check-companies     # find dead company slugs in config.yaml
    python -m jobradar push-keys           # one-time: create keys for phone notifications
    python -m jobradar notify --test       # send a test notification
    python -m jobradar notify --summary    # send the daily summary
"""

from __future__ import annotations

import argparse
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import json

from . import notify, pipeline, push
from .config import ROOT, load_config, load_dotenv
from .http import make_session
from .scoring import Scorer
from .sources import REGISTRY
from .sources.base import SourceContext


def _print_summary(result: pipeline.RunResult, top: int) -> None:
    print("\nSources")
    for name, r in result.source_runs.items():
        extra = r.get("reason") or r.get("error") or ""
        warn = f"  ({r['warning_count']} warnings)" if r.get("warning_count") else ""
        print(f"  {name:<16} {r['status']:<10} {r['count']:>5} jobs {r.get('seconds', '')!s:>5}s  {extra}{warn}")
        for w in r.get("warnings", [])[:5]:
            print(f"      ! {w}")
    print("\nDropped:", ", ".join(f"{k}={v}" for k, v in result.dropped.items()))
    print(f"Kept {len(result.jobs)} jobs, {len(result.new_jobs)} new this run")
    if result.output:
        try:
            shown = result.output.relative_to(ROOT)
        except ValueError:
            shown = result.output
        print(f"Wrote {shown}")
    if not result.jobs:
        return
    print(f"\nTop {min(top, len(result.jobs))}")
    for j in result.jobs[:top]:
        new = "*" if j in result.new_jobs else " "
        print(f" {new}{j.score:>3}  {j.title[:40]:<40} {j.company[:18]:<18} {j.location[:22]:<22} {j.source}")
        print(f"        matched: {', '.join(j.matched_keywords) or '-'}  | {j.url}")


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    only = set(args.sources.split(",")) if args.sources else None
    if only and (unknown := only - set(REGISTRY)):
        print(f"Unknown source(s): {', '.join(sorted(unknown))}. Known: {', '.join(REGISTRY)}")
        return 2
    session = None
    output = Path(args.output) if args.output else None
    if args.offline:
        from .offline import OfflineSession, enable_fake_keys

        enable_fake_keys()
        session = OfflineSession()
        # Never let demo data overwrite the real file the PWA reads
        output = output or ROOT / "out" / "jobs.offline.json"
    result = pipeline.run(
        config,
        only=only,
        session=session,
        output=output,
        write=not args.no_write,
        ignore_throttle=args.offline or args.force,
    )
    _print_summary(result, args.top)
    if args.notify and not args.offline:
        p = config.get("pipeline", {})
        payloads = notify.alert_payloads(result.new_jobs, int(p.get("alert_score", 70)),
                                         int(p.get("max_alerts_per_run", 5)))
        report = notify.send_payloads(payloads, notify.subject_for(config))
        print(f"\nAlerts: {len(payloads)} to send -> {report}")
    # Exit non-zero only if every source that tried to run failed
    attempted = [r for r in result.source_runs.values() if r["status"] in ("ok", "error")]
    return 1 if attempted and all(r["status"] == "error" for r in attempted) else 0


def cmd_check_companies(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    session = make_session(float(config.get("pipeline", {}).get("http_timeout_s", 20)))
    scorer = Scorer(config)
    rows = []
    for ats in ("greenhouse", "lever", "ashby", "smartrecruiters"):
        module = REGISTRY[ats]
        cfg = dict(config.get("sources", {}).get(ats) or {}, max_details_per_company=0)
        for company in config.get("companies", {}).get(ats) or []:
            rows.append((ats, module, cfg, company))

    def check(row):
        ats, module, cfg, company = row
        ctx = SourceContext(config=config, source_cfg=cfg, session=session)
        try:
            jobs = module.fetch_one(ctx, company)
        except Exception as e:  # noqa: BLE001
            code = getattr(getattr(e, "response", None), "status_code", None)
            return ats, company["slug"], f"FAIL {code or type(e).__name__}", 0, 0, 0
        relevant = [j for j in jobs if scorer.is_relevant(j)]
        eligible = [j for j in relevant if scorer.location_points(j)[2]]
        return ats, company["slug"], "ok", len(jobs), len(relevant), len(eligible)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(check, rows))
    print(f"{'ats':<16}{'slug':<18}{'status':<12}{'jobs':>6}{'data-eng':>10}{'india/remote':>14}")
    for r in results:
        print(f"{r[0]:<16}{r[1]:<18}{r[2]:<12}{r[3]:>6}{r[4]:>10}{r[5]:>14}")
    dead = [r for r in results if r[2] != "ok"]
    print(f"\n{len(results) - len(dead)} ok, {len(dead)} failing. Fix or remove failing slugs in config.yaml.")
    return 0


def cmd_push_keys(args: argparse.Namespace) -> int:
    private, public = push.generate_vapid_keys()
    print("Phone notification keys created.\n")
    print("1) Add this as a GitHub Secret named VAPID_PRIVATE_KEY (keep it secret):\n")
    print(f"   {private}\n")
    print("   For local runs you can also put it in .env as VAPID_PRIVATE_KEY=...\n")
    print(f"2) Public key (not secret; Job Radar publishes it for the app automatically):\n\n   {public}")
    return 0


def cmd_notify(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    alert_score = int(config.get("pipeline", {}).get("alert_score", 70))
    if args.test:
        payloads = [{"title": "Job Radar test", "body": "Alerts are working. You'll hear about jobs scoring "
                     f"{alert_score}+.", "tag": "test"}]
    else:
        path = ROOT / config.get("pipeline", {}).get("output", "docs/jobs.json")
        jobs = json.loads(path.read_text(encoding="utf-8")).get("jobs", []) if path.exists() else []
        payloads = [notify.summary_payload(jobs, alert_score)]
    print("Sending:", json.dumps(payloads, ensure_ascii=False, indent=1))
    report = notify.send_payloads(payloads, notify.subject_for(config))
    print("Result:", report)
    if report["skipped"]:
        print(f"Not sent: {report['skipped']}")
        return 1
    return 0 if report["sent"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobradar", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="path to config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="fetch, score and save once")
    p_run.add_argument("--sources", help="comma separated subset, e.g. greenhouse,adzuna")
    p_run.add_argument("--offline", action="store_true", help="use bundled fixtures, no network")
    p_run.add_argument("--no-write", action="store_true", help="don't write the output file")
    p_run.add_argument("--force", action="store_true", help="ignore per-source min_interval_hours")
    p_run.add_argument("--output", help="output path (default docs/jobs.json)")
    p_run.add_argument("--top", type=int, default=15, help="how many jobs to print")
    p_run.add_argument("--notify", action="store_true", help="send phone alerts for strong new jobs")
    p_run.set_defaults(func=cmd_run)

    p_keys = sub.add_parser("push-keys", help="create VAPID keys for phone notifications")
    p_keys.set_defaults(func=cmd_push_keys)

    p_ntf = sub.add_parser("notify", help="send the daily summary or a test notification")
    mode = p_ntf.add_mutually_exclusive_group(required=True)
    mode.add_argument("--summary", action="store_true", help="last 24h summary from docs/jobs.json")
    mode.add_argument("--test", action="store_true", help="a test notification")
    p_ntf.set_defaults(func=cmd_notify)

    p_chk = sub.add_parser("check-companies", help="validate company slugs in config.yaml")
    p_chk.set_defaults(func=cmd_check_companies)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s")
    load_dotenv()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
