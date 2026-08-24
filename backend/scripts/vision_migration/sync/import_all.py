"""FK-ordered idempotent import driver -- the write path's orchestrator.

Runs every domain in dependency order against a scratch/preview Postgres (the
``MIGRATION_DATABASE_URL`` target, guarded by ``config.assert_scratch_target``),
threading a ``{legacy_id -> velocity_id}`` map out of each domain so the next one
can resolve its foreign keys. For each domain it: reads the staged ``vision_legacy``
rows, transforms them, asks ``engine.decide()`` what to do (adopt/insert/update/skip
vs. the target's CURRENT rows), and performs the plan via ``execute.execute_domain``.

Idempotent + non-destructive by construction: a second run ADOPTs/UPDATEs by stored
legacy key instead of duplicating, and nothing is ever deleted. One transaction wraps
the whole run -- committed only with ``--commit``; a preview run rolls back.

Reads the staged source with a read-only psycopg2 cursor (``vision_legacy`` is raw,
not an ORM model); writes the app tables through the ORM (Decision B).
"""
from __future__ import annotations

import re
from typing import Any, Optional, Hashable

from sqlalchemy import func

from .. import config
from ..transform import (
    transform_workorder, transform_workorder_line, workorder_force_closed,
    transform_category, transform_part, transform_labor, transform_misc,
    transform_profile, transform_project, transform_po, transform_po_line,
    transform_invoice, transform_invoice_line,
    extract_staff, to_str, to_int,
)
from .engine import DomainSpec, decide, INSERT, UPDATE, ADOPT, DUP, SKIP
from .execute import execute_domain, DomainResult

# Recovers the Vision work-order number the original importer tagged into a live
# quote's description as "[WO ####]" -- the natural key that lets a staged workorder
# adopt an existing (un-keyed) migrated quote instead of duplicating it.
_WO_RE = re.compile(r"\[WO (\d+)\]")

# FK-dependency order (docs/MIGRATION_PARITY.md §3): a domain only runs once every
# domain it references has run, so its foreign keys can be resolved from prior maps.
DOMAIN_ORDER = [
    "categories", "parts", "labour", "misc",           # catalog (no cross-domain FKs except category)
    "customers", "vendors", "staff",                   # profiles
    "projects",                                        # -> customer
    "quotes", "quote_lines",                           # -> project ; -> quote + catalog
    "pos", "po_lines",                                 # -> project + vendor ; -> po + part
    "invoices", "invoice_lines",                        # -> quote (reconstructed from ...Shipped) ; -> invoice + quote line
]


class Ctx:
    """Mutable run state shared across domains: the ORM session, the accumulated
    legacy->velocity id maps, and the per-project sequence counters.

    Attributes:
        session: SQLAlchemy Session on the target (scratch) DB.
        raw: Read-only psycopg2 connection for reading the staged ``vision_legacy`` schema.
        maps: ``{domain_name -> {legacy_id -> velocity_id}}`` for FK resolution.
        cat_map: ``{(category_legacy_id, type) -> velocity category id}`` -- categories
            need the ``type`` because one Vision category can fan into a part AND a
            labour category sharing one ``CategoryID`` (the composite-key case).
        force_closed: ``{workorder_legacy_id -> bool}`` built while importing quotes,
            so quote LINES can be force-closed correctly.
    """

    def __init__(self, session: Any, raw: Any) -> None:
        self.session = session
        self.raw = raw
        self.maps: dict[str, dict[int, int]] = {}
        self.cat_map: dict[tuple[int, str], int] = {}
        self.force_closed: dict[int, bool] = {}
        self._quote_seq: dict[int, int] = {}   # project_id -> last quote_sequence handed out
        self._po_seq: dict[int, int] = {}      # project_id -> last po_sequence handed out
        self._invoice_seq: dict[int, int] = {} # quote_id -> last invoice_sequence handed out

    def next_seq(self, cache: dict[int, int], model: Any, seq_col: Any, project_id: int) -> int:
        """Return the next per-project sequence (quote_sequence / po_sequence).

        Seeds once per project from the target's current MAX for that project (so we
        never collide with existing/adopted rows), then increments in memory.
        """
        if project_id not in cache:                    # first time we insert under this project
            current = self.session.query(func.coalesce(func.max(seq_col), 0)).filter(
                model.project_id == project_id).scalar() or 0
            cache[project_id] = current                # start after the highest existing sequence
        cache[project_id] += 1                         # hand out the next one
        return cache[project_id]

    def next_invoice_seq(self, quote_id: int) -> int:
        """Return the next per-QUOTE invoice_sequence (1, 2, 3...).

        Invoices sequence per quote (not per project), so this mirrors ``next_seq`` but
        keys on ``Invoice.quote_id``. Seeds once per quote from the target's current MAX
        (so a partial re-run never collides with an already-migrated invoice), then
        increments in memory.
        """
        from models import Invoice
        if quote_id not in self._invoice_seq:          # first invoice we insert under this quote
            current = self.session.query(func.coalesce(func.max(Invoice.invoice_sequence), 0)).filter(
                Invoice.quote_id == quote_id).scalar() or 0
            self._invoice_seq[quote_id] = current      # start after the highest existing sequence
        self._invoice_seq[quote_id] += 1               # hand out the next one
        return self._invoice_seq[quote_id]


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

