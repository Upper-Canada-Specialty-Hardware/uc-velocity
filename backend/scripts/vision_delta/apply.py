"""Update tool: bring everything Vision has into Velocity, log each change, report what changed.

Run from ``backend/``::

    python -m scripts.vision_delta.apply --csv <export folder> --target <local postgres url> --out <folder>
                                         [--commit] [--decisions decisions.csv]
                                         [--june-cutoff YYYY-MM-DD] [--projects-since YYYY-MM-DD]

then check it with ``python -m scripts.vision_delta.verify`` (see verify.py).

It runs on a LOCAL copy only. Production is applied in measured batches
through the admin API instead (plan.py, then push.py): one transaction this
size filled the production volume on 2026-10-29.

It runs the same compare as ``python -m scripts.vision_delta``, decides row by
row what to do (see :func:`rule`), and writes in ONE database transaction:

* without ``--commit`` (the default) every write is made and then rolled back,
  so a dry run hits the same constraints a real run would and changes nothing;
* with ``--commit`` the transaction is committed.

What it writes (certain, nothing for a person to decide):
    every record Vision has and Velocity lacks is added: customers/vendors,
        parts, labour, misc, projects, quotes with their lines, POs with their
        lines. A Vision project whose UCA number Velocity already uses for
        another project gets the next free number. A work order that looks
        like a quote already made in Velocity is still added (it is listed for
        a person; the Vision WO # on it makes it plain where it came from);
    the state the June import threw away, and its other leftovers (rounded
        quantities, every line fulfilled, every quote/PO closed, its own
        timestamp as a created date), and changes Vision made after June to a
        record Velocity left untouched, are set to Vision's values. Quantities
        are stored as Vision has them: credits (negative), zeros and fractions;
    every paired quote, PO and project gets its Vision number recorded.
Nothing is ever deleted; records that exist only in Velocity are never touched.

What it keeps for a person (Velocity's value stays, the row is listed):
    differences Velocity made since June, differences on both sides, a line
    holding a different inventory item, unexplained differences, catalog
    prices. ``--decisions`` takes ``decisions_needed.csv`` back with
    ``apply``/``skip`` filled in, for a later run.

What it holds for a person although it is certain (see :func:`find_holds`):
    a Vision project that reads as test data or holds nothing, with its
    records; a credit-only work order (a credit memo, not a change order).
    A decisions-file ``apply`` releases a held row.

Every change, and every difference left for a person, is written to
``vision_sync_changes`` under one ``vision_sync_runs`` row, in the same
transaction as the changes (a dry run's log is rolled back with it and kept in
report.json). Every field write first checks Velocity still holds the value
the compare saw; if not, the row is skipped as "changed since the compare".
"""
from __future__ import annotations

import argparse
import csv
import getpass
import glob
import hashlib
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime
from types import SimpleNamespace
from typing import Callable, Optional
from urllib.parse import urlparse

# Outcomes of a row
APPLIED, SKIPPED, FAILED, INFO = "applied", "skipped", "failed", "info"
NEEDS = "needs a decision"                                 # reason suffix: a conflict, Velocity kept
HELD = "held for a decision"                               # reason prefix: certain to apply, but a person says if
# A project name that reads as test data or a placeholder (checked on Vision projects Velocity lacks)
TEST_NAME = re.compile(r"\btest\b|do not use|dummy|\bxxx", re.IGNORECASE)

# Vision table each kind of record came from (stamped as legacy_source)
SOURCE = {
    "customer": "tblClients", "vendor": "tblVendors", "parts": "tblMaterial", "labour": "tblApplication",
    "misc": "tblZones", "projects": "tblProjects", "quotes": "tblServiceRecords",
    "pos": "tblPurchaseOrders", "po lines": "tblPurchaseOrdersMaterial",
}
QUOTE_LINE_SOURCE = {"labor": "tblWorkorderApplication", "part": "tblWorkorderMaterial", "misc": "tblWorkorderZones"}

# Velocity table behind each compare level (for the log and the verify step)
TABLES = {
    "categories": "categories", "customers and vendors": "profiles", "contacts": "contacts", "parts": "parts",
    "labour": "labor", "misc": "miscellaneous", "projects": "projects", "quotes": "quotes",
    "quote lines": "quote_line_items", "pos": "purchase_orders", "po lines": "po_line_items",
}
# Tables counted before and after a run (nothing may shrink)
COUNT_TABLES = ["profiles", "parts", "labor", "miscellaneous", "projects", "quotes", "quote_line_items",
                "quote_snapshots", "purchase_orders", "po_line_items", "po_snapshots", "invoices"]
SCHEMA = "034_vision_cutover"                              # the log tables and Vision-number columns

# Who the snapshots name as the actor
ACTOR = {"user_id": "vision-delta-tool", "email": "Vision cutover"}   # velocity.TOOL_ACTOR must match
SYNC = "vision_sync"                                       # snapshot action type; not an item-list action


# --------------------------------------------------------------------------- #
# Decisions (optional: a person's apply/skip for a later run)
# --------------------------------------------------------------------------- #

DECISION_COLUMNS = ["level", "vision_key", "velocity_key", "field", "decision", "kind", "project", "context",
                    "vision_value", "velocity_value", "cause", "reason"]


def _k(v) -> str:
    """A key part as text ("" for None), so CSV values and row values compare."""
    return "" if v is None else str(v)


def vision_closed(v: dict) -> Optional[bool]:
    """``Quote.vision_closed`` for a Vision work order: True when Vision shows it Closed, else None.

    Vision's status is Closed when the job was finished there, or when only
    credits were processed and nothing is left (see vision.quote_status).
    """
    return True if v.get("status") == "Closed" else None


def load_decisions(path: Optional[str]) -> dict[tuple, str]:
    """Read a decisions file (``decisions_needed.csv`` with its decision column filled).

    Args:
        path: The CSV path, or None.

    Returns:
        ``(level, vision_key, velocity_key, field) -> "apply" | "skip"``; a
        blank key part matches any value (so one line decides a whole record).

    Raises:
        ValueError: A decision other than apply / skip / blank.
    """
    out = {}
    if not path:
        return out
    with open(path, newline="", encoding="utf-8-sig") as f:   # Excel saves with a BOM
        for r in csv.DictReader(f):
            d = (r.get("decision") or "").strip().lower()
            if not d:
                continue                                   # undecided: the default rule stays
            if d not in ("apply", "skip"):
                raise ValueError(f"decision must be apply or skip, got {d!r} ({r})")
            out[(r["level"], _k(r.get("vision_key")), _k(r.get("velocity_key")), _k(r.get("field")))] = d
    return out


def decided(decisions: dict, row: dict) -> Optional[str]:
    """The person's decision for a row, most specific match first."""
    lv, vk, tk, f = row["level"], _k(row["vision_key"]), _k(row["velocity_key"]), _k(row["field"])
    for key in ((lv, vk, tk, f), (lv, vk, tk, ""), (lv, vk, "", ""), (lv, "", tk, "")):
        if key in decisions:
            return decisions[key]
    return None


# --------------------------------------------------------------------------- #
# The rule: what happens to one row by default
# --------------------------------------------------------------------------- #

