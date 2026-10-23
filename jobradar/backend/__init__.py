"""Job Radar multi-user backend: per-user Gmail, resumes, matching and drafts.

Runs as one small web service (FastAPI, `jobradar.backend.api:app`) plus a cron
endpoint that a scheduler calls. Secrets never reach the browser: the service
verifies each request's Firebase ID token and only ever touches that user's data.
"""