def _staged(raw: Any, table: str) -> list[dict[str, Any]]:
    """Read all rows of a staged ``vision_legacy`` table as plain dicts (field-named)."""
    import psycopg2.extras
    from psycopg2 import sql
    cur = raw.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql.SQL("SELECT * FROM {}.{}").format(
        sql.Identifier(config.STAGING_SCHEMA), sql.Identifier(table)))
    return [dict(r) for r in cur.fetchall()]


def _existing_by_legacy(session: Any, model: Any, source: str) -> dict[tuple[str, int], int]:
    """``{(source, legacy_id) -> velocity id}`` for rows a prior run already keyed."""
    q = session.query(model.id, model.legacy_id).filter(
        model.legacy_source == source, model.legacy_id.isnot(None))
    return {(source, lid): vid for (vid, lid) in q.all()}


def _run(ctx: Ctx, name: str, source: str, rows: list[dict[str, Any]], model: Any, *,
         legacy_id_key: str, natural_of_row=None, existing_by_natural=None,
         to_fields, on_insert=None, insert_extra=None) -> DomainResult:
    """Plan + perform one domain, store its id map, and print a one-line ledger.

    Args:
        ctx: The run context.
        name: Domain name (also the ``maps`` key downstream domains look up).
        source: ``legacy_source`` stamped on written rows (the Vision table name).
        rows: Transformed source rows for this domain.
        model: Target ORM class.
        legacy_id_key: Which key in a transformed row holds its Vision primary key.
        natural_of_row: ``row -> natural key`` (or None) for adoption; None = never adopt.
        existing_by_natural: ``{natural key -> velocity id}`` of un-keyed adoptable rows.
        to_fields / on_insert / insert_extra: passed through to ``execute_domain``.

    Returns:
        The :class:`DomainResult`; also recorded in ``ctx.maps[name]``.
    """
    spec = DomainSpec(
        legacy_source=source,
        legacy_id_of=lambda r: r.get(legacy_id_key),                       # its Vision PK
        natural_key_of=(natural_of_row or (lambda r: None)),              # adoption key (or none)
    )
    existing_by_legacy = _existing_by_legacy(ctx.session, model, source)  # prior-run keyed rows -> UPDATE
    decisions = decide(rows, spec, existing_by_legacy, existing_by_natural or {})
    result = execute_domain(ctx.session, model, rows, decisions, to_fields,
                            on_insert=on_insert, insert_extra=insert_extra)
    ctx.maps[name] = result.id_map
    c = result.counts
    extra = f"  ({len(result.unresolved)} unresolved)" if result.unresolved else ""
    print(f"  {name:<12} insert={c[INSERT]:>6}  update={c[UPDATE]:>6}  "
          f"adopt={c[ADOPT]:>6}  dup={c[DUP]:>6}  skip={c[SKIP]:>6}{extra}")
    return result


# --------------------------------------------------------------------------- #
# Catalog domains
# --------------------------------------------------------------------------- #

