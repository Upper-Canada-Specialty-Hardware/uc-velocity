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
  * DUP    -- a row EARLIER in this same run already created/adopted this row's
    natural key. A natural key names one real entity, so this is the same thing
    listed twice in the Vision export (e.g. a part number typed once with and once
    without a stray space). It writes nothing and instead resolves to that first
    row -- so a UNIQUE natural-key column (parts.part_number) can't be violated and
    an unconstrained one (labour/misc by description) can't be silently duplicated.
  * SKIP   -- the source row has no usable id, so it can't be keyed or matched.

It NEVER deletes. It is deliberately kept free of any database access so it is
trivially unit-testable and cannot touch live data -- callers pass in plain
lookups (built from a read-only copy for a dry run, or the ORM for a real run),
and the engine just returns a plan. Performing the plan is the caller's job.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Hashable, Optional

# The five actions the engine can choose. Strings (not an enum) so the ledger and
# any printed report read plainly without extra imports.
INSERT = "insert"
UPDATE = "update"
ADOPT = "adopt"
DUP = "dup"        # same natural key already handled earlier in this run -> resolve to it, write nothing
SKIP = "skip"


@dataclass(frozen=True)
class Decision:
    """One planned action for one source row (no write has happened yet).

    Attributes:
        action: One of INSERT / UPDATE / ADOPT / DUP / SKIP.
        legacy_source: The Vision table the source row came from.
        legacy_id: The source row's Vision primary key, or None when SKIPped.
        target_id: The existing Velocity row id to update/adopt, or None on INSERT/DUP/SKIP.
        reason: Short human-readable why, for the dry-run ledger.
        dup_of_legacy_id: On a DUP only, the legacy_id of the earlier row in this run
            that already claimed this natural key; the executor resolves this row to
            that row's Velocity id. None on every other action.
    """
    action: str
    legacy_source: str
    legacy_id: Optional[int]
    target_id: Optional[int]
    reason: str
    dup_of_legacy_id: Optional[int] = None


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


def occurrence_keys(keys: list[Optional[Hashable]]) -> list[Optional[Hashable]]:
    """Make repeated natural keys distinct by numbering their occurrences in order.

    Child rows (quote lines, PO lines) have no unique business key of their own: two
    genuinely separate lines on one quote can be byte-identical (same part, qty and
    price). Numbering identical keys 1, 2, 3... in list order turns them into
    distinct natural keys, so the FIRST such source line adopts the FIRST such
    existing line, the second the second, and none of them is mistaken for a DUP.
    Both sides (existing rows in id order; source rows in export order) must be
    numbered the same way for the pairing to hold. ``None`` stays ``None``.

    Args:
        keys: The natural key of each row, in row order (``None`` = not adoptable).

    Returns:
        A list of ``(key, occurrence)`` tuples (``None`` where the input was ``None``).
    """
    seen: dict[Hashable, int] = {}
    out: list[Optional[Hashable]] = []
    for key in keys:
        if key is None:                       # not adoptable -> stays not adoptable
            out.append(None)
            continue
        seen[key] = seen.get(key, 0) + 1      # 1-based occurrence of this exact key so far
        out.append((key, seen[key]))
    return out


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
    # Natural keys already claimed EARLIER in THIS run, each mapped to the legacy_id of
    # the first source row that claimed it (by ADOPT or INSERT). A natural key names one
    # real entity, so only the first row may write it; a later row with the same natural
    # key is the same thing duplicated in the Vision export and becomes a DUP that
    # resolves to that first row. This also means an existing un-keyed row is adopted at
    # most once (the first claimant adopts; the rest DUP), replacing the old approach
    # where later duplicates fell through to a second INSERT.
    claimed_natural: dict[Hashable, int] = {}

    for row in source_rows:
        legacy_id = spec.legacy_id_of(row)
        if legacy_id is None:
            # No Vision id -> can't be keyed or matched; leave it alone.
            decisions.append(Decision(SKIP, spec.legacy_source, None, None, "source row has no legacy id"))
            continue

        # The natural key names the one real entity this row is; compute it once and
        # record it as "claimed" on whichever branch handles this row, so a later source
        # row with the SAME natural key becomes a DUP of this one, not a second write.
        natural = spec.natural_key_of(row)

        key = (spec.legacy_source, legacy_id)
        if key in existing_by_legacy:
            # A prior run already stamped this exact source row -> refresh in place. Claim
            # the natural key so a duplicate later in this run DUPs to this row instead of
            # ADOPTing (and re-stamping) it -- which keeps re-runs stable, not oscillating.
            if natural is not None:
                claimed_natural[natural] = legacy_id
            decisions.append(Decision(UPDATE, spec.legacy_source, legacy_id,
                                      existing_by_legacy[key], "matched by stored legacy key"))
            continue

        if natural is not None and natural in claimed_natural:
            # An earlier row this run already created/adopted this natural key -> same
            # real entity duplicated in the source. Resolve to that first row (its
            # Velocity id is filled in at execute time) and write nothing.
            decisions.append(Decision(DUP, spec.legacy_source, legacy_id, None,
                                      "duplicate natural key already handled this run",
                                      dup_of_legacy_id=claimed_natural[natural]))
            continue

        if natural is not None and natural in existing_by_natural:
            # Same real entity already exists un-keyed in Velocity -> claim it instead of
            # duplicating. Record the claim so later duplicates DUP to this row.
            claimed_natural[natural] = legacy_id
            decisions.append(Decision(ADOPT, spec.legacy_source, legacy_id,
                                      existing_by_natural[natural], "adopted existing row by natural key"))
            continue

        # Nothing matched -> genuinely new. Record the claim so a later source row sharing
        # this natural key becomes a DUP of this insert instead of a second write.
        if natural is not None:
            claimed_natural[natural] = legacy_id
        decisions.append(Decision(INSERT, spec.legacy_source, legacy_id, None, "no match -> insert"))

    return decisions


def summarize(decisions: list[Decision]) -> dict[str, int]:
    """Tally decisions by action for the dry-run ledger.

    Args:
        decisions: The plan from :func:`decide`.

    Returns:
        ``{action: count}`` covering all five actions (zeros included).
    """
    counts = {INSERT: 0, UPDATE: 0, ADOPT: 0, DUP: 0, SKIP: 0}
    for d in decisions:
        counts[d.action] += 1
    return counts