def rule(row: dict) -> tuple[str, str]:
    """Default action for a compare row.

    Args:
        row: A :class:`scripts.vision_delta.compare.Delta` row.

    Returns:
        ``(APPLIED | SKIPPED | INFO, reason)``; APPLIED means "apply".
    """
    from .compare import BOTH, COPY, DIFFERS, EXTRA, JUNE_ROUNDING, JUNE_RULE, MISSING, RENUMBERED, VELOCITY_EDIT, VISION_LATER
    level, kind, field, why = row["level"], row["kind"], row["field"], row["cause"] or ""
    if kind in (EXTRA, COPY):
        return SKIPPED, "only in velocity: never touched"
    if kind == MISSING and level not in ("quote lines", "po lines"):
        return APPLIED, "missing in velocity"              # Vision has it: it is added
    if level == "projects" and field == "uca":
        return INFO, f"{RENUMBERED}: velocity number kept, vision number recorded"
    if kind == DIFFERS and field == "item":
        return SKIPPED, f"different inventory item: velocity kept, {NEEDS}"
    if level == "quotes" and field == "uca":
        return SKIPPED, f"quote sits in another project in velocity: velocity kept, {NEEDS}"
    if level == "quotes" and field == "status":
        return INFO, "status follows the line fixes"       # Velocity derives quote status from its lines
    if why in (JUNE_ROUNDING, JUNE_RULE, VISION_LATER):
        return APPLIED, why
    if why == VELOCITY_EDIT:
        return SKIPPED, "edited in velocity since june: velocity kept"
    if why == BOTH:
        return SKIPPED, f"changed on both sides: velocity kept, {NEEDS}"
    return SKIPPED, f"unexplained: velocity kept, {NEEDS}"


# --------------------------------------------------------------------------- #
# Holds: rows the rule would apply that wait for a person instead
# --------------------------------------------------------------------------- #

def find_holds(v: dict, delta) -> dict:
    """What the cutover holds back for the decisions page instead of applying.

    * a Vision project Velocity lacks whose name reads as test data, or that
      holds no quotes and no POs in Vision -- with every quote and PO under it;
    * a credit-only work order (credit lines, no positive line): a credit memo,
      not the change-order use (credits beside the new charges) Velocity keeps.
      Its line fixes wait; a missing one is not added.

    Args:
        v: The Vision data the compare read (quotes and POs relabelled).
        delta: The compare's results.

    Returns:
        ``{"projects": {Vision number: why}, "credit_only": {WO}, "wo_of": {Velocity quote id: WO}}``.
    """
    from .compare import MISSING
    used = {r.get("vision_uca", r["uca"]) for r in v["quotes"] + v["pos"]}   # Vision projects holding records
    projects = {}
    for row in delta.rows:
        if row["level"] == "projects" and row["kind"] == MISSING:
            p = row["_ref"]["vision"]
            if TEST_NAME.search(p["name"] or ""):
                projects[p["uca"]] = "project name reads as test data"
            elif p["uca"] not in used:
                projects[p["uca"]] = "project holds no quotes or POs in vision"
    credit_only = {q["wo"] for q in v["quotes"]
                   if any(l["quantity"] < 0 for l in q["lines"]) and not any(l["quantity"] > 0 for l in q["lines"])}
    return {"projects": projects, "credit_only": credit_only,
            "wo_of": {t["id"]: vq["wo"] for vq, t in delta.quote_pairs}}


def held(row: dict, holds: Optional[dict]) -> Optional[str]:
    """Why a row the rule would apply is held for a person, or None."""
    from .compare import MISSING
    if not holds:
        return None
    lv, kind, ref = row["level"], row["kind"], row.get("_ref") or {}
    if kind == MISSING and lv in ("projects", "quotes", "pos"):
        rec = ref.get("vision") or {}
        why = holds["projects"].get(rec.get("vision_uca", rec.get("uca")))
        if why:
            return why if lv == "projects" else f"its project is held ({why})"
    if lv == "quotes" and kind == MISSING and ref.get("vision", {}).get("wo") in holds["credit_only"]:
        return "credit-only work order"
    if lv == "quote lines" and holds["wo_of"].get(ref.get("parent")) in holds["credit_only"]:
        return "line of a credit-only work order"
    return None


# --------------------------------------------------------------------------- #
# Writer
# --------------------------------------------------------------------------- #

class Skip(Exception):
    """Raised inside a record's write to skip it (with the reason) instead of failing."""