def _import_categories(ctx: Ctx) -> None:
    """tblPartsCategories -> categories. Processed PER TYPE so the composite key
    ``(legacy_source, legacy_id, type)`` holds: one Vision category typed
    "Application & Material" fans into a part AND a labour category sharing one
    ``CategoryID``, so each type is keyed independently.
    """
    from models import Category
    source = "tblPartsCategories"
    # Flatten each source row's 0/1/2 category dicts, then split by Velocity type.
    all_cats = [c for row in _staged(ctx.raw, source) for c in transform_category(row)]
    for ctype in ("part", "labor"):                    # one engine pass per type
        rows = [c for c in all_cats if c["type"] == ctype]
        # existing_by_legacy filtered to THIS type, so (source, legacy_id) is unique here.
        q = ctx.session.query(Category.id, Category.legacy_id).filter(
            Category.legacy_source == source, Category.legacy_id.isnot(None),
            Category.type == ctype)
        by_legacy = {(source, lid): vid for (vid, lid) in q.all()}
        # existing_by_natural: an un-keyed category adopted by (name, type).
        qn = ctx.session.query(Category.id, Category.name).filter(Category.type == ctype)
        by_natural = {name: vid for (vid, name) in qn.all()}
        spec = DomainSpec(source, lambda r: r.get("category_legacy_id"), lambda r: r.get("name"))
        decisions = decide(rows, spec, by_legacy, by_natural)
        res = execute_domain(ctx.session, Category, rows, decisions,
                             to_fields=lambda r: {"name": r["name"], "type": r["type"]})
        # Key the map by (legacy_id, type) so parts/labour resolve the right category.
        for r, d in zip(rows, decisions):
            vid = res.id_map.get(d.legacy_id)
            if vid is not None:
                ctx.cat_map[(d.legacy_id, ctype)] = vid
        print(f"  categories/{ctype:<5} insert={res.counts[INSERT]:>6}  update={res.counts[UPDATE]:>6}  "
              f"adopt={res.counts[ADOPT]:>6}  dup={res.counts[DUP]:>6}  skip={res.counts[SKIP]:>6}")


def _import_parts(ctx: Ctx) -> None:
    """tblMaterial -> parts. LM- combo kits (``skipped_lm``) are dropped from the
    catalog (G7). category/vendor FKs resolve from prior maps (nullable -> None if absent)."""
    from models import Part
    source = "tblMaterial"
    rows = [p for p in (transform_part(r) for r in _staged(ctx.raw, source)) if not p["skipped_lm"]]
    existing = ctx.session.query(Part.id, Part.part_number).all()
    by_natural = {num: vid for (vid, num) in existing}

    def to_fields(r):
        return {
            "part_number": r["part_number"],
            "description": r["description"],
            "cost": r["cost"],
            "markup_percent": r["markup_percent"],
            "category_id": ctx.cat_map.get((r["category_legacy_id"], "part")),  # nullable FK
            "vendor_id": ctx.maps.get("vendors", {}).get(r["vendor_legacy_id"]),  # nullable FK
        }
    _run(ctx, "parts", source, rows, Part, legacy_id_key="part_legacy_id",
         natural_of_row=lambda r: r["part_number"], existing_by_natural=by_natural, to_fields=to_fields)


def _import_labour(ctx: Ctx) -> None:
    """tblApplication -> labor. category FK resolves to the LABOUR category."""
    from models import Labor
    source = "tblApplication"
    rows = [transform_labor(r) for r in _staged(ctx.raw, source)]
    existing = ctx.session.query(Labor.id, Labor.description).all()
    by_natural = {desc: vid for (vid, desc) in existing}

    def to_fields(r):
        return {
            "description": r["description"] or "",     # description is NOT NULL
            "hours": r["hours"],
            "rate": r["rate"],
            "markup_percent": r["markup_percent"],
            "category_id": ctx.cat_map.get((r["category_legacy_id"], "labor")),
        }
    _run(ctx, "labour", source, rows, Labor, legacy_id_key="labor_legacy_id",
         natural_of_row=lambda r: r["description"], existing_by_natural=by_natural, to_fields=to_fields)


def _import_misc(ctx: Ctx) -> None:
    """tblZones -> miscellaneous. Keyed by description (first-wins; tiny catalog)."""
    from models import Miscellaneous
    source = "tblZones"
    rows = [transform_misc(r) for r in _staged(ctx.raw, source)]
    existing = ctx.session.query(Miscellaneous.id, Miscellaneous.description).all()
    by_natural = {desc: vid for (vid, desc) in existing}

    def to_fields(r):
        return {
            "description": r["description"],
            "unit_price": r["unit_price"],
            "markup_percent": r["markup_percent"],
            "is_system_item": r["is_system_item"],
        }
    _run(ctx, "misc", source, rows, Miscellaneous, legacy_id_key="misc_legacy_id",
         natural_of_row=lambda r: r["description"], existing_by_natural=by_natural, to_fields=to_fields)


# --------------------------------------------------------------------------- #
# Profiles (customers / vendors / staff) + contacts
# --------------------------------------------------------------------------- #

