"""Idempotent Vision -> Velocity import engine (pure decision logic).

The importer must be safe to re-run: a second run may NOT create duplicates of
rows it already brought in, and it must never touch or delete rows a user created
directly in Velocity. This module decides, for each source row, which ONE of these
the importer would do:

  * UPDATE -- a Velocity row already carries this source row's stored key
    (legacy_source, legacy_id): refresh it in place.
  * ADOPT  -- no stored key matches, but an existing Velocity row is clearly the
    same real thing (its "natural key" matches -- e.g. a quote's work-order
    number). Claim that row by stamping the key onto it, then update it. This is
    what lets the FIRST run take ownership of rows an earlier, un-keyed import
    (or the original CSV import) already created, instead of inserting duplicates.
  * INSERT -- neither matches: it's genuinely new, so create it.
  * SKIP   -- the source row has no usable id, so it can't be keyed or matched.

It NEVER deletes. It is deliberately kept free of any database access so it is
trivially unit-testable and cannot touch live data -- callers pass in plain
lookups (built from a read-only copy for a dry run, or the ORM for a real run),
and the engine just returns a plan. Performing the plan is the caller's job.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Hashable, Optional

# The four actions the engine can choose. Strings (not an enum) so the ledger and
# any printed report read plainly without extra imports.
INSERT = "insert"
UPDATE = "update"
ADOPT = "adopt"
SKIP = "skip"


@dataclass(frozen=True)
class Decision:
    """One planned action for one source row (no write has happened yet).

    Attributes:
        action: One of INSERT / UPDATE / ADOPT / SKIP.
        legacy_source: The Vision table the source row came from.
        legacy_id: The source row's Vision primary key, or None when SKIPped.
        target_id: The existing Velocity row id to update/adopt, or None on INSERT/SKIP.
        reason: Short human-readable why, for the dry-run ledger.
    """
    action: str
    legacy_source: str
    legacy_id: Optional[int]
    target_id: Optional[int]
    reason: str


@dataclass(frozen=True)
class DomainSpec:
    """Everything the engine needs to route ONE domain without knowing its shape.

    Attributes:
        legacy_source: The Vision table these rows come from (stored as legacy_source).
        legacy_id_of: Given a source row, its Vision primary key (its legacy_id),
            or None if the row can't be keyed.
        natural_key_of: Given a source row, a value that identifies the same real
            entity in an already-migrated-but-unkeyed Velocity row (e.g. the
            work-order number for a quote), or None if it can't be adopted.
    """
    legacy_source: str
    legacy_id_of: Callable[[dict[str, Any]], Optional[int]]
    natural_key_of: Callable[[dict[str, Any]], Optional[Hashable]]


def decide(
    source_rows: list[dict[str, Any]],
    spec: DomainSpec,
    existing_by_legacy: dict[tuple[str, int], int],
    existing_by_natural: dict[Hashable, int],
) -> list[Decision]:
    """Plan the importer's action for each source row -- reads nothing, writes nothing.

    Args:
        source_rows: Transformed source rows for one domain.
        spec: How to read this domain's legacy id + natural key.
        existing_by_legacy: ``{(legacy_source, legacy_id): velocity_id}`` for rows a
            previous run already keyed -> these UPDATE.
        existing_by_natural: ``{natural_key: velocity_id}`` for existing Velocity rows
            that have NO legacy key yet but can be adopted by their natural key.

    Returns:
        One :class:`Decision` per source row, in the same order.
    """
    decisions: list[Decision] = []
    # An existing un-keyed row may be adopted only ONCE: if two source rows share a
    # natural key, the first adopts it and the rest fall through to INSERT rather
    # than both claiming the same Velocity row.
    already_adopted: set[Hashable] = set()

    for row in source_rows:
        legacy_id = spec.legacy_id_of(row)
        if legacy_id is None:
            # No Vision id -> can't be keyed or matched; leave it alone.
            decisions.append(Decision(SKIP, spec.legacy_source, None, None, "source row has no legacy id"))
            continue

        key = (spec.legacy_source, legacy_id)
        if key in existing_by_legacy:
            # A prior run already stamped this exact source row -> refresh in place.
            decisions.append(Decision(UPDATE, spec.legacy_source, legacy_id,
                                      existing_by_legacy[key], "matched by stored legacy key"))
            continue

        natural = spec.natural_key_of(row)
        if natural is not None and natural in existing_by_natural and natural not in already_adopted:
            # Same real entity already exists un-keyed -> claim it instead of duplicating.
            already_adopted.add(natural)
            decisions.append(Decision(ADOPT, spec.legacy_source, legacy_id,
                                      existing_by_natural[natural], "adopted existing row by natural key"))
            continue

        # Nothing matched -> it's genuinely new.
        decisions.append(Decision(INSERT, spec.legacy_source, legacy_id, None, "no match -> insert"))

    return decisions


def summarize(decisions: list[Decision]) -> dict[str, int]:
    """Tally decisions by action for the dry-run ledger.

    Args:
        decisions: The plan from :func:`decide`.

    Returns:
        ``{action: count}`` covering all four actions (zeros included).
    """
    counts = {INSERT: 0, UPDATE: 0, ADOPT: 0, SKIP: 0}
    for d in decisions:
        counts[d.action] += 1
    return counts
