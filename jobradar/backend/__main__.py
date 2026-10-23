"""Multi-user scheduled run, outside the API (e.g. from GitHub Actions):

    python -m jobradar.backend run-all [--no-refresh]

Needs FIREBASE_PROJECT_ID, FIREBASE_SERVICE_ACCOUNT, APP_ENCRYPTION_KEY; Gmail sync also
needs GMAIL_WEB_CLIENT_ID / GMAIL_WEB_CLIENT_SECRET / API_URL; alerts need VAPID_PRIVATE_KEY.
Prints only counts, never user data.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys



def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jobradar.backend")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run-all", help="refresh the job pool, match every user, sync connected Gmail accounts")
    run.add_argument("--no-refresh", action="store_true", help="match against the current pool without fetching")
    sub.add_parser("new-key", help="print a new APP_ENCRYPTION_KEY")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    if args.cmd == "new-key":
        from .secretbox import new_key
        print(new_key())
        return 0
    from ..config import load_config  # the server libraries are only needed from here on
    from ..http import make_session
    from . import tasks
    from .api import Settings
    from .store import FirestoreStore
    s = Settings.from_env()
    missing = [k for k in ("FIREBASE_PROJECT_ID", "FIREBASE_SERVICE_ACCOUNT", "APP_ENCRYPTION_KEY") if not os.environ.get(k)]
    if missing:
        print(f"Multi-user run skipped: {', '.join(missing)} not set")
        return 0
    session = make_session()
    store = FirestoreStore(s.project_id, session, json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"]))
    report = tasks.run_all(store, load_config(), session, oauth=s.oauth(), vapid_key=s.vapid_private_key,
                           vapid_subject=s.vapid_subject, refresh=not args.no_refresh)
    print(f"Multi-user run: {json.dumps(report)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