def _make_contacts(session: Any, obj: Any, contacts: list[dict[str, Any]]) -> None:
    """Create Contact + ContactPhone children for a freshly-inserted profile.

    Only runs on INSERT (see execute_domain.on_insert): existing profiles keep their
    current contacts on ADOPT/UPDATE, so re-runs don't duplicate contacts.
    """
    from models import Contact, ContactPhone, PhoneType
    for c in contacts:
        contact = Contact(profile_id=obj.id, name=c["name"], job_title=c.get("job_title"),
                          email=c.get("email"))
        session.add(contact)
        session.flush()                                # need contact.id for its phones
        if c.get("phone"):                             # work phone -> ContactPhone(work)
            session.add(ContactPhone(contact_id=contact.id, type=PhoneType.work, number=c["phone"]))
        if c.get("cell"):                              # mobile -> ContactPhone(mobile)
            session.add(ContactPhone(contact_id=contact.id, type=PhoneType.mobile, number=c["cell"]))


def _import_profiles(ctx: Ctx, name: str, source: str, profile_type: str) -> None:
    """Shared customer/vendor importer. Natural key = profile name within the type
    (existing customers/vendors carry no Vision id, so first-run adoption is by name).
    """
    from models import Profile, ProfileType
    staged = _staged(ctx.raw, source)
    # transform_profile returns (profile_dict, contacts); carry contacts on the row.
    rows = []
    for r in staged:
        prof, contacts = transform_profile(r, profile_type)
        prof["_contacts"] = contacts                   # stash for the on_insert hook
        rows.append(prof)
    lid_key = f"{profile_type}_legacy_id"              # customer_legacy_id / vendor_legacy_id
    existing = ctx.session.query(Profile.id, Profile.name).filter(
        Profile.type == ProfileType(profile_type)).all()
    by_natural = {nm: vid for (vid, nm) in existing}

    def to_fields(r):
        return {"name": r["name"], "type": ProfileType(profile_type),
                "pst": r.get("pst"), "address": r.get("address"), "postal_code": r.get("postal_code")}

    def on_insert(session, obj, r):
        _make_contacts(session, obj, r.get("_contacts", []))

    _run(ctx, name, source, rows, Profile, legacy_id_key=lid_key,
         natural_of_row=lambda r: r["name"], existing_by_natural=by_natural,
         to_fields=to_fields, on_insert=on_insert)


def _import_staff(ctx: Ctx) -> None:
    """tblEmployees -> staff profiles. Keyed on EmployeeID only (same-name staff are
    distinct; no name adoption), so on a clean run every employee INSERTs."""
    from models import Profile, ProfileType
    source = "tblEmployees"
    rows = extract_staff(_staged(ctx.raw, source))     # already-shaped staff dicts (+ contacts)

    def to_fields(r):
        return {"name": r["name"], "type": ProfileType.staff, "pst": r.get("pst"),
                "address": r.get("address"), "postal_code": r.get("postal_code"),
                "staff_roles": r.get("staff_roles")}

    def on_insert(session, obj, r):
        _make_contacts(session, obj, r.get("contacts", []))

    _run(ctx, "staff", source, rows, Profile, legacy_id_key="staff_legacy_id",
         natural_of_row=None, existing_by_natural={}, to_fields=to_fields, on_insert=on_insert)


# --------------------------------------------------------------------------- #
# Projects -> Quotes -> Quote lines
# --------------------------------------------------------------------------- #

def _import_projects(ctx: Ctx) -> None:
    """tblProjects -> projects. customer_id is REQUIRED (NOT NULL): a project whose
    ClientID doesn't resolve to a migrated customer is SKIPPED (to_fields -> None),
    matching the importer's "unknown-ClientID rows skipped"."""
    from models import Project
    source = "tblProjects"
    rows = [transform_project(r) for r in _staged(ctx.raw, source)]
    existing = ctx.session.query(Project.id, Project.uca_project_number).all()
    by_natural = {uca: vid for (vid, uca) in existing}

    def to_fields(r):
        customer_id = ctx.maps.get("customers", {}).get(r["client_legacy_id"])
        if customer_id is None or not r.get("uca_project_number"):
            return None                                # NOT NULL customer_id / uca number -> unwritable
        return {"name": r["name"] or f"Project {r['project_legacy_id']}",
                "customer_id": customer_id, "created_on": r["created_on"],
                "status": r["status"], "ucsh_project_number": r["ucsh_project_number"],
                "uca_project_number": r["uca_project_number"], "project_lead": r["project_lead"]}
    _run(ctx, "projects", source, rows, Project, legacy_id_key="project_legacy_id",
         natural_of_row=lambda r: r["uca_project_number"], existing_by_natural=by_natural, to_fields=to_fields)