class Writer:
    """Writes rows into one Velocity session, keeping the lookups inserts need.

    Args:
        db: An open SQLAlchemy session (one transaction for the whole run).
        delta: The compare's results (rows carry ``_ref``; ``links`` and
            ``project_map`` are set).
    """

    def __init__(self, db, delta) -> None:
        from models import Category, Labor, Miscellaneous, Part, Profile, Project
        from .compare import norm
        self.db, self.delta, self.norm = db, delta, norm
        # Natural keys -> Velocity ids, as the compare pairs them
        self.profiles = {(p.type.value, norm(p.name)): p.id for p in db.query(Profile)}
        self.categories = {(c.type, norm(c.name)): c.id for c in db.query(Category)}
        self.items = {
            "part": {norm(p.part_number): p.id for p in db.query(Part)},
            "labor": {norm(l.description): l.id for l in db.query(Labor)},
            "misc": {norm(m.description): m.id for m in db.query(Miscellaneous) if not m.is_system_item},
        }
        self.part_numbers = {p.part_number for p in db.query(Part.part_number)}   # unique column, exact text
        self.projects = {p.uca_project_number: p.id for p in db.query(Project)}  # Velocity number -> id
        self.new_projects: dict[str, int] = {}             # Vision number -> id of a project inserted this run
        self.renumbered: set[int] = set()                  # ids of inserted projects that got a new number
        self.new_items: dict[tuple[str, int], int] = {}    # (kind, Vision id) -> id of an item inserted this run
        self.labels: dict[tuple[str, int], str] = {}       # (level, new id) -> number shown in Velocity
        self.closed_of = {t["id"]: vision_closed(v) for v, t in delta.quote_pairs}   # Velocity quote -> Vision finished it
        self.wo_of = {t["id"]: v["wo"] for v, t in delta.quote_pairs}                # Velocity quote -> Vision WO
        self.updated_quotes: set[int] = set()              # quotes update_quote changed (their status is set there)
        self.extra: list[dict] = []                         # report rows with no compare row of their own

    # ---- lookups ---------------------------------------------------------- #

    def item_id(self, kind: str, line: dict) -> Optional[int]:
        """Velocity inventory id for a Vision line's item: linked, inserted this run, else by name."""
        vid = line.get("item_vid")
        found = self.delta.links.get((kind, vid)) or self.new_items.get((kind, vid))
        return found or self.items[kind].get(self.norm(line.get("ref")))

    def profile_id(self, kind: str, name: str) -> int:
        """Velocity customer/vendor id by name; skips the record when there is none.

        Vision records pointing at a customer/vendor Vision does not have carry
        the import's placeholder name; that placeholder is created on first use,
        as the import creates it.
        """
        from models import Profile, ProfileType
        from .vision import UNKNOWN_CUSTOMER, UNKNOWN_VENDOR
        pid = self.profiles.get((kind, self.norm(name)))
        if pid is None and name in (UNKNOWN_CUSTOMER, UNKNOWN_VENDOR):
            p = Profile(name=name, type=ProfileType(kind), pst="", address="", postal_code="")   # the import's placeholder
            self.db.add(p)
            self.db.flush()
            pid = self.profiles[(kind, self.norm(name))] = p.id
            self.added("customers and vendors", None, p.id, f"{kind} {name}", "placeholder for records naming no " + kind)
        if pid is None:
            raise Skip(f"{kind} {name!r} not in velocity")
        return pid

    def project_id(self, uca: str) -> int:
        """Velocity project id for a project label from the compare.

        ``uca`` is a Velocity number, or ``"vision <number>"`` for a Vision
        project inserted this run under a new number. Skips the record when
        the project is not there (its insert failed or was skipped).
        """
        if uca.startswith("vision "):
            pid = self.new_projects.get(uca[len("vision "):])
        else:
            pid = self.projects.get(uca)                   # a Velocity number (a kept-number insert is in it)
            if pid in self.renumbered:                     # that number went to a different Vision project
                pid = None
        if pid is None:
            raise Skip(f"project {uca} not in velocity")
        return pid

    def added(self, level: str, vision_key, new_id: int, context: str, reason: str, project: str = "",
              value=None) -> None:
        """Report a record added inside another insert (a line, a placeholder)."""
        self.extra.append({"level": level, "kind": "missing in velocity", "project": project, "vision_key": vision_key,
                           "velocity_key": None, "context": context, "field": "", "vision_value": value,
                           "velocity_value": None, "cause": "", "outcome": APPLIED, "reason": reason,
                           "record_id": new_id})

    def note(self, level: str, context: str, reason: str, project: str = "", vision_key=None) -> None:
        """Report a line skipped inside a record being inserted (it has no compare row of its own)."""
        self.extra.append({"level": level, "kind": "missing in velocity", "project": project, "vision_key": vision_key,
                           "velocity_key": None, "context": context, "field": "", "vision_value": None,
                           "velocity_value": None, "cause": "", "outcome": SKIPPED, "reason": reason})

    def status_change(self, level: str, vision_key, record_id: int, context: str, before, after, project: str) -> None:
        """Report a status the app derived after the tool changed a record's lines."""
        if before == after:
            return
        self.extra.append({"level": level, "kind": "differs", "project": project, "vision_key": vision_key,
                           "velocity_key": record_id, "context": context, "field": "status", "vision_value": after,
                           "velocity_value": before, "cause": "", "outcome": APPLIED,
                           "reason": "status follows the lines", "record_id": record_id})

    # ---- reference records ------------------------------------------------ #

    def insert_profile(self, v: dict) -> int:
        """Insert a Vision customer/vendor with its contacts and phones."""
        from models import Contact, ContactPhone, PhoneType, Profile, ProfileType
        if (v["type"], self.norm(v["name"])) in self.profiles:
            raise Skip("a profile with that name is already in velocity")
        p = Profile(name=v["name"], type=ProfileType(v["type"]), pst=v["pst"], address=v["address"],
                    postal_code=v["postal_code"], legacy_source=SOURCE[v["type"]], legacy_id=v["vid"])
        for c in v["contacts"]:                            # contacts and their phones ride on the profile
            p.contacts.append(Contact(name=c["name"], job_title=c["job_title"], email=c["email"],
                                      phone_numbers=[ContactPhone(type=PhoneType(t), number=n) for t, n in c["phones"]]))
        self.db.add(p)
        self.db.flush()                                    # id for the lookups
        self.profiles[(v["type"], self.norm(v["name"]))] = p.id
        self.labels[("customers and vendors", p.id)] = p.name
        return p.id

    def insert_part(self, v: dict) -> int:
        """Insert a Vision part (the June import's mapping)."""
        from models import Part
        if v["part_number"] in self.part_numbers:
            raise Skip("part number already in velocity")    # unique column: would abort the run
        part = Part(part_number=v["part_number"], description=v["description"], cost=v["cost"],
                    markup_percent=v["markup_percent"], category_id=self.categories.get(("part", self.norm(v["category"]))),
                    vendor_id=self.profiles.get(("vendor", self.norm(v["vendor"]))),
                    legacy_source=SOURCE["parts"], legacy_id=v["vid"])
        self.db.add(part)
        self.db.flush()
        self.part_numbers.add(part.part_number)
        self.new_items[("part", v["vid"])] = self.items["part"][self.norm(part.part_number)] = part.id
        self.labels[("parts", part.id)] = part.part_number
        return part.id

    def insert_labour(self, v: dict) -> int:
        """Insert a Vision labour item."""
        from models import Labor
        item = Labor(description=v["description"], hours=v["hours"], rate=v["rate"], markup_percent=v["markup_percent"],
                     category_id=self.categories.get(("labor", self.norm(v["category"]))),
                     legacy_source=SOURCE["labour"], legacy_id=v["vid"])
        self.db.add(item)
        self.db.flush()
        self.new_items[("labor", v["vid"])] = self.items["labor"][self.norm(item.description)] = item.id
        self.labels[("labour", item.id)] = item.description
        return item.id

    def insert_misc(self, v: dict) -> int:
        """Insert a Vision misc (zone) item."""
        from models import Miscellaneous
        item = Miscellaneous(description=v["description"], unit_price=v["unit_price"], markup_percent=v["markup_percent"],
                             legacy_source=SOURCE["misc"], legacy_id=v["vid"])
        self.db.add(item)
        self.db.flush()
        self.new_items[("misc", v["vid"])] = self.items["misc"][self.norm(item.description)] = item.id
        self.labels[("misc", item.id)] = item.description
        return item.id

    def insert_project(self, v: dict) -> int:
        """Insert a Vision project under its customer.

        It keeps its Vision number unless Velocity uses that number for
        another project; then it gets the next free number, as the app numbers
        new projects. The Vision number is recorded on it either way.

        The compare already chose which: its records are labelled with the
        Vision number (kept) or ``"vision <number>"`` (renumbered). A kept
        number that Velocity took after the compare skips the project, since
        its records would otherwise land in the project now holding it (a
        plan applied in batches meets this; one run in one transaction does not).

        Raises:
            Skip: The kept number was taken after the compare, or the customer is missing.
        """
        from models import Project
        from routes.projects import generate_next_uca_number
        label = self.delta.project_map.get(v["uca"], "")   # how the compare labelled its records
        if v["uca"] not in self.projects:
            number = v["uca"]                              # still free: keep it
        elif label.startswith("vision "):
            number = generate_next_uca_number(self.db)     # records follow the "vision" label: safe to renumber
        else:
            raise Skip(f"number {v['uca']} was taken in velocity after the compare")
        p = Project(name=v["name"], customer_id=self.profile_id("customer", v["customer"]),
                    created_on=v["created_on"] or datetime.utcnow(), status=v["status"], ucsh_project_number=v["ucsh"],
                    uca_project_number=number, project_lead=v["lead"], vision_project_number=v["uca"],
                    legacy_source=SOURCE["projects"], legacy_id=v["vid"])
        self.db.add(p)
        self.db.flush()
        self.projects[number] = self.new_projects[v["uca"]] = p.id
        if number != v["uca"]:
            self.renumbered.add(p.id)
        self.labels[("projects", p.id)] = number
        return p.id

    # ---- quotes ----------------------------------------------------------- #

    @staticmethod
    def line_problem(item_type: str, quantity: float, fulfilled: float, pending: float) -> Optional[str]:
        """Why a quote line's values cannot be stored, or None when they can.

        Quantities are stored as Vision has them: credits (negative), zeros
        and fractions, parts included. Every line must add up: fulfilled +
        pending = quantity. On a positive or zero line neither is below zero;
        on a credit neither is above zero (the credit is invoiced toward zero,
        the way the app invoices it).

        Args:
            item_type: "part", "labor" or "misc" (kept for the callers; no rule uses it).
            quantity, fulfilled, pending: The values to store.

        Returns:
            A reason, or None.
        """
        if quantity < 0:                                   # a credit: both parts on the credit's side of zero
            if fulfilled > 0 or pending > 0 or round(fulfilled + pending, 2) != round(quantity, 2):
                return "a credit's fulfilled and pending must add up to it, neither above zero"
            return None
        if fulfilled < 0 or pending < 0 or round(fulfilled + pending, 2) != round(quantity, 2):
            return "fulfilled and pending do not add up to the quantity"
        return None

    def new_quote_line(self, quote_id: int, vl: dict):
        """A QuoteLineItem for a Vision line, or raises Skip with the reason."""
        from models import QuoteLineItem
        from routes.quotes import round_quantity
        kind = vl["item_type"]
        qty, done, left = (round_quantity(vl[k]) for k in ("quantity", "qty_fulfilled", "qty_pending"))
        problem = self.line_problem(kind, qty, done, left)
        if problem:
            raise Skip(problem)
        item = self.item_id(kind, vl)
        return QuoteLineItem(
            quote_id=quote_id, item_type=kind,
            part_id=item if kind == "part" else None, labor_id=item if kind == "labor" else None,
            misc_id=item if kind == "misc" else None,
            description=vl["description"], quantity=qty, unit_price=vl["unit_price"], base_cost=vl["base_cost"],
            qty_fulfilled=done, qty_pending=left,
            legacy_source=QUOTE_LINE_SOURCE[kind], legacy_id=vl["vid"])

    def insert_quote(self, v: dict) -> int:
        """Insert a Vision work order as a new quote with its lines and one snapshot."""
        from models import Project, Quote
        from routes.quotes import compute_status_from_lines, create_snapshot, get_next_quote_sequence
        project_id = self.project_id(v["uca"])
        project = self.db.query(Project).filter(Project.id == project_id).with_for_update().first()   # as the app numbers
        q = Quote(project_id=project_id, quote_sequence=get_next_quote_sequence(self.db, project_id),
                  created_at=v["created_at"] or datetime.utcnow(), work_description=v["work_description"],
                  client_po_number=v["client_po_number"], current_version=0, legacy_imported=True,
                  vision_wo_number=v["wo"], vision_closed=vision_closed(v),
                  legacy_source=SOURCE["quotes"], legacy_id=v["wo"])
        self.db.add(q)
        self.db.flush()
        number = f"{project.uca_project_number}-{q.quote_sequence:04d}-0"
        lines = []
        for vl in v["lines"]:
            ctx = f"{number} (WO {v['wo']}) {vl['item_type']} {vl['ref'] or ''}"
            try:
                li = self.new_quote_line(q.id, vl)
            except Skip as e:                              # one bad line does not stop the quote
                self.note("quote lines", ctx, str(e), v["uca"], vl["vid"])
                continue
            self.db.add(li)
            self.db.flush()                                # its id, for the log
            lines.append(li)
            self.added("quote lines", vl["vid"], li.id, ctx, "added with its work order", v["uca"], li.quantity)
        q.status = compute_status_from_lines(lines, q.client_po_number, q.vision_closed)   # the app's rule
        self.db.flush()
        create_snapshot(self.db, q, SYNC, f"Added from Vision work order {v['wo']}")
        self.labels[("quotes", q.id)] = number
        return q.id

    def update_quote(self, quote_id: int, rows: list[dict]) -> list[tuple]:
        """Apply one paired quote's header and line rows, then snapshot it once.

        Args:
            quote_id: The Velocity quote.
            rows: Its rows to apply (quote fields, line fields, missing lines).

        Returns:
            ``(row, outcome, reason[, record id, value written])`` per row.
        """
        from models import Quote, QuoteLineItem
        from routes.quotes import compute_status_from_lines, create_snapshot, round_quantity
        from .compare import DAY, MISSING, QUOTE_LINE_FIELDS, TEXT
        q = self.db.query(Quote).filter(Quote.id == quote_id).with_for_update().one()
        lines = {li.id: li for li in self.db.query(QuoteLineItem).filter(QuoteLineItem.quote_id == quote_id)}
        status_before = q.status
        q.vision_closed = q.vision_closed or self.closed_of.get(quote_id)   # stamped in bulk later; needed for the status now
        out, changes = [], Counter()

        # Header fields
        header = {"created_at": DAY, "work_description": TEXT, "client_po_number": TEXT}
        for r in (r for r in rows if r["level"] == "quotes"):
            f = r["field"]
            if f not in header:
                out.append((r, SKIPPED, f"field {f} not handled")); continue
            if header[f](getattr(q, f)) != header[f](r["velocity_value"]):
                out.append((r, SKIPPED, "changed since the compare")); continue
            setattr(q, f, r["vision_value"])
            changes[f] += 1
            out.append((r, APPLIED, "", quote_id, r["vision_value"]))

        # Paired lines: overlay Vision's values on the line, then check the result adds up
        by_line = defaultdict(list)
        for r in rows:
            if r["level"] == "quote lines" and r["kind"] != MISSING:
                by_line[r["velocity_key"]].append(r)
        for line_id, lrows in by_line.items():
            li = lines.get(line_id)
            stale = [r for r in lrows if li is None or QUOTE_LINE_FIELDS[r["field"]](getattr(li, r["field"]))
                     != QUOTE_LINE_FIELDS[r["field"]](r["velocity_value"])]
            if stale:                                      # someone changed the line after the compare read it
                out += [(r, SKIPPED, "changed since the compare") for r in lrows]; continue
            target = {f: getattr(li, f) for f in ("quantity", "qty_fulfilled", "qty_pending")}
            for r in lrows:
                target[r["field"]] = r["vision_value"]
            touched = {r["field"] for r in lrows}
            if not touched & {"qty_fulfilled", "qty_pending"} and "quantity" in touched:
                target["qty_pending"] = target["quantity"] - target["qty_fulfilled"]   # what is left to do, credits too
            qty, done, left = (round_quantity(float(target[k] or 0)) for k in ("quantity", "qty_fulfilled", "qty_pending"))
            problem = self.line_problem(li.item_type, qty, done, left)
            if problem and "quantity" not in touched and round(done + left, 2) != round(qty, 2):
                problem = "waits for the decision on this line's quantity"   # its quantity row was not applied
            if problem:
                out += [(r, SKIPPED, problem) for r in lrows]; continue
            for r in lrows:
                if r["field"] not in ("quantity", "qty_fulfilled", "qty_pending"):
                    setattr(li, r["field"], r["vision_value"])   # description, unit price, base cost
            li.quantity, li.qty_fulfilled, li.qty_pending = qty, done, left
            changes["line"] += 1
            out += [(r, APPLIED, "", line_id, getattr(li, r["field"])) for r in lrows]   # the value actually stored

        # Lines Vision has and Velocity lacks
        for r in (r for r in rows if r["level"] == "quote lines" and r["kind"] == MISSING):
            try:
                li = self.new_quote_line(quote_id, r["_ref"]["vision"])
                self.db.add(li)
                self.db.flush()
                changes["added line"] += 1
                out.append((r, APPLIED, "", li.id, li.quantity))
            except Skip as e:
                out.append((r, SKIPPED, str(e)))

        if changes:                                        # one snapshot for everything above
            self.db.flush()
            fresh = self.db.query(QuoteLineItem).filter(QuoteLineItem.quote_id == quote_id).all()
            q.status = compute_status_from_lines(fresh, q.client_po_number, q.vision_closed)
            what = ", ".join(f"{n} {k}{'s' if n > 1 and k.endswith('line') else ''}" for k, n in changes.items())
            create_snapshot(self.db, q, SYNC, f"Updated from Vision: {what}")
            self.updated_quotes.add(quote_id)
            r0 = rows[0]
            ctx = r0["context"].split(" quote lines")[0] if r0["level"] == "quotes" else r0["context"].rsplit(") ", 1)[0] + ")"
            self.status_change("quotes", self.wo_of.get(quote_id), quote_id, ctx, status_before, q.status, r0["project"])
        return out

    # ---- purchase orders -------------------------------------------------- #

    @staticmethod
    def po_line_problem(quantity: int, received: int, pending: int) -> Optional[str]:
        """Why a PO line's values cannot be stored, or None when they can.

        Zero lines are stored as Vision has them. PO quantities are whole
        numbers in Velocity; Vision holds no negative PO lines, so a credit
        is not stored (its receiving rules are not defined).
        """
        if quantity < 0:
            return "negative PO quantity: receiving a credit is not defined in velocity"
        if received < 0 or pending < 0 or pending > quantity:
            return "received and pending do not fit the quantity"
        return None

    def new_po_line(self, po_id: int, vl: dict):
        """A POLineItem for a Vision line, or raises Skip with the reason."""
        from models import POLineItem
        problem = self.po_line_problem(vl["quantity"], vl["qty_received"], vl["qty_pending"])
        if problem:
            raise Skip(problem)
        part = self.delta.links.get(("part", vl.get("item_vid"))) or self.new_items.get(("part", vl.get("item_vid"))) \
            or self.items["part"].get(self.norm(vl["part_number"]))
        return POLineItem(purchase_order_id=po_id, item_type="part", part_id=part, description=vl["description"],
                          quantity=vl["quantity"], unit_price=vl["unit_price"], qty_received=vl["qty_received"],
                          qty_pending=vl["qty_pending"], legacy_source=SOURCE["po lines"], legacy_id=vl["vid"])

    def insert_po(self, v: dict) -> int:
        """Insert a Vision PO with its lines and the app's "create" snapshot."""
        from models import POStatus, Project, PurchaseOrder
        from routes.purchase_orders import create_po_snapshot, get_next_po_sequence
        project_id = self.project_id(v["uca"])
        project = self.db.query(Project).filter(Project.id == project_id).with_for_update().first()
        po = PurchaseOrder(project_id=project_id, vendor_id=self.profile_id("vendor", v["vendor"]),
                           po_sequence=get_next_po_sequence(self.db, project_id),
                           created_at=v["created_at"] or datetime.utcnow(), status=POStatus(v["status"]),
                           work_description=v["work_description"], current_version=0, legacy_imported=True,
                           vision_po_number=v["number"], legacy_source=SOURCE["pos"], legacy_id=v["vid"])
        self.db.add(po)
        self.db.flush()
        number = f"PO-{project.uca_project_number}-{po.po_sequence:04d}-0"
        for vl in v["lines"]:
            ctx = f"{number} (vision PO {v['number'] or v['vid']}) {vl['part_number'] or ''}"
            try:
                li = self.new_po_line(po.id, vl)
            except Skip as e:
                self.note("po lines", ctx, str(e), v["uca"], vl["vid"])
                continue
            self.db.add(li)
            self.db.flush()
            self.added("po lines", vl["vid"], li.id, ctx, "added with its PO", v["uca"], li.quantity)
        self.db.flush()
        create_po_snapshot(self.db, po, "create", f"Added from Vision PO {v['number'] or v['vid']}")   # keeps version 0
        self.labels[("pos", po.id)] = number
        return po.id

    def update_po(self, po_id: int, rows: list[dict]) -> list[tuple]:
        """Apply one paired PO's rows; snapshot only when its lines change.

        Args:
            po_id: The Velocity PO.
            rows: Its rows to apply.

        Returns:
            ``(row, outcome, reason[, record id, value written])`` per row.
        """
        from models import POLineItem, POStatus, PurchaseOrder
        from routes.purchase_orders import create_po_snapshot
        from .compare import DAY, MISSING, NAME, PO_LINE_FIELDS, TEXT
        po = self.db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).with_for_update().one()
        lines = {li.id: li for li in self.db.query(POLineItem).filter(POLineItem.purchase_order_id == po_id)}
        out, line_changes = [], 0

        for r in (r for r in rows if r["level"] == "pos"):
            f = r["field"]
            now = {"status": po.status.value if po.status else None, "created_at": po.created_at,
                   "work_description": po.work_description, "vendor": po.vendor.name if po.vendor else None}.get(f)
            cmp = {"status": TEXT, "created_at": DAY, "work_description": TEXT, "vendor": NAME}.get(f)
            if cmp is None:
                out.append((r, SKIPPED, f"field {f} not handled")); continue
            if cmp(now) != cmp(r["velocity_value"]):
                out.append((r, SKIPPED, "changed since the compare")); continue
            try:
                if f == "status":
                    po.status = POStatus(r["vision_value"])
                elif f == "vendor":
                    po.vendor_id = self.profile_id("vendor", r["vision_value"])
                else:
                    setattr(po, f, r["vision_value"])
            except Skip as e:
                out.append((r, SKIPPED, str(e))); continue
            out.append((r, APPLIED, "", po_id, r["vision_value"]))

        by_line = defaultdict(list)
        for r in rows:
            if r["level"] == "po lines" and r["kind"] != MISSING:
                by_line[r["velocity_key"]].append(r)
        for line_id, lrows in by_line.items():
            li = lines.get(line_id)
            if any(li is None or PO_LINE_FIELDS[r["field"]](getattr(li, r["field"])) != PO_LINE_FIELDS[r["field"]](r["velocity_value"])
                   for r in lrows):
                out += [(r, SKIPPED, "changed since the compare") for r in lrows]; continue
            target = {f: getattr(li, f) for f in ("quantity", "qty_received", "qty_pending")}
            for r in lrows:
                target[r["field"]] = r["vision_value"]
            if "quantity" in target and not {r["field"] for r in lrows} & {"qty_received", "qty_pending"}:
                target["qty_pending"] = max(0, target["quantity"] - target["qty_received"])
            problem = self.po_line_problem(int(target["quantity"]), int(target["qty_received"]), int(target["qty_pending"]))
            if problem:
                out += [(r, SKIPPED, problem) for r in lrows]; continue
            for r in lrows:
                setattr(li, r["field"], r["vision_value"])
            li.qty_pending = int(target["qty_pending"])
            line_changes += 1
            out += [(r, APPLIED, "", line_id, getattr(li, r["field"])) for r in lrows]

        for r in (r for r in rows if r["level"] == "po lines" and r["kind"] == MISSING):
            try:
                li = self.new_po_line(po_id, r["_ref"]["vision"])
                self.db.add(li)
                self.db.flush()
                line_changes += 1
                out.append((r, APPLIED, "", li.id, li.quantity))
            except Skip as e:
                out.append((r, SKIPPED, str(e)))

        if line_changes:                                   # status-only fixes write no snapshot (number stays)
            self.db.flush()
            create_po_snapshot(self.db, po, SYNC, f"Updated from Vision: {line_changes} line(s)")
        return out

    # ---- Vision numbers on paired records --------------------------------- #

    def stamp_all(self) -> list[dict]:
        """Record the Vision number (and Vision id) on every paired quote, PO and project.

        Bulk UPDATE by primary key of what :func:`stamp_plan` finds missing.
        No snapshot: no line changes.

        Returns:
            One report row per field written.
        """
        from sqlalchemy import update
        updates, report = stamp_plan(self.delta, self.updated_quotes)
        for model, batch in updates:
            for cols in {tuple(sorted(row)) for row in batch}:   # executemany needs one shape per call
                same = [row for row in batch if tuple(sorted(row)) == cols]
                for i in range(0, len(same), 2000):
                    self.db.execute(update(model), same[i:i + 2000])
        return report

    # ---- reference fields a person chose to apply ------------------------- #

    REFERENCE_FIELDS = {                                   # compare field -> model attribute
        "parts": {"part_number": "part_number", "description": "description", "cost": "cost", "markup_percent": "markup_percent"},
        "labour": {"description": "description", "hours": "hours", "rate": "rate", "markup_percent": "markup_percent"},
        "misc": {"description": "description", "unit_price": "unit_price", "markup_percent": "markup_percent"},
        "projects": {"name": "name", "created_on": "created_on", "status": "status", "ucsh": "ucsh_project_number",
                     "lead": "project_lead"},
        "customers and vendors": {"pst": "pst", "address": "address", "postal_code": "postal_code"},
    }

    def update_reference(self, row: dict) -> None:
        """Set one field of a paired reference record (a June artifact, or a person decided apply)."""
        from models import Labor, Miscellaneous, Part, Profile, Project
        model = {"parts": Part, "labour": Labor, "misc": Miscellaneous, "projects": Project,
                 "customers and vendors": Profile}[row["level"]]
        attr = self.REFERENCE_FIELDS[row["level"]].get(row["field"])
        if attr is None:
            raise Skip(f"field {row['field']} not handled")
        if row["vision_value"] is None and attr == "created_on":
            raise Skip("vision has no date")               # never blank a created date
        rec = self.db.get(model, row["velocity_key"])
        if rec is None or _k(getattr(rec, attr)) != _k(row["velocity_value"]):
            raise Skip("changed since the compare")
        setattr(rec, attr, row["vision_value"])



