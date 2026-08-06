"""The Vision -> Velocity import engine and its per-domain callers.

``engine.py`` holds the pure, database-free decision logic (update / adopt /
insert, never delete). Per-domain modules wire it to real tables.
"""