def _import_quotes(ctx: Ctx) -> None:
    """tblServiceRecords -> quotes. project_id REQUIRED; quote_sequence assigned per
    project on INSERT only. Also builds the ``force_closed`` map for the line import.
    Quote STATUS is intentionally left at its default: the app derives it from the line
    fulfillment quantities on read (compute_quote_status), so it is correct when shown.
    """
    from models import Quote
    source = "tblServiceRecords"
    staged = _staged(ctx.raw, source)
    rows = [transform_workorder(r) for r in staged]
    # Force-closed map (workorder number -> bool) for the line importer.
    for r in staged:
        wo = transform_workorder(r)["workorder_legacy_id"]
        if wo:
            ctx.force_closed[wo] = workorder_force_closed(r)
    # existing_by_natural: {WO number -> live quote id} from the [WO ####] tag.
    by_natural: dict[Hashable, int] = {}
    for vid, desc in ctx.session.query(Quote.id, Quote.work_description).all():
        m = _WO_RE.search(to_str(desc))
        if m:
            by_natural.setdefault(int(m.group(1)), vid)

    def to_fields(r):
        project_id = ctx.maps.get("projects", {}).get(r["project_legacy_id"])
        if project_id is None:
            return None                                # project_id is NOT NULL
        return {"project_id": project_id, "created_at": r["created_at"],
                "client_po_number": r["client_po_number"], "work_description": r["work_description"],
                "legacy_imported": True}

    def insert_extra(r):
        project_id = ctx.maps.get("projects", {}).get(r["project_legacy_id"])
        return {"quote_sequence": ctx.next_seq(ctx._quote_seq, Quote, Quote.quote_sequence, project_id)}

    _run(ctx, "quotes", source, rows, Quote, legacy_id_key="workorder_legacy_id",
         natural_of_row=lambda r: r["workorder_legacy_id"], existing_by_natural=by_natural,
         to_fields=to_fields, insert_extra=insert_extra)


def _import_quote_lines(ctx: Ctx) -> None:
    """The three workorder-line tables -> quote_line_items. Each line resolves its
    parent quote (by workorder number) and its catalog ref (part/labour/misc). Lines
    whose parent quote didn't import are skipped (quote_id NOT NULL)."""
    from models import QuoteLineItem
    quote_map = ctx.maps.get("quotes", {})             # workorder_legacy_id -> quote velocity id
    part_map = ctx.maps.get("parts", {})
    labour_map = ctx.maps.get("labour", {})
    misc_map = ctx.maps.get("misc", {})
    ref_maps = {"part": (part_map, "part_id", "part_legacy_id"),
                "labor": (labour_map, "labor_id", "labor_legacy_id"),
                "misc": (misc_map, "misc_id", "misc_legacy_id")}

    for source, item_type in (("tblWorkorderApplication", "labor"),
                              ("tblWorkorderMaterial", "part"),
                              ("tblWorkorderZones", "misc")):
        staged = _staged(ctx.raw, source)
        rows = []
        for r in staged:
            # A line is force-closed iff its OWNING workorder was (map built in _import_quotes).
            fc = ctx.force_closed.get(int(r.get("intWorkorderID") or 0), False)
            rows.append(transform_workorder_line(r, item_type, force_closed=fc))
        cat_map, fk_col, ref_key = ref_maps[item_type]

        def to_fields(rr, _cat_map=cat_map, _fk_col=fk_col, _ref_key=ref_key, _it=item_type):
            quote_id = quote_map.get(rr["workorder_legacy_id"])
            if quote_id is None:
                return None                            # quote_id is NOT NULL -> skip orphan line
            return {"quote_id": quote_id, "item_type": _it,
                    _fk_col: _cat_map.get(rr.get(_ref_key)),   # catalog FK (nullable: LM/detached -> None)
                    "description": rr["description"], "quantity": rr["quantity"],
                    "unit_price": rr["unit_price"], "base_cost": rr["base_cost"],
                    "qty_fulfilled": rr["qty_fulfilled"], "qty_pending": rr["qty_pending"]}
        _run(ctx, f"quote_lines/{item_type}", source, rows, QuoteLineItem,
             legacy_id_key="line_legacy_id", natural_of_row=None, existing_by_natural={},
             to_fields=to_fields)


# --------------------------------------------------------------------------- #
# Purchase orders + PO lines
# --------------------------------------------------------------------------- #

