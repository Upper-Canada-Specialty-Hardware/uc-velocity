"""Perform the import engine's plan against the target Velocity DB (the write half).

``engine.decide()`` only PLANS one of INSERT / UPDATE / ADOPT / SKIP per source row;
this module *applies* that plan through the app's SQLAlchemy models (Decision B in
``docs/MIGRATION_PARITY.md`` -- reuse the models so column/constraint rules live in
one place). It **never deletes**, and it **never commits or rolls back**: the caller
(``import_all.py``) owns one transaction for the whole run, so a preview run can be
rolled back wholesale and a real run committed once.

The write target is a scratch/preview Postgres reached via ``MIGRATION_DATABASE_URL``
and guarded by ``config.assert_scratch_target`` -- never production.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from .engine import Decision, INSERT, UPDATE, ADOPT, DUP, SKIP


class DomainResult:
    """Outcome of executing ONE domain's plan.

    Attributes:
        id_map: ``{legacy_id -> velocity row id}`` for every row inserted/updated/adopted,
            AND every DUP row (mapped to the row it duplicates), so downstream FK-dependent
            domains resolve their foreign keys even when they point at a duplicate source row.
        counts: action tallies (INSERT/UPDATE/ADOPT/DUP/SKIP).
        unresolved: human-readable reasons for rows that could not be written (a
            required FK/field did not resolve, or a planned target row was gone) --
            recorded rather than crashing the batch.
    """

    def __init__(self) -> None:
        self.id_map: dict[int, int] = {}
        self.counts: dict[str, int] = {INSERT: 0, UPDATE: 0, ADOPT: 0, DUP: 0, SKIP: 0}
        self.unresolved: list[str] = []


def execute_domain(
    session: Any,
    model: Any,
    rows: list[dict[str, Any]],
    decisions: list[Decision],
    to_fields: Callable[[dict[str, Any]], Optional[dict[str, Any]]],
    on_insert: Optional[Callable[[Any, Any, dict[str, Any]], None]] = None,
    insert_extra: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
) -> DomainResult:
    """Apply the planned decisions for one domain through the ORM.

    Args:
        session: SQLAlchemy Session bound to the TARGET (scratch) DB. Not committed here.
        model: The ORM class to write (e.g. ``Category``, ``Quote``).
        rows: Transformed source rows, in the SAME order as ``decisions``.
        decisions: ``engine.decide()`` output -- one Decision per row.
        to_fields: Maps a source row to a ``{column: value}`` dict for ``model``, with
            all foreign keys already resolved to velocity ids by the caller. Returns
            ``None`` to DROP a row that cannot be written (e.g. a required FK that did
            not resolve); the drop is recorded, never raised.
        on_insert: Optional hook ``(session, obj, row)`` run after a FRESH insert is
            flushed (so ``obj.id`` exists), for child rows -- e.g. a profile's contacts.
            Not called on UPDATE/ADOPT (existing children are left untouched, which keeps
            re-runs idempotent instead of duplicating children).
        insert_extra: Optional ``row -> {column: value}`` merged into the fields ONLY on
            INSERT -- for insert-only columns that must not be overwritten on ADOPT/UPDATE
            (chiefly a per-project ``quote_sequence`` / ``po_sequence``, which an adopted
            existing row already owns and must keep).

    Returns:
        A :class:`DomainResult`. Does NOT commit or roll back.
    """
    result = DomainResult()
    for row, dec in zip(rows, decisions):
        if dec.action == SKIP:                         # engine already decided it can't be keyed
            result.counts[SKIP] += 1
            continue

        promoted_dup = False                           # set when a DUP is promoted to writer (its claimant couldn't write)
        if dec.action == DUP:                          # same natural key already handled this run
            src_id = result.id_map.get(dec.dup_of_legacy_id)   # the first claimant's velocity id, if it wrote
            if src_id is not None:                     # claimant wrote a row -> resolve to it, no second write
                result.id_map[dec.legacy_id] = src_id  # this legacy_id resolves to that same row
                result.counts[DUP] += 1                # tally the dedup (no write performed)
                continue
            # Claimant did NOT write (its own FK/field didn't resolve -> it was skipped), so the natural key
            # is not actually taken. Promote THIS duplicate to be the writer rather than drop a writable row
            # (the engine claims a natural key at plan time, before writability is known). Fall through to INSERT.
            promoted_dup = True

        fields = to_fields(row)                        # map columns + resolve FKs to velocity ids
        if fields is None:                             # a required FK/field didn't resolve -> can't write
            result.counts[SKIP] += 1                   # a promoted dup that also can't write simply skips too
            result.unresolved.append(
                f"{dec.legacy_source}#{dec.legacy_id}: required field/FK did not resolve"
            )
            continue

        if dec.action == INSERT or promoted_dup:       # genuinely new (or a promoted duplicate) -> create it
            if insert_extra is not None:               # merge insert-only columns (e.g. quote_sequence)
                fields = {**fields, **insert_extra(row)}
            obj = model(**fields)                      # build the ORM row from the mapped fields
            obj.legacy_source = dec.legacy_source      # stamp the key so a re-run UPDATEs, never duplicates
            obj.legacy_id = dec.legacy_id
            session.add(obj)                           # stage the insert
            session.flush()                            # assign obj.id (no commit yet)
            result.id_map[dec.legacy_id] = obj.id      # record for downstream FK resolution
            if promoted_dup:                           # further dups still point at the FAILED claimant's id,
                result.id_map[dec.dup_of_legacy_id] = obj.id   # so resolve that id to this now-written row too
            result.counts[INSERT] += 1
            if on_insert is not None:                  # create child rows (e.g. contacts) for a fresh row
                on_insert(session, obj, row)
        else:                                          # UPDATE or ADOPT -> mutate the existing row in place
            obj = session.get(model, dec.target_id)    # load the planned target
            if obj is None:                            # target disappeared between plan and apply
                result.counts[SKIP] += 1
                result.unresolved.append(
                    f"{dec.legacy_source}#{dec.legacy_id}: planned target row {dec.target_id} not found"
                )
                continue
            for col, val in fields.items():            # refresh the mapped columns from the source
                setattr(obj, col, val)
            obj.legacy_source = dec.legacy_source      # ADOPT stamps the key (the one-time backfill);
            obj.legacy_id = dec.legacy_id              #   UPDATE just re-affirms it. NEVER touches other rows.
            result.id_map[dec.legacy_id] = obj.id      # record for downstream FK resolution
            result.counts[dec.action] += 1
    return result