def stamp_plan(delta, updated_quotes: frozenset | set = frozenset()) -> tuple[list[tuple], list[dict]]:
    """The Vision numbers still to record on paired quotes, POs and projects.

    Only values that are missing or different are planned, so after a run the
    plan is empty (verify.py checks that). ``legacy_id`` is set only where it
    is empty: it is the exact key later compares pair on, never re-keyed.
    A record that already carries its Vision id gets no Vision number either:
    the id leads back to the Vision record, so the number would only cost
    writes (about 50,000 on the production data).

    A quote Vision finished gets ``vision_closed``; unless the run already
    rewrote its lines (``updated_quotes``, whose status is set there), its
    stored status is set to what the app now shows, and reported.

    Args:
        delta: The compare's results (``quote_pairs``, ``po_pairs``, ``project_pairs``).
        updated_quotes: Velocity quotes the run changed through ``update_quote``.

    Returns:
        ``(updates, report)``: ``[(model, [{"id": .., column: value}, ..]), ..]``
        and one report row per field.
    """
    from models import Project, PurchaseOrder, Quote
    from routes.quotes import compute_status_from_lines
    plan = [  # (level, model, pairs, Vision key, Vision number column, its value, Vision id)
        ("quotes", Quote, delta.quote_pairs, lambda v: v["wo"], "vision_wo_number", lambda v: v["wo"], lambda v: v["wo"]),
        ("pos", PurchaseOrder, delta.po_pairs, lambda v: v["vid"], "vision_po_number", lambda v: v["number"],
         lambda v: v["vid"]),
        ("projects", Project, delta.project_pairs, lambda v: v["uca"], "vision_project_number", lambda v: v["uca"],
         lambda v: v["vid"]),
    ]
    updates, report = [], []
    for level, model, pairs, vkey, column, number, vid in plan:
        batch = []
        for v, t in pairs:
            values = {}
            keyed = t.get("legacy_id") is not None         # June import keyed it: Vision id already on it
            if not keyed and number(v) is not None and number(v) != t.get(column):
                values[column] = number(v)                 # the number Vision printed
            if not keyed and vid(v) is not None:
                values["legacy_id"], values["legacy_source"] = vid(v), SOURCE[level]   # already keyed: never re-keyed
            if level == "quotes" and vision_closed(v) and not t.get("vision_closed"):
                values["vision_closed"] = True             # Vision finished it: Closed once nothing is pending
            if not values:
                continue
            row = {"id": t["id"], **values}
            batch.append(row)
            ctx = t.get("quote_number") or t.get("po_number") or f"{t['uca']} {t.get('name') or ''}".strip()
            if "vision_closed" in values and t["id"] not in updated_quotes:   # the status the app now shows
                after = compute_status_from_lines([SimpleNamespace(**l) for l in t["lines"]], t["client_po_number"], True)
                if after != t["status"]:
                    row["status"] = after                  # stored too, so it matches what is shown
                    report.append({"level": "quotes", "kind": "differs", "project": t["uca"], "vision_key": vkey(v),
                                   "velocity_key": t["id"], "context": ctx, "field": "status", "vision_value": after,
                                   "velocity_value": t["status"], "cause": "", "outcome": APPLIED,
                                   "reason": "status follows the lines", "record_id": t["id"]})
            for col, value in values.items():
                if col != "legacy_source":
                    report.append({"level": level, "kind": "differs", "project": t["uca"], "vision_key": vkey(v),
                                   "velocity_key": t["id"], "context": ctx, "field": col, "vision_value": value,
                                   "velocity_value": t.get(col), "cause": "", "outcome": APPLIED,
                                   "reason": "vision number recorded", "record_id": t["id"]})
        if batch:
            updates.append((model, batch))
    return updates, report


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #

INSERT_ORDER = ["customers and vendors", "parts", "labour", "misc", "projects", "quotes", "pos"]


def report_row(row: dict, outcome: str, reason: str = "", record_id=None, written=None) -> dict:
    """A compare row as a report row: its ``_ref`` dropped, the outcome added."""
    return {**{k: v for k, v in row.items() if not k.startswith("_")}, "outcome": outcome, "reason": reason,
            "record_id": record_id, "written": written}


def decide(delta, decisions: dict, holds: Optional[dict] = None) -> tuple[list[dict], list[dict]]:
    """Run every compare row through the rule, the holds and the decisions.

    Args:
        delta: The compare's results.
        decisions: :func:`load_decisions` output.
        holds: :func:`find_holds` output; rows it names wait for a person
            (a decisions-file "apply" releases them).

    Returns:
        ``(to_apply, left)``: the compare rows to write (``_ref`` kept), and a
        report row for every row left as it is (skipped, held, a conflict, information).
    """
    to_apply, left = [], []
    for row in delta.rows:
        outcome, reason = rule(row)
        why = held(row, holds) if outcome == APPLIED else None
        if why:
            outcome, reason = SKIPPED, f"{HELD}: {why}"    # certain, but a person says whether
        choice = decided(decisions, row)
        if choice == "apply" and outcome == SKIPPED and not reason.startswith("only in velocity"):
            outcome, reason = APPLIED, "decided: apply"    # a person overrides the default skip
        elif choice == "skip" and outcome == APPLIED:
            outcome, reason = SKIPPED, "decided: skip"
        if outcome == APPLIED:
            to_apply.append(row)
        else:
            left.append(report_row(row, outcome, reason))
    return to_apply, left