def _import_pos(ctx: Ctx) -> None:
    """tblPurchaseOrders -> purchase_orders. project_id + vendor_id REQUIRED;
    po_sequence assigned per project on INSERT. No natural key (migrated POs carry no
    Vision id), so a first run INSERTs them all -- adoption comes only after the keys
    are backfilled by this run."""
    from models import PurchaseOrder
    source = "tblPurchaseOrders"
    rows = [transform_po(r) for r in _staged(ctx.raw, source)]

    def to_fields(r):
        project_id = ctx.maps.get("projects", {}).get(r["project_legacy_id"])
        vendor_id = ctx.maps.get("vendors", {}).get(r["vendor_legacy_id"])
        if project_id is None or vendor_id is None:
            return None                                # both are NOT NULL
        return {"project_id": project_id, "vendor_id": vendor_id, "created_at": r["created_at"],
                "work_description": r["work_description"], "vendor_po_number": r["vendor_po_number"],
                "expected_delivery_date": r["expected_delivery_date"], "legacy_imported": True}

    def insert_extra(r):
        project_id = ctx.maps.get("projects", {}).get(r["project_legacy_id"])
        return {"po_sequence": ctx.next_seq(ctx._po_seq, PurchaseOrder, PurchaseOrder.po_sequence, project_id)}

    _run(ctx, "pos", source, rows, PurchaseOrder, legacy_id_key="po_legacy_id",
         natural_of_row=None, existing_by_natural={}, to_fields=to_fields, insert_extra=insert_extra)


def _import_po_lines(ctx: Ctx) -> None:
    """tblPurchaseOrdersMaterial -> po_line_items. Resolves parent PO + part FK."""
    from models import POLineItem
    source = "tblPurchaseOrdersMaterial"
    rows = [transform_po_line(r) for r in _staged(ctx.raw, source)]
    po_map = ctx.maps.get("pos", {})
    part_map = ctx.maps.get("parts", {})

    def to_fields(r):
        po_id = po_map.get(r["po_legacy_id"])
        if po_id is None:
            return None                                # purchase_order_id is NOT NULL
        return {"purchase_order_id": po_id, "item_type": "part",
                "part_id": part_map.get(r["part_legacy_id"]),  # nullable FK
                "description": r["description"], "quantity": r["quantity"],
                "unit_price": r["unit_price"], "qty_received": r["qty_received"],
                "qty_pending": r["qty_pending"]}
    _run(ctx, "po_lines", source, rows, POLineItem, legacy_id_key="line_legacy_id",
         natural_of_row=None, existing_by_natural={}, to_fields=to_fields)


# --------------------------------------------------------------------------- #
# Invoices + invoice lines (reconstructed from the three ...Shipped ledgers)
# --------------------------------------------------------------------------- #

def _import_invoices(ctx: Ctx) -> None:
    """The three ...Shipped ledgers -> invoices (reconstructed; see transform_invoice).

    A Vision invoice is the set of shipment events sharing (intWorkorderID,
    intInvoiceNumber>0); intInvoiceNumber is globally unique, so it is the invoice
    legacy_id (legacy_source "workorder_shipped"). quote_id resolves from the parent
    workorder; invoice_sequence is assigned per quote in invoice-number order. Migrated
    invoices are inserted directly as historical records -- NO freeze flow, NO quote-line
    mutation, NO snapshot (the line import already set the quote's fulfillment). Unbilled
    events (intInvoiceNumber == 0) form no invoice.
    """
    from models import Invoice, Quote
    quote_map = ctx.maps.get("quotes", {})             # workorder_legacy_id -> quote velocity id
    # A quote's created_at is the fallback date when an invoice group has no parseable ship
    # date, so a migrated invoice NEVER gets a NULL created_at -- the API requires it (a NULL
    # 500s the invoice reads), and a NULL would also flip across re-runs since an UPDATE can't
    # re-fire the model default.
    q_created = {qid: ca for (qid, ca) in ctx.session.query(Quote.id, Quote.created_at).all()}

    # Group billed shipment events across all three ledgers by (workorder, invoice#).
    groups: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for ledger in ("tblWorkorderApplicationShipped", "tblWorkorderMaterialShipped",
                   "tblWorkorderZonesShipped"):
        for e in _staged(ctx.raw, ledger):
            inv = to_int(e.get("intInvoiceNumber"), default=0)
            wo = to_int(e.get("intWorkorderID"), default=0)
            if inv > 0 and wo:                         # billed events only (inv 0 = unbilled -> no invoice)
                groups.setdefault((wo, inv), []).append(e)

    # One invoice row per group; sort so invoice_sequence follows invoice-number order per quote.
    rows = [transform_invoice(wo, inv, events) for (wo, inv), events in groups.items()]
    rows.sort(key=lambda r: (r["workorder_legacy_id"], r["invoice_legacy_id"]))

    def to_fields(r):
        quote_id = quote_map.get(r["workorder_legacy_id"])
        if quote_id is None:
            return None                                # quote_id is NOT NULL -> skip orphan invoice
        notes = f"Vision invoice {r['full_invoice_number']}" if r["full_invoice_number"] else None
        fields = {"quote_id": quote_id, "status": "Sent",   # payment status not in Vision -> default "Sent"
                  "quote_version": 0, "notes": notes}        # migrated quotes sit at version 0
        ca = r["invoice_date"] or q_created.get(quote_id)   # invoice's ship date, else the quote's date
        if ca is not None:                                  # only set it when we HAVE a date -- never write
            fields["created_at"] = ca                       # NULL (let the model default stand + stay stable)
        return fields

    def insert_extra(r):
        # invoice_sequence is INSERT-only (a re-run UPDATEs and keeps it), assigned per quote.
        return {"invoice_sequence": ctx.next_invoice_seq(quote_map[r["workorder_legacy_id"]])}

    _run(ctx, "invoices", "workorder_shipped", rows, Invoice, legacy_id_key="invoice_legacy_id",
         natural_of_row=None, existing_by_natural={}, to_fields=to_fields, insert_extra=insert_extra)


