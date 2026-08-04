"""UC Vision -> UC Velocity migration tooling (local, Windows-only).

This package is *local one-time cutover tooling*, NOT part of the deployed
backend. It reads the legacy UC Vision Access (.mdb) backend and loads it into
Postgres. It is intentionally standalone: it does NOT import the FastAPI app or
its ORM, and its dependencies live in this package's own requirements.txt so
they never reach the Linux-deployed `backend/requirements.txt`.

PKG 1 scope (this commit):
  - Stage A "raw staging": copy every Vision user table verbatim into an
    isolated `vision_legacy` Postgres schema (M4).
  - A load-verification harness: prove staged row counts match the source.

Later packages add Stage B (the idempotent legacy-keyed sync into Velocity's
real tables) on top of this foundation.
"""
