"""Pytest configuration — skip DB-dependent tests when Postgres is unreachable."""
import socket

def _pg_reachable():
    """Quick TCP check to see if Postgres is listening on localhost:5432."""
    try:
        s = socket.create_connection(("localhost", 5432), timeout=1)
        s.close()
        return True
    except OSError:
        return False

# collect_ignore is evaluated before pytest imports test modules.
# This prevents the ImportError from `from main import app` when
# there's no local Postgres — while CI (which has Postgres) is unaffected.
if not _pg_reachable():
    collect_ignore = ["test_smoke.py", "test_backlog_report.py", "test_invoice_summary.py"]