def apply(db, delta, decisions: dict, holds: Optional[dict] = None,
          planned: bool = False) -> tuple[list[dict], "Writer"]:
    """Decide and write every row; returns the report rows and the writer.

    Each record is written inside its own savepoint, so a failure rolls back
    that record only and is reported as failed; the caller commits or rolls
    back the whole transaction.

    Args:
        db: An open session.
        delta: The compare's results.
        decisions: :func:`load_decisions` output.
        holds: :func:`find_holds` output; rows it names wait for a person
            (a decisions-file "apply" releases them).
        planned: True when ``delta.rows`` were decided already (one batch of a
            plan, see plan.py): every row is written, nothing is decided again.

    Returns:
        ``(report, writer)``: every compare row (plus rows the writer added:
        lines inside inserted records, derived statuses, Vision numbers) with
        ``outcome``, ``reason`` and, for written rows, ``record_id`` and
        ``written`` (the value actually stored); the writer holds the numbers
        given to inserted records.
    """
    from .compare import DIFFERS, MISSING
    w = Writer(db, delta)
    report: list[dict] = []

    def done(row, outcome, reason="", record_id=None, written=None):
        report.append(report_row(row, outcome, reason, record_id, written))

    def attempt(rows: list[dict], write: Callable[[], object]):
        """Write one record in a savepoint; record each row's outcome."""
        sp = db.begin_nested()
        try:
            result = write()
            sp.commit()
        except Skip as e:
            sp.rollback()
            result = [(r, SKIPPED, str(e)) for r in rows]
        except Exception as e:                             # noqa: BLE001 -- reported, the run goes on
            sp.rollback()
            result = [(r, FAILED, f"{type(e).__name__}: {e}"[:300]) for r in rows]
        if isinstance(result, int):                        # an insert: one row, the new record's id
            result = [(r, APPLIED, rule(r)[1], result, None) for r in rows]
        for item in result or [(r, APPLIED, "") for r in rows]:
            done(*item)

    # 1. Decide every row (a plan's batch was decided when the plan was made)
    if planned:
        to_apply = list(delta.rows)
    else:
        to_apply, left = decide(delta, decisions, holds)
        report += left

    # 2. Inserts, in dependency order
    inserts = {
        "customers and vendors": w.insert_profile, "parts": w.insert_part, "labour": w.insert_labour,
        "misc": w.insert_misc, "projects": w.insert_project, "quotes": w.insert_quote, "pos": w.insert_po,
    }
    for level in INSERT_ORDER:
        rows = [r for r in to_apply if r["level"] == level and r["kind"] == MISSING]
        if level == "projects":                            # projects keeping their number first, so a
            rows.sort(key=lambda r: bool(r["_ref"].get("new_number")))   # new number never takes one of theirs
        for row in rows:
            attempt([row], lambda row=row: inserts[level](row["_ref"]["vision"]))
    for row in [r for r in to_apply if r["kind"] == MISSING and r["level"] not in inserts
                and r["level"] not in ("quote lines", "po lines")]:
        done(row, SKIPPED, f"inserting {row['level']} is not handled")   # e.g. a contact of a paired vendor

    # 3. Reference field changes (June artifacts, or a person decided apply)
    for row in [r for r in to_apply if r["kind"] == DIFFERS and r["level"] in Writer.REFERENCE_FIELDS]:
        attempt([row], lambda row=row: (w.update_reference(row), [(row, APPLIED, "", row["velocity_key"],
                                                                    row["vision_value"])])[1])

    # 4. Paired quotes and POs, one record at a time
    groups = {"quote": defaultdict(list), "po": defaultdict(list)}
    for row in to_apply:
        lv = row["level"]
        if lv == "quotes" and row["kind"] == DIFFERS:
            groups["quote"][row["velocity_key"]].append(row)
        elif lv == "quote lines":
            groups["quote"][row["_ref"]["parent"]].append(row)
        elif lv == "pos" and row["kind"] == DIFFERS:
            groups["po"][row["velocity_key"]].append(row)
        elif lv == "po lines":
            groups["po"][row["_ref"]["parent"]].append(row)
    for qid, rows in groups["quote"].items():
        attempt(rows, lambda qid=qid, rows=rows: w.update_quote(qid, rows))
    for pid, rows in groups["po"].items():
        attempt(rows, lambda pid=pid, rows=rows: w.update_po(pid, rows))

    # 5. Vision numbers on everything paired
    sp = db.begin_nested()
    stamped = w.stamp_all()
    sp.commit()

    for r in report:                                       # inserted records: the number they got
        if r["outcome"] == APPLIED and r["kind"] == MISSING and r["record_id"] is not None and r["written"] is None:
            r["written"] = w.labels.get((r["level"], r["record_id"]))
    for r in w.extra + stamped:
        r.setdefault("written", r["vision_value"])
    return report + w.extra + stamped, w