def _import_invoice_lines(ctx: Ctx) -> None:
    """The three ...Shipped ledgers -> invoice_line_items. Each billed shipment event ->
    one line, linked to its parent invoice (by intInvoiceNumber) and to the migrated quote
    LINE (by intWorkorderPartID via the per-type quote_lines maps). ``qty_fulfilled_this_invoice``
    is the event's shipped quantity (which drives the invoice total); ``qty_fulfilled_total``
    is the running total for that line across its invoices, in invoice-number order. Lines
    whose parent invoice didn't import are skipped (invoice_id NOT NULL).
    """
    from models import InvoiceLineItem, QuoteLineItem, Part, Labor, Miscellaneous
    invoice_map = ctx.maps.get("invoices", {})         # invoice_number -> invoice velocity id
    # Each invoice line copies its DESCRIPTION and catalog ref from the quote LINE it bills --
    # exactly like the app's freeze flow (create_invoice) -- so the invoice shows the same item
    # and text as its quote line (and the two never disagree). Snapshot the migrated quote lines.
    qline_snap = {qid: (desc, pid, lid, mid) for (qid, desc, pid, lid, mid) in ctx.session.query(
        QuoteLineItem.id, QuoteLineItem.description, QuoteLineItem.part_id,
        QuoteLineItem.labor_id, QuoteLineItem.misc_id).all()}
    # Catalog descriptions -- fallback for the rare line whose quote line didn't import (no snapshot).
    cat_desc = {"part": {i: pn for (i, pn) in ctx.session.query(Part.id, Part.part_number).all()},
                "labor": {i: d for (i, d) in ctx.session.query(Labor.id, Labor.description).all()},
                "misc": {i: d for (i, d) in ctx.session.query(Miscellaneous.id, Miscellaneous.description).all()}}
    cat_maps = {"part": ctx.maps.get("parts", {}), "labor": ctx.maps.get("labour", {}),
                "misc": ctx.maps.get("misc", {})}

    for ledger, item_type in (("tblWorkorderApplicationShipped", "labor"),
                              ("tblWorkorderMaterialShipped", "part"),
                              ("tblWorkorderZonesShipped", "misc")):
        events = [transform_invoice_line(e, item_type) for e in _staged(ctx.raw, ledger)
                  if to_int(e.get("intInvoiceNumber"), default=0) > 0]
        # Running fulfilled-total per quote LINE, in invoice-number order, so each invoice
        # line reflects the cumulative shipped quantity as of its own invoice.
        cumulative: dict[int, int] = {}                # event_legacy_id -> running total for its line
        run_total: dict[int, int] = {}                 # line_legacy_id -> total shipped so far
        for ev in sorted(events, key=lambda e: (e["line_legacy_id"] or 0, e["invoice_legacy_id"] or 0)):
            key = ev["line_legacy_id"]
            run_total[key] = run_total.get(key, 0) + ev["ship_qty"]
            cumulative[ev["event_legacy_id"]] = run_total[key]
        line_map = ctx.maps.get(f"quote_lines/{item_type}", {})     # WorkorderPartID -> quote_line velocity id
        cmap, dmap = cat_maps[item_type], cat_desc[item_type]

        def to_fields(r, _line_map=line_map, _cmap=cmap, _dmap=dmap, _it=item_type, _cum=cumulative):
            invoice_id = invoice_map.get(r["invoice_legacy_id"])
            if invoice_id is None:
                return None                            # invoice_id is NOT NULL -> parent invoice absent
            qline_id = _line_map.get(r["line_legacy_id"])          # the migrated quote line, if any
            snap = qline_snap.get(qline_id) if qline_id is not None else None
            if snap is not None:                       # copy the quote line's catalog snapshot (freeze parity)
                description, part_id, labor_id, misc_id = snap
                if not description:                     # blank quote-line desc -> catalog item's desc (freeze flow's "or item_desc")
                    description = _dmap.get(part_id or labor_id or misc_id)
            else:                                      # rare orphan (no quote line) -> catalog by the event's product id
                cat_id = _cmap.get(r["product_legacy_id"])
                description = _dmap.get(cat_id)        # catalog description, or None if unresolved
                part_id = cat_id if _it == "part" else None
                labor_id = cat_id if _it == "labor" else None
                misc_id = cat_id if _it == "misc" else None
            total = _cum.get(r["event_legacy_id"], r["ship_qty"])   # cumulative shipped up to this invoice
            return {"invoice_id": invoice_id, "item_type": _it, "quote_line_item_id": qline_id,
                    "description": description, "part_id": part_id, "labor_id": labor_id, "misc_id": misc_id,
                    "unit_price": r["unit_price"], "qty_ordered": r["ordered_qty"],
                    "qty_fulfilled_this_invoice": r["ship_qty"],
                    "qty_fulfilled_total": total,
                    "qty_pending_after": max(0, r["ordered_qty"] - total)}

        _run(ctx, f"invoice_lines/{item_type}", ledger, events, InvoiceLineItem,
             legacy_id_key="event_legacy_id", natural_of_row=None, existing_by_natural={},
             to_fields=to_fields)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

