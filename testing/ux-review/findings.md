# UC Velocity — UX Critique + Audit (2026-06-02)

Live multi-surface review against deployed Railway frontend (`https://frontend-production-11d4.up.railway.app`) and current `master` code. Driven via `chrome-devtools` MCP with Nielsen heuristic and WCAG 2.1 AA evaluation.

**Surfaces reviewed:** App shell, Projects landing, Project Details, Quote Editor, PO Editor, Inventory, Sign-in screen.

**Screenshots:** `testing/ux-review/01-09-*.png`

---

## Severity scale

- **P0 — Blocking.** Prevents task completion or breaks a major access path. Fix immediately.
- **P1 — Major.** Significant difficulty, or a WCAG AA violation. Fix before next release.
- **P2 — Minor.** Annoyance, workaround exists. Next pass.
- **P3 — Polish.** Nice-to-fix, no real user impact.

---

## Nielsen heuristic scorecard (whole product)

| # | Heuristic | Score | Key issue |
|---|---|---|---|
| 1 | Visibility of system status | 3 | Good: status badges, "(N)" counts in tabs, disabled-reason text on Create Invoice. Weak: no skeleton loading; "Loading..." centered text instead. |
| 2 | Match system / real world | 3 | Domain vocabulary is correct (UCA, UCSH, PMS, PO). Some inconsistency (Labour vs labor in code). |
| 3 | User control & freedom | 2 | Cannot open project rows by keyboard. Native `window.confirm()` for destructive actions. Floating action bar covers content. Unsaved-changes guard is good. |
| 4 | Consistency & standards | 2 | Section headers vary across Parts/Labour/Misc (different button sets). Quote vs PO Editor diverge on metadata layout. Two `<h1>` elements site-wide. |
| 5 | Error prevention | 3 | Strong: Client PO Number required-before-invoice prevention. Unsaved-changes guard. Edit/Commit/Discard pattern. Weak: native `confirm()` for project delete (cascades quotes/POs). |
| 6 | Recognition vs recall | 3 | Excellent empty state in Project Details with `⌘K` shortcut. Weak: empty section states ("No labour items yet") give no CTA. |
| 7 | Flexibility & efficiency | 2 | Cmd/Ctrl+K command palette in Project Details is a real win. Otherwise, no keyboard shortcuts; no bulk actions; no saved filters. |
| 8 | Aesthetic & minimalist | 2 | Cards-everywhere in Quote Editor metadata (6 stacked). 8 buttons in two section headers. Always-disabled "Set Markup" buttons. Customer column always same value. |
| 9 | Error recovery | 2 | Errors shown via `alert()` in many paths. Toast system exists but isn't used. |
| 10 | Help & documentation | 1 | No in-app help, no tooltips on most icons, no first-run guidance, no docs link. |

**Total: ~23/40 — Acceptable (significant work needed)**

---

## Audit dimension scorecard

| # | Dimension | Score | Key finding |
|---|---|---|---|
| 1 | Accessibility | 1 | Hundreds of unlabeled icon buttons, no `aria-sort`, no skip link, clickable `<tr>` with no keyboard handler, non-semantic VirtualizedTable. |
| 2 | Performance | 2 | 2,451 unvirtualized rows on Projects landing. N+1 invoice fetch in Project Details. Inventory IS virtualized — proves the pattern works elsewhere. |
| 3 | Responsive design | 0 | App essentially unusable below ~900px viewport. No sidebar collapse, no table responsiveness, primary CTAs clipped. |
| 4 | Theming | 4 | OKLCH color system, neutrals properly tinted, dark theme well-tuned (~7-8:1 contrast on muted-fg). Theme toggle works. Strong dimension. |
| 5 | Anti-patterns | 3 | Mostly clean. One side-stripe border violation. Cards-everywhere creeping. No gradient text, no glassmorphism, no hero metrics, no AI-slop tells. |

**Total: 10/20 — Acceptable (significant work needed)**

---

## P0 findings (5)

### P0-1. App is unusable below ~900px viewport

**Surfaces:** Projects landing, Project Details, Quote Editor, PO Editor.

