"""Pair Vision and Velocity records at every level and list each difference.

Levels, in dependency order: categories, customers and vendors, their contacts,
parts, labour, misc, projects, quotes, quote lines, purchase orders, PO lines.

Pairing (production rows from the June import carry no Vision ids, so a Vision
id on the Velocity row is used first, then the evidence below):
    categories          type + name
    customers, vendors  type + name
    contacts            name, within a paired customer/vendor
    quotes              the "[WO n]" tag (the lowest-id copy; later copies are clones)
    purchase orders     project + the import's per-project numbering, checked
                        against vendor and order date; then project + vendor + date
    parts, labour, misc how paired quotes and POs use them (a line that matches
                        on description, price and quantity says which Velocity
                        item a Vision item became), then part number / description
    projects            UCA project number when the names agree; when both
                        systems gave one number to two different projects
                        (they numbered on their own after June), the one
                        Velocity project with the same name and customer
    lines               within a paired quote / PO: same item and values, then
                        same item in order, then same kind in order

Every difference names a likely cause, worked out from what each side records:
    june rounding       the June import rounded the quantity (whole numbers, at least 1)
    june import rule    the June import marked every line done and every quote/PO closed
    changed in vision after june   Vision dates the change after the June import
    edited in velocity  the Velocity record has been changed or invoiced/received since
    changed on both sides          both of the above: needs a decision
    unexplained         none of the above fits
"""
from __future__ import annotations

import difflib
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Callable, Iterable, Optional

# Row kinds in the output
MISSING = "missing in velocity"
EXTRA = "velocity only"
DIFFERS = "differs"
COPY = "velocity copy"

# Causes
JUNE_ROUNDING = "june rounding"
JUNE_RULE = "june import rule"
VISION_LATER = "changed in vision after june"
VELOCITY_EDIT = "edited in velocity"
BOTH = "changed on both sides"
UNEXPLAINED = "unexplained"
RENUMBERED = "numbered differently in vision"              # same project, two UCA numbers


def norm(s) -> str:
    """Text as a matching key: trimmed, lowercased, inner whitespace collapsed."""
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _day(v) -> Optional[date]:
    """Date part of a date/datetime, or None."""
    if isinstance(v, datetime):
        return v.date()
    return v if isinstance(v, date) else None