_RUNNERS = {
    "categories": _import_categories, "parts": _import_parts, "labour": _import_labour,
    "misc": _import_misc, "customers": lambda c: _import_profiles(c, "customers", "tblClients", "customer"),
    "vendors": lambda c: _import_profiles(c, "vendors", "tblVendors", "vendor"), "staff": _import_staff,
    "projects": _import_projects, "quotes": _import_quotes, "quote_lines": _import_quote_lines,
    "pos": _import_pos, "po_lines": _import_po_lines,
    "invoices": _import_invoices, "invoice_lines": _import_invoice_lines,
}


def run_import(target_url: str, *, commit: bool, confirmed: bool, only: Optional[str] = None) -> None:
    """Run the whole FK-ordered import against a scratch target.

    Args:
        target_url: The scratch/preview Postgres URL (holds the app schema + the staged
            ``vision_legacy`` schema). Guarded against production.
        commit: True to commit the run; False (default) rolls the whole thing back
            (a safe preview that still exercises every write).
        confirmed: The operator's ``--i-understand-scratch-db`` acknowledgement, required
            by :func:`config.assert_scratch_target`.
        only: If given, run just that one domain (must have its FK-parent domains already
            imported in the target); otherwise run every domain in dependency order.

    Raises:
        MigrationConfigError: If the target looks like production or isn't confirmed scratch.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    host = config.assert_scratch_target(target_url, confirmed)   # HARD prod-host guard on the write target
    engine = create_engine(target_url)
    session = sessionmaker(bind=engine)()
    raw = config.open_postgres(target_url)
    raw.set_session(readonly=True, autocommit=True)              # staged reads only; never writes here
    print("=" * 72)
    print(f"Vision -> Velocity import  [target host: {host}]  "
          f"{'COMMIT' if commit else 'PREVIEW (rollback)'}")
    print("=" * 72)
    try:
        ctx = Ctx(session, raw)
        order = [only] if only else DOMAIN_ORDER
        for name in order:
            _RUNNERS[name](ctx)                                  # each records its id map in ctx.maps
        if commit:
            session.commit()                                    # persist the whole run atomically
            print("committed.")
        else:
            session.rollback()                                  # preview: discard every write
            print("rolled back (preview). Re-run with --commit to persist.")
    except Exception:
        session.rollback()                                      # never leave a half-written transaction
        raise
    finally:
        session.close()
        raw.close()