At 375×812 (iPhone 13):
- Sidebar consumes ~50% of viewport (256px fixed).
- Projects table shows ONE column ("Project Name"); other 7 columns off-screen with no overflow affordance.
- "+ New Project" CTA clipped to "+ New Projec...".
- Page subtitle wraps to 4 lines.
- No hamburger / drawer pattern; sidebar never collapses.

**Code:** `frontend/src/App.tsx:577` — `<aside className="w-64 border-r bg-card flex flex-col">`. Hard-coded `w-64` with no `md:` / `lg:` collapse rules.

**WCAG:** 1.4.10 (Reflow).

**Fix:**
- Replace fixed-width sidebar with `<Sheet>` (shadcn drawer) on `< md:`.
- Hide non-essential table columns at `< lg:` (hide Customer when single-customer, hide UCSH#/Project Lead at narrower viewports, keep Project Name + Status + Actions).
- Or convert table to card-stack layout on `< md:` (each project as a Card).

**Suggested command:** `/impeccable adapt`

---

### P0-2. Hundreds of icon-only buttons have no accessible name

**Surfaces:** Projects landing, Inventory, Project Details, Quote Editor, PO Editor.

Audit found 15 unlabeled icon buttons in the visible viewport of Projects landing (pencil + trash per row × 7 rows + sort headers). Multiplied across ~1,000 active projects, ~3,541 parts, ~thousands of PO/quote line items, **this is a site-wide pattern, not isolated**.

`aria-label: null`, `title: null`, no text content. Screen reader users hear "button" with no context.

**WCAG:** 4.1.2 (Name, Role, Value).

**Files affected** (representative):
- `frontend/src/pages/ProjectsPage.tsx:331, 338` — edit & delete buttons
- `frontend/src/App.tsx:406, 409, 466, 469, 524, 527` — inventory rows
- `frontend/src/pages/ProjectDetailsPage.tsx` — quote/PO sidebar trash
- `frontend/src/components/editors/QuoteEditor.tsx` — metadata edit pencils
- `frontend/src/components/editors/POEditor.tsx` — same

**Fix:**
```tsx
<Button variant="ghost" size="sm" onClick={...} aria-label={`Delete project ${project.name}`}>
  <Trash2 className="h-4 w-4" />
</Button>
```

Use the row's identifier in the label (project name, part number, quote number) so screen reader users know what they're acting on.

**Suggested command:** `/impeccable harden`

---

### P0-3. Project rows cannot be opened by keyboard

**Surface:** Projects landing.

`<TableRow className="cursor-pointer hover:bg-muted/50" onClick={...}>` — click handler on `<tr>`, no `tabindex`, no `role="link"` or `role="button"`, no `onKeyDown`. Keyboard users can focus the per-row pencil and trash but cannot open the project.

Verified via DOM script: `firstRowReachable: false`, `firstRowDescendantReachable: true`.

**Code:** `frontend/src/pages/ProjectsPage.tsx:312-315`.

**WCAG:** 2.1.1 (Keyboard).

**Fix:** Replace the row-as-link with a real focusable + activatable link wrapping the row content, or:
```tsx
<TableRow
  role="button"
  tabIndex={0}
  onClick={() => onSelectProject(project.id)}
  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelectProject(project.id); } }}
  aria-label={`Open project ${project.name}`}
>
```

Better pattern: make Project Name a real `<Link>` inside the first cell, drop the row-level click handler entirely.

**Suggested command:** `/impeccable harden`

---

### P0-4. Projects landing renders 2,451 rows without virtualization

**Surface:** Projects landing.

`document.querySelectorAll('tr').length === 2451`. The entire projects list (active + archived + sub-rows for matched docs) is in the DOM. With 1,012+ archived projects on top of active ones, scroll performance degrades; initial paint is heavy.

Confusingly, `VirtualizedTable` already exists and is used on Inventory (3,541 parts renders only ~22 DOM rows). The pattern works — it's just not applied to Projects.

**Code:** `frontend/src/pages/ProjectsPage.tsx:266-394` uses shadcn `<Table>` directly. Inventory (`frontend/src/App.tsx:379`) uses `<VirtualizedTable>`.

**Fix:** Migrate Projects table to `VirtualizedTable`. Header is already separable from rows in that component. Sort/filter/search live above the table — only the row rendering changes.

Tabbable count drops from 4,917 to ~60-80, transitively fixing the no-skip-link problem (P1-7) for this surface.

**Suggested command:** `/impeccable optimize`

---

### P0-5. Sign-in screen has no branding, value prop, or context

**Surface:** Sign-in.

`frontend/src/App.tsx:555-572` renders **4 elements total**: H1 "UC Velocity", subtitle "ERP System", `<SignInButton>`, `<SignUpButton>`. No tagline, no product description, no logo (other than text), no help/support link, no terms.

First impression is "developer scaffolding."

Additionally, **Sign Up is exposed publicly** for what is internal company tooling. Users could create unauthorized accounts (Clerk gates further access, but the affordance still misleads).

**Fix:**
- Hero block: company logo (UCSH), product name, one-sentence value prop ("Quote and purchase order management for Upper Canada Specialty Hardware").
- Single Sign In primary CTA. Remove or disable Sign Up (or gate behind invite).
- Footer: small text "Need access? Contact your admin." + support email.
- Consider a screenshot/illustration of the dashboard for visual interest.

**Suggested command:** `/impeccable onboard`

---

## P1 findings (10)

### P1-6. Replace native `window.confirm()` and `alert()` with shadcn `AlertDialog` + toast

**Surfaces:** Projects landing, Inventory, Project Details, Quote Editor.

Native browser dialogs used for destructive confirmations and error notification despite `Toaster` and `AlertDialog` being available in the codebase. Project Details has already migrated (line 17-25 imports + `deleteConfirm` state); Projects landing and Inventory have not.

Occurrences:
- `frontend/src/pages/ProjectsPage.tsx:187` — `confirm("Are you sure you want to delete this project? All quotes and purchase orders will be deleted.")` — *cascading delete; this is the worst place to use a native confirm*
- `frontend/src/pages/ProjectsPage.tsx:192` — `alert(...)` for delete error
- `frontend/src/App.tsx:164, 173, 193` — `confirm(...)` for inventory deletes
- `frontend/src/App.tsx:169, 178, 199` — `alert(...)` for inventory delete errors
- `frontend/src/pages/ProjectDetailsPage.tsx:340, 358, 389` — `alert(...)` for create/delete errors

**WCAG:** 1.4.3 (native dialogs can't be themed for contrast). 2.4.6 (no descriptive headings). Native dialogs are also not screen-reader-friendly in the same way as ARIA dialogs.

**Fix:** Port the Project Details `deleteConfirm` pattern across the app. Replace each `alert(...)` with `toast({ variant: "destructive", description: ... })`.

**Suggested command:** `/impeccable harden`

---

### P1-7. Sortable column headers lack `aria-sort`

**Surface:** Projects landing.

All 7 sortable column headers (`Project Name`, `Customer`, `UCA #`, `UCSH #`, `Project Lead`, `Status`, `Created On`) use plain `<button>` with a chevron icon. The `<TableHead>` wrapping them has no `aria-sort="ascending"|"descending"|"none"`. Screen readers cannot announce which column is sorted or in which direction.

**Code:** `frontend/src/pages/ProjectsPage.tsx:269-303`.

**WCAG:** 1.3.1 (Info and Relationships), 4.1.2 (Name, Role, Value).

**Fix:**
```tsx
<TableHead aria-sort={sortBy === "name" ? (sortDir === "asc" ? "ascending" : "descending") : "none"}>
  <button type="button" onClick={() => toggleSort("name")} ...>
    Project Name {renderSortIcon("name")}
  </button>
</TableHead>
```

**Suggested command:** `/impeccable harden`

---

### P1-8. `VirtualizedTable` uses non-semantic `<div>` grid instead of `<table>`

**Surface:** Inventory (Parts, Labour, Misc tabs).

`tbodyRowCount: 0` in the rendered Inventory page — no semantic `<table>` elements. The component uses CSS Grid with `<div>` elements, but does not set `role="table"`, `role="row"`, `role="columnheader"`, `role="gridcell"`, `aria-rowcount`, or `aria-colcount`. Screen readers read each cell as a flat list of static text.

**Code:** `frontend/src/components/ui/virtualized-table.tsx`.

**WCAG:** 1.3.1 (Info and Relationships).

**Fix:** Add ARIA roles:
```tsx
<div role="table" aria-rowcount={items.length + 1} aria-colcount={cols}>
  <div role="row" aria-rowindex={1}>{header}</div>
  <div role="rowgroup">
    {visible.map((item, i) => (
      <div key={...} role="row" aria-rowindex={i + 2}>{renderRow(item)}</div>
    ))}
  </div>
</div>
```

Inner cells need `role="gridcell"` and the header cells `role="columnheader"`.

**Suggested command:** `/impeccable harden`

---

### P1-9. Two `<h1>` elements on every page

**All surfaces.**

`<h1>UC Velocity</h1>` in the sidebar (`App.tsx:581`) PLUS `<h1>` on each page (`ProjectsPage.tsx:218 — "Projects"`, `ProjectDetailsPage.tsx:521 — project name`, etc.).

Audit results: `h1Count: 2` on every surface checked.

**WCAG:** 1.3.1 — heading hierarchy. Screen readers using heading navigation see two top-level documents.

**Fix:** Change sidebar logo to `<p>` or `<div>` with appropriate visual styling; keep page-level `<h1>` as the only H1.

```tsx
// App.tsx:581
- <h1 className="text-xl font-bold">UC Velocity</h1>
+ <p className="text-xl font-bold" aria-label="UC Velocity, ERP System">UC Velocity</p>
```

**Suggested command:** `/impeccable harden`

---

### P1-10. Search inputs have no accessible labels

**Surfaces:** Projects landing, Inventory.

Both search inputs rely on `placeholder` for context — no `aria-label`, no `<label for>`. Placeholder text disappears when typed and is not a substitute for a label per WCAG 3.3.2 (Labels or Instructions).

**Code:**
- `frontend/src/pages/ProjectsPage.tsx:236-241`
- `frontend/src/App.tsx:357-362`

**Fix:**
```tsx
<Input
  aria-label="Search projects, POs, quotes, and vendors"
  placeholder="Search projects, POs, quotes, vendors..."
  ...
/>
```

**Suggested command:** `/impeccable clarify`

---

### P1-11. N+1 sequential invoice fetching in Project Details

**Surface:** Project Details.

`frontend/src/pages/ProjectDetailsPage.tsx:215-225` — `for (const quote of quotes) { await api.quotes.getInvoices(quote.id) }`. Sequential `await` in a `for` loop. For a project with 20 quotes, that's 20 sequential round-trips before the Invoices tab can render.

Visible cost: on slow networks, the Invoices count (`Invoices (N)`) in the tablist takes seconds to appear. The badge stays at "(0)" until all fetches complete.

**Fix:**
```ts
const fetchInvoices = async (quotes) => {
  try {
    const results = await Promise.all(quotes.map(q => api.quotes.getInvoices(q.id)));
    const allInvoices = results.flatMap((arr, i) => arr.map(inv => ({ ...inv, quoteId: quotes[i].id, quoteNumber: quotes[i].quote_number })));
    allInvoices.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
    setInvoices(allInvoices);
  } catch (err) { console.error("Failed to fetch invoices", err); }
};
```

Better: add a backend endpoint `GET /projects/:id/invoices` that returns all invoices for the project in one query.

**Suggested command:** `/impeccable optimize`

---

### P1-12. Currency formatting has no thousand separators

**Surfaces:** Inventory, Quote Editor, PO Editor — anywhere monetary values render.

`.toFixed(2)` produces `"1227.33"`, not `"1,227.33"`. Multiple places use this:
- `frontend/src/App.tsx:402, 404, 462, 464, 520, 522` (Inventory rows)
- `frontend/src/lib/pricing.ts` and `frontend/src/lib/format.ts` likely.

Example from Inventory screenshot: `$1227.33`, `$1343.51`, `$5033.68`, `$3771.38`. Hard to scan at a glance.

**Fix:** Create / use a single currency formatter:
```ts
// frontend/src/lib/format.ts
export const fmtCurrency = (n: number, currency = 'USD') =>
  new Intl.NumberFormat('en-CA', { style: 'currency', currency }).format(n);
```

Use throughout: `${fmtCurrency(part.cost)}` instead of `$${part.cost.toFixed(2)}`.

**Suggested command:** `/impeccable polish`

---

### P1-13. No skip link; Projects landing has 4,917 tabbable elements

**Surface:** Projects landing (mostly resolved when P0-4 lands, but still relevant elsewhere).

Audit found 4,917 tabbable elements on Projects landing (sort headers + all row-level pencil/trash buttons × 1,000+ rows). Keyboard users cannot bypass the table to reach pagination or other regions. No `<a href="#main-content">` skip link is present.

**WCAG:** 2.4.1 (Bypass Blocks).

**Fix:**
1. Add a visible-on-focus skip link as the first focusable element in `App.tsx`:
```tsx
<a href="#main-content" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:bg-background focus:p-2 focus:z-50 focus:ring-2">
  Skip to main content
</a>
```
2. Give `<main>` an `id="main-content"`.
3. P0-4 (virtualize Projects) naturally collapses tabbable count.

**Suggested command:** `/impeccable harden`

---

### P1-14. `aria-describedby` text reads "Add Add Part" (duplicated)

**Surface:** Quote Editor edit-mode.

In edit mode, section header add-buttons have `description="Add Add Part"`, `description="Add Add Labour"`, `description="Add Add Misc"`. Looks like the `aria-describedby` text is constructed as `\`Add ${label}\`` where `label` is already `"Add Part"` — producing the duplication.

Discovered via `take_snapshot` on the Quote Editor in edit mode.

**Code:** `frontend/src/components/editors/QuoteEditor.tsx` — search for `Add ${...}` in button rendering.

**Fix:** Pass the bare noun: `description="Add a part to this quote"`.

**Suggested command:** `/impeccable harden`

---

### P1-15. Clickable `<tr>` rows have inconsistent click target

**Surface:** Projects landing.

The row is clickable to open the project, but pencil + trash buttons inside the row `e.stopPropagation()` to prevent navigation. Hover state highlights the entire row even when the cursor is over a no-op cell, creating ambiguity about what is clickable. Combined with P0-3 (no keyboard handler), this is doubly confusing for some users.

Related: the matched-docs sub-row (`frontend/src/pages/ProjectsPage.tsx:349`) uses `border-l-2 border-l-primary/40` — a **side-stripe border**, which violates the impeccable absolute ban (decorative side-stripe on a list item).

**Code:** `frontend/src/pages/ProjectsPage.tsx:312-389`.

**Fix:**
- Make the Project Name cell a real `<Link>` and remove the row-level `onClick`.
- For matched-docs sub-row, replace side stripe with full thin border + indent, or with a leading icon (already has `↳` arrow).

**Suggested command:** `/impeccable layout`

---

## P2 findings (summarized)

These are real but lower priority. Bundle into follow-up work after P0/P1.

1. **Customer column on Projects landing always shows the same value** ("Upper Canada Specialty Hardware") — wastes ~15% of horizontal real estate. Convert to a filter chip or drop the column.
2. **Subtitle on Inventory is stale** — "Manage parts and labor items" but Miscellaneous tab is present.
3. **Audit Trail section surfaces "No history yet" even on Draft quotes** — visual noise.
4. **Cards-everywhere in Quote Editor metadata** — 6 stacked cards (Client PO Number, Cost Code, Work Description, Hardware Schedule Version, Markup Control). PO Editor's denser horizontal-row layout should be ported back.
5. **Floating action bar covers Cost Summary / Markup Control sections** in Quote/PO Editors. Add a bottom spacer or move the bar to a fixed footer with its own background.
6. **"Set Markup" buttons disabled but always visible** in Quote Editor sections — show only when Markup Control is enabled.
7. **Section header button inconsistency**: Labour has PMS $/% buttons, Misc has Parking/Travel, Parts has neither. Standardize via a single `+ Add ▼` split-button per section.
8. **"PMS $" and "PMS %" as two separate buttons** — segmented control or single button with type toggle.
9. **"Back to Projects" wastes a row** — replace with a chevron beside the project title (breadcrumb pattern).
10. **Empty section CTAs** — "No labour items yet" gives no guidance to add. Add "Click *Edit Quote* to add items" in view mode, or "+ Add Labour" CTA in the empty state in edit mode.
11. **Sign Up button visible** on splash for what is internal tooling.
12. **Project name "n/a" shows as H1** without defensive UI — orphan/legacy projects.

---

## P3 polish

1. Some part descriptions are placeholder ("LM10-001" / "LM10-001") — data quality, not UI.
2. Markup % renders as `233.33%` always — round to integer or 1 decimal where appropriate.
3. PO status combobox uses amber (warning color) for Draft — confirm intentional.
4. Floating Print/Clone vs Edit/Commit visual weight differs — Print/Clone look secondary, Edit looks primary, Commit looks primary. Three primary affordances on one bar.
5. The thin gray bar next to "Parts" heading in PO Editor — unidentified visual element, possibly fulfillment indicator without label.

---

## What's working (positive findings — preserve)

1. **Empty state in Project Details with `⌘K` shortcut** — best moment in the app. Teach-the-interface pattern.
2. **Disabled-reason inline on Create Invoice** — `aria-describedby="No items have pending quantities..."` plus visible amber warning text. Strong Nielsen #5 + #9.
3. **OKLCH color system** — neutrals properly tinted, dark theme passes WCAG AAA contrast on muted-foreground.
4. **Unsaved-changes navigation guard** in Project Details — `editorDirtyRef` + `AlertDialog` for confirmation. Real Nielsen #5 win.
5. **Cmd/Ctrl+K command palette** for global search inside a project.
6. **VirtualizedTable on Inventory** — proves the team can do it; just needs to be used elsewhere.
7. **`AlertDialog` in Project Details for delete confirmation** — the migration path is already proven; replicate.
8. **Tablist semantics** on Project Details — proper `role="tab"`, `aria-selected`, counts in label.
9. **shadcn/Radix primitives throughout** — strong foundation; ARIA-correct out of the box for most components.

---

## Patterns & systemic causes

- **Older code (App.tsx, ProjectsPage.tsx) hasn't adopted the patterns the newer code (ProjectDetailsPage.tsx, Quote/PO Editor) demonstrates.** Many P0/P1 findings are not "needs to be invented" but "needs to be ported across surfaces."
- **Icon-only buttons without `aria-label` is the single biggest a11y debt** — fix once via a `<IconButton>` wrapper that requires `aria-label` as a prop type.
- **Responsive design has been deferred entirely** — no `md:` / `lg:` / `sm:` Tailwind variants in the shell or major surfaces. Whole app design assumes ≥ 1280px desktop.

---

## Recommended commands (in order of impact)

1. `/impeccable adapt` — Make the app responsive below 900px (P0-1). Highest user-pain dimension.
2. `/impeccable harden` — Sweep `aria-label` onto icon buttons, replace native `confirm/alert`, add `aria-sort`, fix double-H1, add skip link, fix "Add Add Part" (P0-2, P0-3, P1-6 through P1-10, P1-13, P1-14).
3. `/impeccable optimize` — Virtualize Projects landing, fix N+1 invoice fetch (P0-4, P1-11).
4. `/impeccable onboard` — Redesign sign-in screen with branding/value prop (P0-5).
5. `/impeccable polish` — Currency formatting, P2/P3 cleanup, port PO Editor's metadata layout to Quote Editor.

---

**Re-run `/impeccable critique` after fixes** to see scores improve. Re-run `/impeccable audit` after `/impeccable harden` to verify WCAG compliance.