def _money(v) -> Decimal:
    """Amount to the cent, halves up (201.695 -> 201.70), as Velocity stores it."""
    return Decimal(repr(float(v or 0))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# Field comparers: each turns a value into what counts as "the same"
TEXT = lambda v: (str(v).strip() or None) if v is not None else None      # None == "" == "  "
MONEY = _money
QTY = lambda v: round(float(v or 0), 2)                                     # app holds 2 decimals
RATE = lambda v: round(float(v or 0), 4)                                    # derived rates carry more
DAY = _day
NAME = lambda v: norm(v) or None                                            # names compare loosely


def field_diffs(v: dict, t: dict, fields: dict[str, Callable]) -> list[tuple[str, object, object]]:
    """Fields whose values differ once each is put through its comparer.

    Args:
        v: The Vision record.
        t: The Velocity record.
        fields: Field name -> comparer (see TEXT, MONEY, ...).

    Returns:
        ``(field, vision value, velocity value)`` for each differing field.
    """
    return [(f, v.get(f), t.get(f)) for f, cmp in fields.items() if cmp(v.get(f)) != cmp(t.get(f))]


def pair(vrecs: Iterable[dict], trecs: Iterable[dict], key: Callable[[dict], object],
         legacy: bool = True, links: Optional[dict] = None) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    """Pair Vision records with Velocity records.

    Order of evidence: a Vision id stamped on the Velocity record; then
    ``links`` (Vision id -> Velocity id worked out elsewhere); then ``key``,
    pairing in id order when several share it (the June import inserted them
    in Vision order).

    Args:
        vrecs: Vision records (``vid`` = Vision id).
        trecs: Velocity records (``id``, optional ``legacy_id``).
        key: Natural key of a record (same function for both sides).
        legacy: Use ``legacy_id`` first (off where Vision ids are not unique per row).
        links: Optional Vision id -> Velocity id.

    Returns:
        ``(pairs, vision-only, velocity-only)``.
    """
    vrecs, trecs = list(vrecs), list(trecs)
    pairs, used_v, used_t = [], set(), set()

    def take(v, t):
        pairs.append((v, t)); used_v.add(id(v)); used_t.add(id(t))

    by_vid = {v["vid"]: v for v in vrecs}
    if legacy:
        for t in trecs:                                    # 1. stamped Vision id
            v = by_vid.get(t.get("legacy_id"))
            if v is not None and id(v) not in used_v:
                take(v, t)
    if links:
        by_id = {t["id"]: t for t in trecs}
        for vid, tid in links.items():                     # 2. worked-out links
            v, t = by_vid.get(vid), by_id.get(tid)
            if v is not None and t is not None and id(v) not in used_v and id(t) not in used_t:
                take(v, t)
    vk, tk = defaultdict(list), defaultdict(list)          # 3. natural key
    for v in vrecs:
        if id(v) not in used_v:
            vk[key(v)].append(v)
    for t in trecs:
        if id(t) not in used_t:
            tk[key(t)].append(t)
    missing, extra = [], []
    for k in set(vk) | set(tk):
        vs = sorted(vk.get(k, []), key=lambda r: r.get("vid") or 0)
        ts = sorted(tk.get(k, []), key=lambda r: r["id"])
        pairs += list(zip(vs, ts))                         # same key: pair in order
        missing += vs[len(ts):]
        extra += ts[len(vs):]
    return pairs, missing, extra


def cause(vision_later: bool, velocity_edit: bool, june_explains: Optional[str]) -> str:
    """Name the likely cause of one difference.

    Args:
        vision_later: Vision dates the change after the June import.
        velocity_edit: The Velocity record changed since the import.
        june_explains: A June-import cause that reproduces the Velocity value, if any.

    Returns:
        One of the cause constants.
    """
    if vision_later and velocity_edit:
        return BOTH
    if velocity_edit:
        return VELOCITY_EDIT
    if vision_later:
        return VISION_LATER
    return june_explains or UNEXPLAINED


def june_quantity(v) -> int:
    """Quantity the June import stored: rounded to a whole number, at least 1."""
    return max(1, round(float(v or 0)))


def same_quantity(v, t) -> bool:
    """Whether a Velocity quantity is the Vision one, as is or as the June import rounded it."""
    return QTY(v) == QTY(t) or QTY(t) == june_quantity(v)


class Delta:
    """Collects difference rows and per-level counts."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.counts: dict[str, Counter] = defaultdict(Counter)
        self.links: dict[tuple[str, int], int] = {}        # Vision item -> Velocity item, for the update tool
        self.project_map: dict[str, str] = {}               # Vision UCA -> Velocity UCA (or "vision Axxxx" when new)
        self.project_pairs: list[tuple[dict, dict]] = []    # paired projects, for stamping Vision numbers
        self.quote_pairs: list[tuple[dict, dict]] = []      # paired quotes (Vision, Velocity original)
        self.po_pairs: list[tuple[dict, dict]] = []         # paired POs
        self.twins: list[dict] = []                         # Vision WOs Velocity lacks, beside look-alike Velocity quotes
        self.po_twins: list[dict] = []                      # the same for POs
        self.copies: list[dict] = []                        # Velocity copies of June quotes
        self.duplicate_projects: list[dict] = []            # one project entered twice in Velocity
        self.project_numbers: list[dict] = []               # Vision projects whose number Velocity uses differently

    def add(self, level: str, kind: str, vision_key=None, velocity_key=None, context: str = "",
            field: str = "", vision_value=None, velocity_value=None, cause_: str = "",
            project: str = "", ref: Optional[dict] = None) -> None:
        """Record one difference (a whole record, or one field of a paired record).

        ``project`` is the UCA project number the row belongs to ("" for
        reference levels), so the rows can be grouped per project. ``ref``
        carries what the update tool needs to act on the row (the Vision record,
        and for a line its Velocity parent's id); it stays in memory under
        ``_ref`` and is never written out.
        """
        self.rows.append({
            "level": level, "kind": kind, "project": project, "vision_key": vision_key, "velocity_key": velocity_key,
            "context": context, "field": field, "vision_value": vision_value,
            "velocity_value": velocity_value, "cause": cause_, "_ref": ref,
        })

    def tally(self, level: str, vision: int, velocity: int, pairs: int, same: int, missing: int, extra: int) -> None:
        """Record a level's counts for the summary."""
        self.counts[level].update(vision=vision, velocity=velocity, paired=pairs, same=same,
                                  differs=pairs - same, missing=missing, extra=extra)


def compare_simple(delta: Delta, level: str, vrecs: list, trecs: list, key: Callable, fields: dict,
                   label: Callable[[dict], str], legacy: bool = True,
                   links: Optional[dict] = None,
                   project: Callable[[dict], str] = lambda r: "") -> list[tuple[dict, dict]]:
    """Compare one reference level (no versions on either side, so no cause beyond "unexplained").

    Args:
        delta: Where rows go.
        level: Level name for the output.
        vrecs, trecs: Vision and Velocity records.
        key: Natural key.
        fields: Fields to compare -> comparer.
        label: Human label of a record (used for both sides).
        legacy: Pair on Vision ids first.
        links: Optional Vision id -> Velocity id evidence (see :func:`pair`).
        project: UCA project number of a record (projects level only).

    Returns:
        The pairs, for levels nested under this one.
    """
    pairs, missing, extra = pair(vrecs, trecs, key, legacy, links)
    same = 0
    for v, t in pairs:
        diffs = field_diffs(v, t, fields)
        same += not diffs
        for f, vv, tv in diffs:
            delta.add(level, DIFFERS, v.get("vid"), t["id"], label(t), f, vv, tv, UNEXPLAINED, project(t))
    for v in missing:
        delta.add(level, MISSING, v.get("vid"), None, label(v), project=project(v), ref={"vision": v})
    for t in extra:
        delta.add(level, EXTRA, None, t["id"], label(t), project=project(t))
    delta.tally(level, len(vrecs), len(trecs), len(pairs), same, len(missing), len(extra))
    return pairs


def compare_contacts(delta: Delta, profile_pairs: list[tuple[dict, dict]]) -> None:
    """Contacts of each paired customer/vendor, paired by name."""
    fields = {"job_title": TEXT, "email": TEXT, "phones": lambda p: sorted(p or [])}
    n = Counter()
    for vp, tp in profile_pairs:
        vc = [{**c, "vid": i} for i, c in enumerate(vp["contacts"])]   # Vision contacts have no id of their own
        pairs, missing, extra = pair(vc, tp["contacts"], lambda c: norm(c["name"]), legacy=False)
        ctx = f"{tp['type']} {tp['name']}"
        for v, t in pairs:
            diffs = field_diffs(v, t, fields)
            n["same"] += not diffs
            for f, vv, tv in diffs:
                delta.add("contacts", DIFFERS, vp["vid"], t["id"], f"{ctx}: {t['name']}", f, vv, tv, UNEXPLAINED)
        for v in missing:
            delta.add("contacts", MISSING, vp["vid"], tp["id"], f"{ctx}: {v['name']}")
        for t in extra:
            delta.add("contacts", EXTRA, vp["vid"], t["id"], f"{ctx}: {t['name']}")
        n.update(vision=len(vc), velocity=len(tp["contacts"]), pairs=len(pairs), missing=len(missing), extra=len(extra))
    delta.tally("contacts", n["vision"], n["velocity"], n["pairs"], n["same"], n["missing"], n["extra"])


# --------------------------------------------------------------------------- #
# Projects
# --------------------------------------------------------------------------- #

SAME_NAME = 0.6                                            # similarity at which one UCA number is one project
PROJECT_FIELDS = {"name": TEXT, "customer": NAME, "created_on": DAY, "status": TEXT, "ucsh": TEXT, "lead": TEXT}


def _name_key(name: Optional[str]) -> str:
    """A project name as a matching key: lowercase letters and digits only."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def name_similarity(a: Optional[str], b: Optional[str]) -> float:
    """How alike two names are, from 0 to 1, ignoring case, spaces and punctuation.

    Typos score high ("Correctional Center" / "Correctional Centr": 0.98); two
    different projects sharing a number score low (at most 0.40 in the data).
    """
    x, y = _name_key(a), _name_key(b)
    if not x or not y:
        return 1.0 if x == y else 0.0                      # two blanks match, a blank and a name do not
    return difflib.SequenceMatcher(None, x, y).ratio()


def pair_projects(vprojects: list, tprojects: list) -> tuple[list, list, list]:
    """Pair projects.

    1. a Vision id stamped on the Velocity project;
    2. the same UCA number with a similar name (see :data:`SAME_NAME`);
    3. what is left: the one unpaired Velocity project with the same name and
       customer. After the June import both systems numbered new projects on
       their own, so one number can mean two projects; this finds the project
       under its other number. Two or more candidates is not "one", so none.

    Args:
        vprojects, tprojects: Vision and Velocity projects.

    Returns:
        ``(pairs, vision-only, velocity-only)``.
    """
    pairs, used_v, used_t = [], set(), set()

    def take(v, t):
        pairs.append((v, t)); used_v.add(v["vid"]); used_t.add(t["id"])

    by_vid = {v["vid"]: v for v in vprojects}
    for t in tprojects:                                    # 1. stamped Vision id
        v = by_vid.get(t.get("legacy_id"))
        if v is not None and v["vid"] not in used_v:
            take(v, t)
    by_uca = {t["uca"]: t for t in tprojects}
    for v in vprojects:                                    # 2. same number, same project
        t = by_uca.get(v["uca"])
        if v["vid"] not in used_v and t is not None and t["id"] not in used_t \
                and name_similarity(v["name"], t["name"]) >= SAME_NAME:
            take(v, t)
    by_name = defaultdict(list)                            # 3. same name + customer, other number
    for t in tprojects:
        if t["id"] not in used_t:
            by_name[(_name_key(t["name"]), NAME(t["customer"]))].append(t)
    for v in vprojects:
        if v["vid"] in used_v:
            continue
        cands = [t for t in by_name.get((_name_key(v["name"]), NAME(v["customer"])), []) if t["id"] not in used_t]
        if len(cands) == 1:
            take(v, cands[0])
    return pairs, [v for v in vprojects if v["vid"] not in used_v], [t for t in tprojects if t["id"] not in used_t]


def compare_projects(delta: Delta, vprojects: list, tprojects: list, cutoff: date) -> None:
    """Projects, and the Vision -> Velocity project-number map the later levels use.

    A Vision project Velocity lacks keeps its number when Velocity does not use
    it; when Velocity uses the number for another project, the insert gets the
    next free number, and until then it is called "vision <number>".

    Args:
        delta: Where rows go; ``project_map`` and ``project_pairs`` are set.
        vprojects, tprojects: Vision and Velocity projects.
        cutoff: Day of the June import (a Velocity created date on that day is the import's).
    """
    pairs, missing, extra = pair_projects(vprojects, tprojects)
    taken = {t["uca"]: t for t in tprojects}               # number -> the Velocity project using it
    same = 0
    for v, t in pairs:
        diffs = field_diffs(v, t, PROJECT_FIELDS)
        if v["uca"] != t["uca"]:
            diffs.insert(0, ("uca", v["uca"], t["uca"]))   # same project under two numbers
            other = taken.get(v["uca"])
            delta.project_numbers.append({
                "vision number": v["uca"], "name": v["name"], "customer": v["customer"], "vision created": v["created_on"],
                "velocity number": t["uca"], "velocity created": t["created_on"],
                "what happens": f"paired with velocity {t['uca']} (same name and customer); velocity number kept",
                "velocity project using the vision number": f"{other['uca']} {other['name']}" if other else None})
        same += not diffs
        for f, vv, tv in diffs:
            if f == "uca":
                why = RENUMBERED
            elif f == "created_on" and _day(tv) == cutoff and vv is not None and _day(vv) != cutoff:
                why = JUNE_RULE                            # the import stamped its own time
            else:
                why = UNEXPLAINED
            delta.add("projects", DIFFERS, v["vid"], t["id"], f"{t['uca']} {t['name']}", f, vv, tv, why, t["uca"],
                      {"vision": v})
        delta.project_map[v["uca"]] = t["uca"]
    for v in missing:
        new = v["uca"] in taken                            # Velocity uses this number for another project
        label = f"vision {v['uca']}" if new else v["uca"]
        delta.project_map[v["uca"]] = label
        if new:
            other = taken[v["uca"]]
            delta.project_numbers.append({
                "vision number": v["uca"], "name": v["name"], "customer": v["customer"], "vision created": v["created_on"],
                "velocity number": None, "velocity created": None,
                "what happens": "not in velocity: added under the next free number, vision number recorded on it",
                "velocity project using the vision number": f"{other['uca']} {other['name']}"})
        delta.add("projects", MISSING, v["vid"], None,
                  f"{v['uca']} {v['name']}" + (" (number used by another velocity project: gets a new number)" if new else ""),
                  project=label, ref={"vision": v, "new_number": new})
    for t in extra:
        delta.add("projects", EXTRA, None, t["id"], f"{t['uca']} {t['name']}", project=t["uca"])
    delta.project_pairs = pairs
    delta.tally("projects", len(vprojects), len(tprojects), len(pairs), same, len(missing), len(extra))


def find_duplicate_projects(delta: Delta, t: dict, cutoff: date) -> None:
    """Projects entered twice in Velocity since the June import, with what each copy holds.

    Same name and customer, at least one created after the import. Nothing is
    changed; the findings and a prediction go to the report for a person.

    Args:
        delta: Filled through quotes and POs (``project_map`` and the rows are used).
        t: :func:`scripts.vision_delta.velocity.load` output.
        cutoff: Day of the June import.
    """
    groups = defaultdict(list)
    for p in t["projects"]:
        groups[(_name_key(p["name"]), NAME(p["customer"]))].append(p)
    quotes, pos, invoices, last = Counter(), Counter(), Counter(), {}
    for q in t["quotes"]:
        quotes[q["uca"]] += 1
        invoices[q["uca"]] += q["invoices"]
        if q["last_change"] and (q["uca"] not in last or q["last_change"] > last[q["uca"]]):
            last[q["uca"]] = q["last_change"]
    for po in t["pos"]:
        pos[po["uca"]] += 1
    incoming = Counter(r["project"] for r in delta.rows if r["level"] in ("quotes", "pos") and r["kind"] == MISSING)
    vision_of = {tp["uca"]: vp["uca"] for vp, tp in delta.project_pairs}
    after = lambda p: bool(_day(p["created_on"]) and _day(p["created_on"]) > cutoff)
    for (_, customer), members in groups.items():
        if len(members) < 2 or not any(after(p) for p in members):
            continue
        rows = [{"uca": p["uca"], "name": p["name"], "customer": p["customer"], "created": p["created_on"],
                 "imported in june": not after(p), "vision project": vision_of.get(p["uca"]),
                 "quotes": quotes[p["uca"]], "pos": pos[p["uca"]], "invoices": invoices[p["uca"]],
                 "last quote change": last.get(p["uca"]),
                 "vision records added at cutover": incoming[p["uca"]]} for p in sorted(members, key=lambda p: p["id"])]
        empty = [r for r in rows if not r["quotes"] and not r["pos"]]
        if empty and len(empty) < len(rows):
            e, kept = empty[0], ", ".join(r["uca"] for r in rows if r not in empty)
            guess = (f"likely a genuine duplicate: {e['uca']} was created by hand on {_day(e['created'])} and held "
                     f"nothing before cutover; the work is in {kept}. Likely outcome: move what it receives at "
                     f"cutover into {kept}, then archive {e['uca']}")
        else:
            guess = "both copies hold work: a person decides which to keep"
        for r in rows:
            delta.duplicate_projects.append({"group": rows[0]["name"], **r, "prediction": guess})


# --------------------------------------------------------------------------- #
# Pairing quotes and POs (done first: their lines identify the inventory)
# --------------------------------------------------------------------------- #

def pair_quotes(vquotes: list, tquotes: list) -> tuple[list, list, list, list]:
    """Pair quotes by Vision work order.

    Args:
        vquotes, tquotes: Vision and Velocity quotes.

    Returns:
        ``(pairs, vision-only, velocity-only, clones)``; ``clones`` are
        ``(original, copy)`` Velocity quotes carrying the same work order.
    """
    tagged = defaultdict(list)
    for t in tquotes:
        wo = t.get("legacy_id") or t["wo"]                 # stamped id, else the "[WO n]" tag
        # (never vision_wo_number: a Velocity-made twin shares it on purpose)
        if wo:
            tagged[wo].append(t)
    pairs, missing, clones, used = [], [], [], set()
    for v in vquotes:
        copies = sorted(tagged.get(v["wo"], []), key=lambda t: t["id"])
        if not copies:
            missing.append(v)
            continue
        pairs.append((v, copies[0]))                       # lowest id is the imported original
        clones += [(copies[0], c) for c in copies[1:]]     # later ones were copied in Velocity
        used.update(t["id"] for t in copies)
    return pairs, missing, [t for t in tquotes if t["id"] not in used], clones


def pair_pos(vpos: list, tpos: list) -> tuple[list, list, list]:
    """Pair purchase orders.

    A Velocity PO with a Vision id pairs with it. Otherwise the June import
    numbered each project's POs by order date then Vision id, so project +
    number finds the June original; it is accepted only when vendor and order
    date agree. Anything left pairs on project + vendor + order date.

    Args:
        vpos, tpos: Vision and Velocity POs.

    Returns:
        ``(pairs, vision-only, velocity-only)``.
    """
    pairs, used_v, used_t = [], set(), set()

    def take(v, t):
        pairs.append((v, t)); used_v.add(v["vid"]); used_t.add(t["id"])

    by_vid = {v["vid"]: v for v in vpos}
    for t in tpos:                                         # 1. stamped Vision id
        v = by_vid.get(t.get("legacy_id"))
        if v is not None and v["vid"] not in used_v:
            take(v, t)
    by_seq = {(v["uca"], v["seq"]): v for v in vpos}
    agree = lambda v, t: NAME(v["vendor"]) == NAME(t["vendor"]) and _day(v["created_at"]) == _day(t["created_at"])
    for t in sorted(tpos, key=lambda t: t["id"]):          # 2. June numbering, checked
        v = by_seq.get((t["uca"], t["seq"]))
        if t["id"] not in used_t and v is not None and v["vid"] not in used_v and agree(v, t):
            take(v, t)
    loose = defaultdict(list)                              # 3. project + vendor + date
    for v in sorted(vpos, key=lambda v: v["vid"]):
        if v["vid"] not in used_v:
            loose[(v["uca"], NAME(v["vendor"]), _day(v["created_at"]))].append(v)
    for t in sorted(tpos, key=lambda t: t["id"]):
        cands = loose.get((t["uca"], NAME(t["vendor"]), _day(t["created_at"])))
        if t["id"] not in used_t and cands:
            take(cands.pop(0), t)
    return pairs, [v for v in vpos if v["vid"] not in used_v], [t for t in tpos if t["id"] not in used_t]


def infer_item_links(record_pairs: list, kinds: Callable[[dict], str]) -> dict[tuple[str, int], int]:
    """Work out which Velocity inventory item each Vision item became, from line usage.

    In each paired quote/PO, a Vision line that matches exactly one Velocity
    line of the same kind on description, price and quantity (as is or as June
    rounded it) is the same line, so its two inventory items are the same item:
    that is one vote. Each Vision item goes to the Velocity item most of its
    votes name. This holds when an item was renamed on one side, which pairing
    by name cannot see.

    Args:
        record_pairs: Paired ``(vision, velocity)`` quotes or POs.
        kinds: The line's kind (item type).

    Returns:
        ``(kind, Vision item id) -> Velocity item id``.
    """
    votes: dict[tuple, Counter] = defaultdict(Counter)
    for v, t in record_pairs:
        idx = defaultdict(list)
        for tl in t["lines"]:
            idx[(kinds(tl), TEXT(tl["description"]), MONEY(tl["unit_price"]))].append(tl)
        for vl in v["lines"]:
            if not vl.get("item_vid"):
                continue
            cands = [tl for tl in idx.get((kinds(vl), TEXT(vl["description"]), MONEY(vl["unit_price"])), [])
                     if same_quantity(vl["quantity"], tl["quantity"])]
            ids = {tl["item_id"] for tl in cands}
            if len(ids) == 1 and None not in ids:          # unambiguous: one Velocity item
                votes[(kinds(vl), vl["item_vid"])][ids.pop()] += 1
    links, claimed = {}, {}
    for k, c in sorted(votes.items(), key=lambda kv: -sum(kv[1].values())):   # strongest evidence first
        tid, n = c.most_common(1)[0]
        if n * 2 > sum(c.values()) and (k[0], tid) not in claimed:            # a clear majority, not yet taken
            links[k] = tid
            claimed[(k[0], tid)] = k
    return links


# --------------------------------------------------------------------------- #
# Lines
# --------------------------------------------------------------------------- #

def line_key(links: dict, kind: Callable, item_name: Callable, side: str) -> Callable[[dict], tuple]:
    """Item key of a line, the same for both sides when they hold the same item.

    A Vision line's item is its linked Velocity item when one was worked out;
    a Velocity line's item is its own id when some Vision item links to it.
    Anything else falls back to the item's name.

    Args:
        links: :func:`infer_item_links` output.
        kind: The line's kind.
        item_name: The line's item name (part number / inventory description).
        side: ``"vision"`` or ``"velocity"``.

    Returns:
        A function giving a line's key.
    """
    targets = {(k[0], tid) for k, tid in links.items()}
    if side == "vision":
        def key(l):
            tid = links.get((kind(l), l.get("item_vid")))
            return ("id", tid) if tid is not None else ("name", norm(item_name(l)))
    else:
        def key(l):
            linked = (kind(l), l.get("item_id")) in targets
            return ("id", l["item_id"]) if linked else ("name", norm(item_name(l)))
    return key


def pair_lines(vlines: list, tlines: list, kind: Callable, vkey: Callable, tkey: Callable, same: Callable):
    """Pair a quote's or PO's lines.

    Three passes, each on what the one before left over:
      1. same item, and ``same`` says the values match;
      2. same item, in order;
      3. same kind, in order (the item then shows as a differing field).
    "In order" is Vision id order against Velocity id order.

    Args:
        vlines, tlines: The Vision and Velocity lines of one paired record.
        kind: The line's kind (item type).
        vkey, tkey: Item key of a Vision / Velocity line (see :func:`line_key`).
        same: ``same(vision line, velocity line)`` -> True when the values match.

    Returns:
        ``(pairs, vision-only, velocity-only)``.
    """
    pairs, missing, extra = [], [], []
    by_vid, by_id = (lambda r: r.get("vid") or 0), (lambda r: r["id"])
    vk, tk = defaultdict(list), defaultdict(list)
    for l in vlines:
        vk[(kind(l), vkey(l))].append(l)
    for l in tlines:
        tk[(kind(l), tkey(l))].append(l)
    left_v, left_t = defaultdict(list), defaultdict(list)  # per kind, what passes 1-2 leave
    for k in set(vk) | set(tk):
        rest_t, rest_v = sorted(tk.get(k, []), key=by_id), []
        for v in sorted(vk.get(k, []), key=by_vid):        # pass 1: matching values
            hit = next((t for t in rest_t if same(v, t)), None)
            if hit is not None:
                pairs.append((v, hit)); rest_t.remove(hit)
            else:
                rest_v.append(v)
        pairs += list(zip(rest_v, rest_t))                 # pass 2: same item, in order
        left_v[k[0]] += rest_v[len(rest_t):]
        left_t[k[0]] += rest_t[len(rest_v):]
    for k in set(left_v) | set(left_t):                    # pass 3: same kind, in order
        vs, ts = sorted(left_v.get(k, []), key=by_vid), sorted(left_t.get(k, []), key=by_id)
        pairs += list(zip(vs, ts))
        missing += vs[len(ts):]
        extra += ts[len(vs):]
    return pairs, missing, extra


# --------------------------------------------------------------------------- #
# Quotes
# --------------------------------------------------------------------------- #

QUOTE_FIELDS = {"uca": TEXT, "created_at": DAY, "work_description": TEXT, "client_po_number": TEXT, "status": TEXT}
QUOTE_LINE_FIELDS = {"description": TEXT, "quantity": QTY, "unit_price": MONEY, "base_cost": MONEY,
                     "qty_fulfilled": QTY, "qty_pending": QTY}
LINE_DONE_FIELDS = ("qty_fulfilled", "qty_pending")       # move with invoicing, not with line edits


def compare_quotes(delta: Delta, vquotes: list, tquotes: list, links: dict, cutoff: date,
                   same_project: Optional[dict] = None) -> None:
    """Quotes and their lines.

    Args:
        delta: Where rows go.
        vquotes, tquotes: Vision and Velocity quotes.
        links: Inventory links (see :func:`infer_item_links`).
        cutoff: Day of the June import; Vision dates after it count as later changes.
        same_project: UCA number -> project identity (name + customer), so the
            twin check also looks in a project's other copy; default: the number.
    """
    pairs, missing, extra, clones = pair_quotes(vquotes, tquotes)
    ident = (lambda uca: same_project.get(uca, uca)) if same_project else (lambda uca: uca)
    later = lambda d: bool(d and _day(d) > cutoff)        # dated after the June import
    kind = lambda l: l["item_type"]
    vkey = line_key(links, kind, lambda l: l["ref"], "vision")
    tkey = line_key(links, kind, lambda l: l["ref"], "velocity")
    same = lambda v, t: (vkey(v) == tkey(t) and TEXT(v["description"]) == TEXT(t["description"])
                         and MONEY(v["unit_price"]) == MONEY(t["unit_price"])
                         and same_quantity(v["quantity"], t["quantity"]))

    nsame = 0
    lc = Counter()
    for v, t in pairs:
        ctx = f"{t['quote_number']} (WO {v['wo']})"
        v_later = later(v["revised"]) or later(v["entered"]) or any(later(l["entered"]) for l in v["lines"])
        t_edit = (t["current_version"] or 0) > 0 or t["invoices"] > 0
        diffs = field_diffs(v, t, QUOTE_FIELDS)
        for f, vv, tv in diffs:
            june = JUNE_RULE if f == "status" else None    # June closed every quote
            if f == "created_at" and _day(tv) == cutoff and vv is not None and _day(vv) != cutoff:
                june = JUNE_RULE                           # the import stamped its own time on undated WOs
            delta.add("quotes", DIFFERS, v["wo"], t["id"], ctx, f, vv, tv, cause(v_later, t_edit, june), t["uca"],
                      {"vision": v})

        lpairs, lmiss, lextra = pair_lines(v["lines"], t["lines"], kind, vkey, tkey, same)
        lines_edited = (t["item_list_version"] or 0) > 0
        revised = later(v["revised"])                      # a Vision revision rewrites the work order's lines
        line_later = lambda l: revised or later(l["entered"])
        line_diffs = 0
        for vl, tl in lpairs:
            lctx = f"{ctx} {vl['item_type']} {vl['ref'] or ''}"
            ld = field_diffs(vl, tl, QUOTE_LINE_FIELDS)
            if vkey(vl) != tkey(tl):                       # a different inventory item
                ld.insert(0, ("item", vl["ref"], tl["ref"]))
            line_diffs += bool(ld)
            for f, vv, tv in ld:
                if f in LINE_DONE_FIELDS:                  # fulfilment: June marked it done; invoicing moves it
                    june = JUNE_RULE if QTY(tl["qty_pending"]) == 0 else None
                    edit = lines_edited or t["invoices"] > 0
                elif f == "quantity":
                    june = JUNE_ROUNDING if QTY(tv) == june_quantity(vv) else None
                    edit = lines_edited
                else:
                    june, edit = None, lines_edited
                delta.add("quote lines", DIFFERS, vl["vid"], tl["id"], lctx, f, vv, tv,
                          cause(line_later(vl), edit, june), t["uca"], {"vision": vl, "parent": t["id"]})
        for vl in lmiss:
            delta.add("quote lines", MISSING, vl["vid"], None, f"{ctx} {vl['item_type']} {vl['ref'] or ''}",
                      cause_=cause(line_later(vl), lines_edited, None), project=t["uca"],
                      ref={"vision": vl, "parent": t["id"]})
        for tl in lextra:
            delta.add("quote lines", EXTRA, None, tl["id"], f"{ctx} {tl['item_type']} {tl['ref'] or ''}",
                      cause_=VELOCITY_EDIT if lines_edited else UNEXPLAINED, project=t["uca"])
        lc.update(vision=len(v["lines"]), velocity=len(t["lines"]), paired=len(lpairs),
                  same=len(lpairs) - line_diffs, differs=line_diffs, missing=len(lmiss), extra=len(lextra))
        nsame += not diffs and not line_diffs and not lmiss and not lextra

    natives = defaultdict(list)                            # quotes made in Velocity, for the twin check
    for t in extra:
        if not (t.get("legacy_id") or t["wo"]):
            natives[ident(t["uca"])].append(t)
    for v in missing:
        twins = [(n, why) for n in natives.get(ident(v["uca"]), []) if (why := twin_evidence(v, n))]
        delta.twins += [twin_row(v, n, why) for n, why in twins]
        note = f" (look-alike velocity quote: {', '.join(n['quote_number'] for n, _ in twins)})" if twins else ""
        delta.add("quotes", MISSING, v["wo"], None, f"WO {v['wo']} {v['uca']} {v['status']}{note}",
                  project=v["uca"], ref={"vision": v})
        lc.update(vision=len(v["lines"]), missing=len(v["lines"]))
    for t in extra:
        wo = t.get("legacy_id") or t["wo"]
        delta.add("quotes", EXTRA, None, t["id"], t["quote_number"] + (f" (WO {wo} not in vision)" if wo else ""),
                  project=t["uca"])
        lc.update(velocity=len(t["lines"]), extra=len(t["lines"]))
    changed_later = {r["velocity_key"] for r in delta.rows if r["level"] == "quotes" and r["cause"] in (VISION_LATER, BOTH)}
    changed_later |= {r["_ref"]["parent"] for r in delta.rows
                      if r["level"] == "quote lines" and r["cause"] in (VISION_LATER, BOTH) and r["_ref"]}
    for orig, c in clones:
        delta.copies.append({"project": c["uca"], "original": orig["quote_number"], "copy": c["quote_number"],
                             "vision work order": orig.get("legacy_id") or orig["wo"], "copy created": c["created_at"],
                             "copy status": c["status"], "copy invoices": c["invoices"],
                             "original changed in vision after june": orig["id"] in changed_later})
        delta.add("quotes", COPY, orig.get("legacy_id") or orig["wo"], c["id"],
                  f"{c['quote_number']} copies {orig['quote_number']}", project=c["uca"])
        lc.update(velocity=len(c["lines"]))
    delta.quote_pairs = pairs
    delta.tally("quotes", len(vquotes), len(tquotes), len(pairs), nsame, len(missing), len(extra))
    delta.counts["quote lines"].update(lc)


WO_REF = re.compile(r"W\s*/?\s*O\s*#?\s*:?\s*(\d{4,5})", re.I)    # "W/O#13692", "WO 13637"
QUOTE_REF = re.compile(r"\b([A-Z]{1,2}\d{4}-\d{4}-\d+)\b")       # "A3801-0001-3"


def twin_evidence(v: dict, n: dict) -> list[str]:
    """What suggests a Velocity-made quote is the same job as a Vision work order Velocity lacks.

    Evidence only; nothing is decided from it. A cross-reference is staff
    writing the other system's number into the description.

    Args:
        v: The Vision work order.
        n: A quote made in Velocity, in the same project.

    Returns:
        The evidence found (empty when none): "cross-reference", "same day",
        "<k> of <m> items shared" (only when at least half of a multi-line
        work order is shared, since one shared service item says little).
    """
    why = []
    own = re.sub(r"^\[WO \d+\]", "", n["work_description"] or "")    # the import's own tag is not evidence
    if str(v["wo"]) in WO_REF.findall(own) or n["quote_number"] in QUOTE_REF.findall(v["work_description"] or ""):
        why.append("cross-reference")
    if _day(n["created_at"]) == _day(v["created_at"]):
        why.append("same day")
    a = {(l["item_type"], norm(l["ref"])) for l in v["lines"]}
    shared = len(a & {(l["item_type"], norm(l["ref"])) for l in n["lines"]})
    if len(a) > 1 and shared * 2 >= len(a):
        why.append(f"{shared} of {len(a)} items shared")
    return why


def twin_row(v: dict, n: dict, why: list[str]) -> dict:
    """One report row: a Vision work order beside one look-alike Velocity quote."""
    a = {(l["item_type"], norm(l["ref"])): l for l in v["lines"]}
    b = {(l["item_type"], norm(l["ref"])): l for l in n["lines"]}
    same_qty = sum(1 for k in a.keys() & b.keys() if same_quantity(a[k]["quantity"], b[k]["quantity"]))
    strong = "cross-reference" in why or ("same day" in why and any("items shared" in w for w in why))
    text = lambda q: " ".join(re.sub(r"^\[WO \d+\]", "", q["work_description"] or "").split())[:120]
    return {
        "project": v["uca"], "vision WO": v["wo"], "vision created": v["created_at"], "vision status": v["status"],
        "vision lines": len(v["lines"]), "vision description": text(v),
        "velocity quote": n["quote_number"], "velocity quote id": n["id"], "velocity created": n["created_at"],
        "velocity status": n["status"], "velocity lines": len(n["lines"]), "velocity invoices": n["invoices"],
        "velocity description": text(n), "evidence": ", ".join(why), "items in both": len(a.keys() & b.keys()),
        "same quantity": same_qty, "only in vision": len(a.keys() - b.keys()),
        "only in velocity": len(b.keys() - a.keys()),
        "prediction": "likely the same job typed into both systems" if strong else "likely a separate job",
    }


# --------------------------------------------------------------------------- #
# Purchase orders
# --------------------------------------------------------------------------- #

PO_FIELDS = {"vendor": NAME, "created_at": DAY, "work_description": TEXT, "status": TEXT}
PO_LINE_FIELDS = {"description": TEXT, "quantity": QTY, "unit_price": MONEY, "qty_received": QTY, "qty_pending": QTY}


def compare_pos(delta: Delta, vpos: list, tpos: list, links: dict, cutoff: date,
                same_project: Optional[dict] = None) -> None:
    """Purchase orders and their lines.

    Args:
        delta: Where rows go.
        vpos, tpos: Vision and Velocity POs.
        links: Inventory links (see :func:`infer_item_links`).
        cutoff: Day of the June import.
        same_project: As for :func:`compare_quotes`.
    """
    later = lambda d: bool(d and _day(d) > cutoff)
    ident = (lambda uca: same_project.get(uca, uca)) if same_project else (lambda uca: uca)
    pairs, missing, extra = pair_pos(vpos, tpos)
    kind = lambda l: "part"
    name = lambda l: l["part_number"] or l["description"]
    vkey, tkey = line_key(links, kind, name, "vision"), line_key(links, kind, name, "velocity")
    same = lambda v, t: (vkey(v) == tkey(t) and TEXT(v["description"]) == TEXT(t["description"])
                         and MONEY(v["unit_price"]) == MONEY(t["unit_price"])
                         and same_quantity(v["vision_quantity"], t["quantity"]))
    nsame = 0
    lc = Counter()
    for v, t in pairs:
        ctx = f"{t['po_number']} (vision PO {v['vid']})"
        t_edit = (t["current_version"] or 0) > 0 or t["receivings"] > 0
        v_later = any(later(l["last_received"]) for l in v["lines"])
        diffs = field_diffs(v, t, PO_FIELDS)
        for f, vv, tv in diffs:
            june = JUNE_RULE if f == "status" and tv == "Closed" else None   # June closed every PO
            delta.add("pos", DIFFERS, v["vid"], t["id"], ctx, f, vv, tv, cause(v_later, t_edit, june), t["uca"],
                      {"vision": v})
        lpairs, lmiss, lextra = pair_lines(v["lines"], t["lines"], kind, vkey, tkey, same)
        line_diffs = 0
        for vl, tl in lpairs:
            lctx = f"{ctx} {name(vl) or ''}"
            ld = field_diffs(vl, tl, PO_LINE_FIELDS)
            if vkey(vl) != tkey(tl):
                ld.insert(0, ("item", name(vl), name(tl)))
            line_diffs += bool(ld)
            for f, vv, tv in ld:
                june = None
                if f == "quantity" and QTY(tv) == june_quantity(vl["vision_quantity"]):
                    june = JUNE_ROUNDING
                elif f in ("qty_received", "qty_pending"):
                    june = JUNE_RULE                       # June summed receipts and ignored received-all
                delta.add("po lines", DIFFERS, vl["vid"], tl["id"], lctx, f, vv, tv,
                          cause(later(vl["last_received"]), t_edit, june), t["uca"], {"vision": vl, "parent": t["id"]})
        for vl in lmiss:
            delta.add("po lines", MISSING, vl["vid"], None, f"{ctx} {name(vl) or ''}",
                      cause_=cause(False, t_edit, None), project=t["uca"], ref={"vision": vl, "parent": t["id"]})
        for tl in lextra:
            delta.add("po lines", EXTRA, None, tl["id"], f"{ctx} {name(tl) or ''}",
                      cause_=VELOCITY_EDIT if t_edit else UNEXPLAINED, project=t["uca"])
        lc.update(vision=len(v["lines"]), velocity=len(t["lines"]), paired=len(lpairs),
                  same=len(lpairs) - line_diffs, differs=line_diffs, missing=len(lmiss), extra=len(lextra))
        nsame += not diffs and not line_diffs and not lmiss and not lextra
    extra_by_project = defaultdict(list)
    for t in extra:
        extra_by_project[ident(t["uca"])].append(t)
    for v in missing:
        hint = [t for t in extra_by_project.get(ident(v["uca"]), []) if NAME(t["vendor"]) == NAME(v["vendor"])]
        for t in hint:                                     # same project + vendor: evidence for a person
            delta.po_twins.append({
                "project": v["uca"], "vision PO": v["number"], "vision created": v["created_at"], "vendor": v["vendor"],
                "vision status": v["status"], "vision lines": len(v["lines"]), "velocity PO": t["po_number"],
                "velocity created": t["created_at"], "velocity status": t["status"], "velocity lines": len(t["lines"]),
                "evidence": "same vendor" + (", same day" if _day(t["created_at"]) == _day(v["created_at"]) else "")})
        note = f" (look-alike velocity PO: {', '.join(t['po_number'] for t in hint)})" if hint else ""
        delta.add("pos", MISSING, v["vid"], None,
                  f"vision PO {v['number'] or v['vid']} {v['uca']} {v['vendor']} {v['status']}{note}",
                  project=v["uca"], ref={"vision": v})
        lc.update(vision=len(v["lines"]), missing=len(v["lines"]))
    for t in extra:
        delta.add("pos", EXTRA, None, t["id"], t["po_number"], project=t["uca"])
        lc.update(velocity=len(t["lines"]), extra=len(t["lines"]))
    delta.po_pairs = pairs
    delta.tally("pos", len(vpos), len(tpos), len(pairs), nsame, len(missing), len(extra))
    delta.counts["po lines"].update(lc)


def june_cutoff(tquotes: list, first_snapshot: Optional[datetime]) -> Optional[date]:
    """Estimate the day of the June import from the Velocity data.

    Imported quotes carry their Vision start dates, so the latest of them on or
    before Velocity's first recorded edit is the newest work order the import saw.

    Args:
        tquotes: Velocity quotes.
        first_snapshot: When Velocity recorded its first quote change, or None.

    Returns:
        The estimated day, or None when there are no imported quotes.
    """
    limit = _day(first_snapshot) or date.max
    days = [_day(t["created_at"]) for t in tquotes if t["wo"] and _day(t["created_at"])]
    days = [d for d in days if d <= limit]
    return max(days) if days else None


def run(v: dict, t: dict, cutoff: date) -> Delta:
    """Compare every level.

    Args:
        v: :func:`scripts.vision_delta.vision.load` output.
        t: :func:`scripts.vision_delta.velocity.load` output.
        cutoff: Day of the June import.

    Returns:
        The filled :class:`Delta`. ``v``'s quotes and POs are relabelled to
        Velocity project numbers (see :func:`relabel`).
    """
    d = Delta()
    compare_simple(d, "categories", v["categories"], t["categories"], lambda r: (r["type"], norm(r["name"])), {},
                   lambda r: f"{r['type']} {r['name']}", legacy=False)    # one Vision id can give two categories
    profiles_t = [p for p in t["profiles"] if p["type"] != "staff"]       # staff are Velocity-only by design
    profile_pairs = compare_simple(
        d, "customers and vendors", v["profiles"], profiles_t, lambda r: (r["type"], norm(r["name"])),
        {"pst": TEXT, "address": TEXT, "postal_code": TEXT}, lambda r: f"{r['type']} {r['name']}", legacy=False)
    compare_contacts(d, profile_pairs)

    # Inventory identity from usage on paired quotes and POs
    quote_pairs = pair_quotes(v["quotes"], t["quotes"])[0]
    links = infer_item_links(quote_pairs, lambda l: l["item_type"])
    po_links = infer_item_links(pair_pos(v["pos"], t["pos"])[0], lambda l: "part")
    for (k, vid), tid in po_links.items():
        links.setdefault(("part", vid), tid)               # POs add evidence for parts quotes never used
    by_kind = lambda kind: {vid: tid for (k, vid), tid in links.items() if k == kind}
    d.links = links

    compare_simple(d, "parts", v["parts"], t["parts"], lambda r: norm(r["part_number"]),
                   {"part_number": TEXT, "description": TEXT, "cost": MONEY, "markup_percent": MONEY,
                    "category": NAME, "vendor": NAME}, lambda r: r["part_number"], links=by_kind("part"))
    compare_simple(d, "labour", v["labour"], t["labour"], lambda r: norm(r["description"]),
                   {"description": TEXT, "hours": QTY, "rate": RATE, "markup_percent": MONEY, "category": NAME},
                   lambda r: r["description"], links=by_kind("labor"))
    compare_simple(d, "misc", v["misc"], [m for m in t["misc"] if not m["is_system_item"]],
                   lambda r: norm(r["description"]), {"description": TEXT, "unit_price": MONEY, "markup_percent": MONEY},
                   lambda r: r["description"], links=by_kind("misc"))
    compare_projects(d, v["projects"], t["projects"], cutoff)
    relabel(v, d.project_map)
    # A project entered twice in Velocity is one project for the twin check
    identity = {p["uca"]: (_name_key(p["name"]), NAME(p["customer"])) for p in t["projects"]}
    identity.update({d.project_map[p["uca"]]: (_name_key(p["name"]), NAME(p["customer"]))
                     for p in v["projects"] if d.project_map.get(p["uca"], "").startswith("vision ")})
    compare_quotes(d, v["quotes"], t["quotes"], links, cutoff, identity)
    po_part_links = {("part", vid): tid for (k, vid), tid in links.items() if k == "part"}
    compare_pos(d, v["pos"], t["pos"], po_part_links, cutoff, identity)
    find_duplicate_projects(d, t, cutoff)
    return d


def relabel(v: dict, project_map: dict[str, str]) -> None:
    """Put Vision quotes and POs under the Velocity number of their project (in place).

    Their own Vision number stays in ``vision_uca``. A second call is
    harmless: it maps from ``vision_uca`` again.
    """
    for rec in v["quotes"] + v["pos"]:
        rec.setdefault("vision_uca", rec["uca"])
        rec["uca"] = project_map.get(rec["vision_uca"], rec["vision_uca"])


# --------------------------------------------------------------------------- #
# Per-project review (one row per recent project, for a person to check)
# --------------------------------------------------------------------------- #

# Review columns: (heading, level, kind, which rows count, what is counted)
#   which: "status" = only status-field rows, "other" = every other field, None = all
#   count: "records" = distinct Velocity/Vision records, "rows" = rows
REVIEW_COUNTS = [
    ("quotes missing in velocity", "quotes", MISSING, None),
    ("quotes with a different status", "quotes", DIFFERS, "status"),
    ("quotes with other header differences", "quotes", DIFFERS, "other"),
    ("quote lines missing in velocity", "quote lines", MISSING, None),
    ("quote lines that differ", "quote lines", DIFFERS, None),
    ("quote lines only in velocity", "quote lines", EXTRA, None),
    ("quotes only in velocity", "quotes", (EXTRA, COPY), None),
    ("POs missing in velocity", "pos", MISSING, None),
    ("POs with a different status", "pos", DIFFERS, "status"),
    ("PO lines missing in velocity", "po lines", MISSING, None),
    ("PO lines that differ", "po lines", DIFFERS, None),
    ("POs only in velocity", "pos", EXTRA, None),
]
REVIEW_COLUMNS = (["project", "vision number", "name", "customer", "vision status", "vision date created", "velocity date created",
                   "in velocity", "vision quotes", "velocity quotes", "vision POs", "velocity POs"]
                  + [c[0] for c in REVIEW_COUNTS]
                  + ["project fields that differ", "missing work orders", "missing vision POs", "notes",
                     "checked", "checker notes"])


def _review_counts(rows: list[dict]) -> dict:
    """Count one project's difference rows into the review columns.

    Args:
        rows: The project's rows from :class:`Delta`.

    Returns:
        Heading -> number of distinct records (a quote with three differing
        fields counts once), plus the text columns.
    """
    out = {}
    for heading, level, kind, which in REVIEW_COUNTS:
        kinds = kind if isinstance(kind, tuple) else (kind,)
        hits = {(r["vision_key"], r["velocity_key"]) for r in rows      # one per record, not per field
                if r["level"] == level and r["kind"] in kinds
                and (which is None or (r["field"] == "status") == (which == "status"))}
        out[heading] = len(hits)
    pick = lambda level, kind: [r for r in rows if r["level"] == level and r["kind"] == kind]
    out["project fields that differ"] = ", ".join(r["field"] for r in pick("projects", DIFFERS))
    out["missing work orders"] = ", ".join(str(r["vision_key"]) for r in pick("quotes", MISSING))
    out["missing vision POs"] = ", ".join(str(r["vision_key"]) for r in pick("pos", MISSING))
    hints = [r["cause"] for r in pick("quotes", MISSING) + pick("pos", MISSING) if r["cause"]]
    out["notes"] = "; ".join(dict.fromkeys(hints))         # causes worth a look, each once
    return out


def project_review(delta: Delta, v: dict, t: dict, since: date) -> list[dict]:
    """One row per project created on or after ``since``, with what differs in it.

    Vision projects are picked by their start date (the date the project was
    created in Vision); projects that exist only in Velocity by Velocity's
    created date.

    Args:
        delta: The filled :class:`Delta` (rows carry their project).
        v: :func:`scripts.vision_delta.vision.load` output.
        t: :func:`scripts.vision_delta.velocity.load` output.
        since: First creation day to include.

    Returns:
        Review rows keyed by :data:`REVIEW_COLUMNS`, oldest project first.
    """
    pairs, missing, extra = pair_projects(v["projects"], t["projects"])   # as the projects level pairs
    by_project = defaultdict(list)
    for r in delta.rows:
        if r["project"]:
            by_project[r["project"]].append(r)
    count = lambda recs: Counter(r["uca"] for r in recs)
    vq, tq, vp, tp = count(v["quotes"]), count(t["quotes"]), count(v["pos"]), count(t["pos"])
    recent = lambda d: bool(_day(d) and _day(d) >= since)

    out = []
    sides = ([(vr, tr, "yes") for vr, tr in pairs] + [(vr, None, "missing") for vr in missing]
             + [(None, tr, "velocity only") for tr in extra])
    for vr, tr, present in sides:
        if not recent((vr or tr)["created_on"]):           # Vision's date when there is one
            continue
        rec = vr or tr
        uca = tr["uca"] if tr else delta.project_map.get(vr["uca"], vr["uca"])   # Velocity's number when paired
        out.append({
            "project": uca, "vision number": vr["uca"] if vr else None, "name": rec["name"], "customer": rec["customer"],
            "vision status": vr["status"] if vr else None,
            "vision date created": _day(vr["created_on"]) if vr else None,
            "velocity date created": _day(tr["created_on"]) if tr else None,
            "in velocity": present,
            "vision quotes": vq[uca], "velocity quotes": tq[uca], "vision POs": vp[uca], "velocity POs": tp[uca],
            **_review_counts(by_project.get(uca, [])),
            "checked": None, "checker notes": None,        # for the person checking
        })
    out.sort(key=lambda r: (r["vision date created"] or r["velocity date created"], r["project"]))
    return out