# --------------------------------------------------------------------------- #
# The log
# --------------------------------------------------------------------------- #

def _txt(v) -> Optional[str]:
    """A value as log text (None stays None, dates in ISO form)."""
    if v is None:
        return None
    if isinstance(v, (datetime, date)):
        return v.isoformat(sep=" ") if isinstance(v, datetime) else v.isoformat()
    return str(v)


def action_of(r: dict) -> Optional[str]:
    """The log's word for a report row, or None for rows that are not logged (only-in-Velocity)."""
    if r["kind"] in ("velocity only", "velocity copy"):
        return None                                        # never touched: not a change, not a conflict
    if r["outcome"] == APPLIED:
        if r.get("reason") == "vision number recorded":
            return "stamped"
        return "added" if r["kind"] == "missing in velocity" else "updated"
    if r["outcome"] == FAILED:
        return "failed"
    if r["outcome"] == INFO:
        return "info"
    if (r.get("reason") or "").startswith(HELD):
        return "held"
    return "kept: conflict" if NEEDS in (r.get("reason") or "") else "skipped"


def log_rows(report: list[dict]) -> list[dict]:
    """Report rows as ``vision_sync_changes`` values (without ``run_id``)."""
    out = []
    for r in report:
        action = action_of(r)
        if action is None:
            continue
        added = action == "added"
        out.append({
            "level": r["level"], "action": action, "record_table": TABLES.get(r["level"]),
            "record_id": r.get("record_id") or r.get("velocity_key"), "vision_key": _k(r["vision_key"]) or None,
            "project": r.get("project") or None, "context": r.get("context") or None, "field": r.get("field") or None,
            "before": None if added else _txt(r.get("velocity_value")),
            "after": _txt(r.get("written") if action in ("added", "updated", "stamped") else r.get("vision_value")),
            "reason": " | ".join(x for x in (r.get("cause"), r.get("reason")) if x) or None,
        })
    return out


def fingerprint(csv_dir: str) -> dict[str, str]:
    """SHA-256 of each Vision CSV, proving later which export a run applied."""
    out = {}
    for path in sorted(glob.glob(os.path.join(csv_dir, "*.csv"))):
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        out[os.path.basename(path)] = h.hexdigest()
    return out


def table_counts(db) -> dict[str, int]:
    """Row counts of :data:`COUNT_TABLES` (table names are constants, never input)."""
    from sqlalchemy import text
    return {t: db.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() for t in COUNT_TABLES}


def check_schema(db) -> None:
    """Refuse to run before the cutover migration (its columns and log tables)."""
    from sqlalchemy import text
    version = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    if version != SCHEMA:
        raise SystemExit(f"the database is at {version}; run `alembic upgrade head` ({SCHEMA}) first")


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #

def write_report(report: list[dict], delta, v: dict, t: dict, meta: dict, out_dir: str, since: date) -> list[str]:
    """Write report.xlsx (for Dexter), decisions_needed.csv and report.json.

    Sheets: Summary; what changed (added, status changes, line corrections,
    other updates); what a person resolves (the compare's findings, the
    differences kept, credit and zero lines, skipped or failed); and the
    per-project sheet of what was applied.

    Args:
        report: :func:`apply` output.
        delta: The compare's results (its findings go in).
        v, t: The Vision and Velocity data the compare read (for the project review).
        meta: Run details for the Summary sheet.
        out_dir: Output folder.
        since: First creation day for the project review.

    Returns:
        The paths written.
    """
    import json
    from openpyxl import Workbook                          # only needed here
    from .compare import REVIEW_COLUMNS, Delta, project_review
    from .workbook import FINDINGS, _cell, write_findings
    os.makedirs(out_dir, exist_ok=True)
    wb = Workbook(write_only=True)
    act = {id(r): action_of(r) for r in report}

    ws = wb.create_sheet("Summary")
    for k, val in meta.items():
        ws.append([k, _cell(val)])
    ws.append([])
    ws.append(["level", "action", "rows"])
    for key, n in sorted(Counter((r["level"], act[id(r)]) for r in report if act[id(r)]).items()):
        ws.append(list(key) + [n])
    ws.append([])
    ws.append(["finding", "rows"])
    for name, attr, _ in FINDINGS:
        ws.append([name, len(getattr(delta, attr))])

    def sheet(name: str, cols: list[str], rows: list[dict]):
        s = wb.create_sheet(name[:31])
        s.freeze_panes = "A2"
        s.append(cols)
        for r in rows:
            s.append([_cell(r.get(c)) for c in cols])

    base = ["project", "level", "context", "vision_key", "record_id"]
    records = ("customers and vendors", "parts", "labour", "misc", "projects", "quotes", "pos")
    sheet("added", base + ["written", "reason"],
          [r for r in report if act[id(r)] == "added" and r["level"] in records])
    sheet("status changes", base + ["velocity_value", "written", "reason"],
          [{**r, "velocity_value": r["velocity_value"]} for r in report
           if act[id(r)] == "updated" and r["field"] == "status"])
    sheet("line corrections", base + ["field", "velocity_value", "written", "cause"],
          [r for r in report if act[id(r)] in ("updated", "added") and r["level"] in ("quote lines", "po lines")])
    sheet("other updates", base + ["field", "velocity_value", "written", "cause"],
          [r for r in report if act[id(r)] == "updated" and r["level"] not in ("quote lines", "po lines")
           and r["field"] != "status"])

    write_findings(wb, delta)
    sheet("held for a decision", base + ["field", "vision_value", "velocity_value", "reason", "decision"],
          [{**r, "decision": None} for r in report if act[id(r)] == "held"])
    sheet("differences kept", base + ["field", "vision_value", "velocity_value", "cause", "reason", "decision"],
          [{**r, "decision": None} for r in report if act[id(r)] == "kept: conflict"])
    credit = lambda r: isinstance(r.get("written"), (int, float)) and r["written"] <= 0
    sheet("credit and zero lines", base + ["field", "velocity_value", "written", "reason"],
          [r for r in report if r["level"] == "quote lines" and act[id(r)] in ("updated", "added")
           and r["field"] in ("", "quantity") and credit(r)])
    sheet("skipped or failed", base + ["field", "vision_value", "velocity_value", "cause", "reason"],
          [r for r in report if act[id(r)] in ("skipped", "failed")])

    applied = Delta()                                      # per project: what was APPLIED
    applied.rows = [r for r in report if r["outcome"] == APPLIED and r.get("reason") != "vision number recorded"]
    applied.project_map = delta.project_map
    ws = wb.create_sheet(f"applied since {since}"[:31])
    ws.freeze_panes = "B2"
    ws.append([c.replace("missing in velocity", "added from vision").replace("with a different status", "with status corrected")
               .replace("with other header differences", "with header fields updated").replace("that differ", "updated")
               .replace("project fields that differ", "project fields updated").replace("missing work orders", "added work orders")
               .replace("missing vision POs", "added vision POs") for c in REVIEW_COLUMNS])
    for r in project_review(applied, v, t, since):
        ws.append([_cell(r[c]) for c in REVIEW_COLUMNS])
    xlsx = os.path.join(out_dir, "report.xlsx")
    wb.save(xlsx)

    dec = os.path.join(out_dir, "decisions_needed.csv")    # fill "decision" with apply / skip, pass back with --decisions
    with open(dec, "w", newline="", encoding="utf-8-sig") as f:
        wr = csv.DictWriter(f, DECISION_COLUMNS, extrasaction="ignore")
        wr.writeheader()
        for r in report:
            if NEEDS in (r["reason"] or "") or (r["reason"] or "").startswith(HELD):
                wr.writerow({**r, "decision": ""})
    js = os.path.join(out_dir, "report.json")
    with open(js, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "rows": report, "log": log_rows(report)}, f, default=str, indent=1)
    return [xlsx, dec, js]


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, compare, apply (dry run unless --commit), log, write the report.

    Args:
        argv: Command-line arguments (defaults to ``sys.argv``).

    Returns:
        Process exit code (1 when any record failed).
    """
    ap = argparse.ArgumentParser(prog="python -m scripts.vision_delta.apply", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True, help="folder holding the Vision CSV export (tblX.csv)")
    ap.add_argument("--target", default=os.getenv("VISION_DELTA_TARGET_URL"), help="Velocity Postgres URL")
    ap.add_argument("--out", required=True, help="output folder for the report")
    ap.add_argument("--decisions", help="decisions_needed.csv with its decision column filled")
    ap.add_argument("--commit", action="store_true", help="commit the writes (default: dry run, rolled back)")
    ap.add_argument("--june-cutoff", type=date.fromisoformat, help="day of the June import (default: estimated)")
    ap.add_argument("--projects-since", type=date.fromisoformat, default=date(2025, 1, 1),
                    help="first creation day for the per-project sheet (default: 2025-01-01)")
    args = ap.parse_args(argv)
    if not args.target:
        ap.error("--target (or VISION_DELTA_TARGET_URL) is required")

    os.environ.setdefault("DATABASE_URL", args.target)   # app modules read it at import
    from sqlalchemy import create_engine, insert
    from sqlalchemy.orm import Session
    from auth import current_actor
    from models import VisionSyncChange, VisionSyncRun
    from . import compare, velocity, vision

    started = time.time()
    host = urlparse(args.target).hostname
    if host not in ("localhost", "127.0.0.1", "::1"):      # one big transaction crashed prod on 2026-10-29
        ap.error(f"--target must be a local copy (got host {host}); production is applied in batches: "
                 "scripts.vision_delta.plan, then scripts.vision_delta.push")
    mode = "commit" if args.commit else "dry run"
    print(f"vision export: {args.csv}\nvelocity:      {host}\nmode:          "
          f"{'COMMIT' if args.commit else 'dry run (rolled back)'}")
    decisions = load_decisions(args.decisions)
    prints = fingerprint(args.csv)
    v = vision.load(args.csv)
    conn = velocity.connect(args.target)                   # the compare reads on its own read-only connection
    try:
        t = velocity.load(conn)
    finally:
        conn.close()
    cutoff = args.june_cutoff or t["recorded_cutoff"] or compare.june_cutoff(t["quotes"], t["first_snapshot"])
    source = "given" if args.june_cutoff else "recorded by an earlier run" if t["recorded_cutoff"] else "estimated"
    if cutoff is None:
        print("no imported quotes found; pass --june-cutoff", file=sys.stderr)
        return 2
    delta = compare.run(v, t, cutoff)
    print(f"compared:      {len(delta.rows)} difference rows (june import {cutoff}, {source})")

    token = current_actor.set(ACTOR)                       # snapshots name the tool as the actor
    engine = create_engine(args.target)
    try:
        with Session(engine) as db:
            check_schema(db)
            run = VisionSyncRun(started_at=datetime.utcnow(), mode=mode, actor=getpass.getuser(),
                                vision_export=os.path.abspath(args.csv), export_fingerprint=prints,
                                june_cutoff=str(cutoff), counts_before=table_counts(db))
            db.add(run)
            db.flush()                                     # its id, for the change rows
            report, _ = apply(db, delta, decisions, find_holds(v, delta))
            entries = log_rows(report)
            for i in range(0, len(entries), 5000):         # the log, in the same transaction as the changes
                db.execute(insert(VisionSyncChange), [{**e, "run_id": run.id} for e in entries[i:i + 5000]])
            run.counts_after = table_counts(db)
            run.totals = dict(Counter(e["action"] for e in entries))
            run.finished_at = datetime.utcnow()
            run_id = run.id
            if args.commit:
                db.commit()
            else:
                db.rollback()                              # dry run: nothing stays, the log included
    finally:
        current_actor.reset(token)
        engine.dispose()

    counts = Counter(e["action"] for e in entries)
    meta = {"mode": mode, "run id": run_id if args.commit else f"{run_id} (rolled back)",
            "vision export": os.path.abspath(args.csv), "velocity host": host, "june import day": cutoff,
            "decisions file": args.decisions or "(none)", "run at": datetime.now().replace(microsecond=0),
            **{f"changes {k}": n for k, n in sorted(counts.items())}}
    paths = write_report(report, delta, v, t, meta, args.out, args.projects_since)
    print("\n" + "\n".join(f"{k:>16}: {n}" for k, n in sorted(counts.items())))
    print(f"\nrun {meta['run id']}\nreport -> {paths[0]}\n({time.time() - started:.0f}s, {mode})")
    if args.commit:
        print(f"next: python -m scripts.vision_delta.verify --csv {args.csv} --target <url> --run {run_id} --out <folder>")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
