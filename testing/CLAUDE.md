# UC Velocity — Agentic UI Testing Knowledge Base

This file is the operating manual for **simulated user testing**: an agent drives the deployed Railway frontend in a real Chrome browser via the `chrome-devtools` MCP and verifies outcomes from the rendered DOM. It is the end-to-end verification step that comes after unit tests and type checks.

**Load this file only when a testing session begins.** It is not needed during planning or normal implementation work — keeping it out of the default context budget is intentional.

---

## CRITICAL — UC Velocity holds live data

**There is no `/admin/reset-data` endpoint and there must not be one.** UC Velocity's Railway Postgres contains real customer, quote, and PO data. Do not drop the schema, truncate tables, or otherwise wipe state. The root project's older "data is dev-only — safe to drop/recreate" note is outdated as of 2026-05-11.

How to test under this constraint:
- Prefix test artifacts with `[TEST]` (e.g., a project named `[TEST] agent-quote-1`) so they are obvious to humans.
- Clean up everything the test created before declaring success — delete the test project, quote, PO, parts, profiles, etc.
- If you cannot reliably clean up because the feature does not expose a delete path, **stop and surface the gap** rather than leaving orphans behind.
- Never use destructive Alembic operations (drop column, drop table) as part of a test path.

---

## Deployment targets

| Surface | URL |
| --- | --- |
| Frontend (Railway) | `https://frontend-production-11d4.up.railway.app` |
| Backend (Railway)  | `https://uc-velocity-production.up.railway.app` |
| Clerk dev instance | `next-stork-41.clerk.accounts.dev` (decoded from `VITE_CLERK_PUBLISHABLE_KEY`) |

Backend Railway env vars relevant to testing:
- `TESTING_ENABLED=1` — gates the `/testing/*` routes. When unset or any other value, those routes return 404.
- `CLERK_SECRET_KEY` (`sk_test_...`) — backend uses this to mint sign-in tickets via the Clerk Admin API. Must match the Clerk instance that issued `VITE_CLERK_PUBLISHABLE_KEY`.
- `TESTING_ALLOWED_EMAILS` (optional, default `jayp@ucsh.com`) — comma-separated allowlist of emails for which tickets may be minted.

If either variable is missing on the backend service, set them with the Railway MCP (`mcp__Railway__set-variables`) and wait for the auto-redeploy.

---

## Auth recipe (passwordless sign-in)

Test user: **`jayp@ucsh.com`** (must already exist as a Clerk user in the `next-stork-41` dev instance). If it does not exist, create it via the Clerk dashboard or the Clerk MCP — do not change the allowlist to bypass.

Steps from a fresh chrome-devtools session:

1. **Probe `/testing/status`** — confirm `testing_enabled: true` and `clerk_configured: true` before doing anything else. If either is false, fix the env var via Railway MCP first.

   ```
   GET https://uc-velocity-production.up.railway.app/testing/status
   ```

2. **Mint a sign-in ticket.** Either with `WebFetch`, `evaluate_script` inside the browser (CORS is permitted between the two Railway services), or `mcp__chrome-devtools__navigate_page` directly to the URL — the response is JSON.

   ```
   GET https://uc-velocity-production.up.railway.app/testing/clerk-sign-in?email=jayp@ucsh.com
   ```

   Response:

   ```json
   {
     "ticket": "<long-token>",
     "ticket_url_query": "?__clerk_ticket=<long-token>",
     "user_id": "user_...",
     "expires_at": 1234567890
   }
   ```

3. **Navigate Chrome to the frontend with the ticket appended.** Clerk's frontend SDK is *supposed* to consume `__clerk_ticket` automatically on load, but as of 2026-06-02 this auto-consumption is unreliable — the SDK loads, no sign-in request fires, and `window.Clerk.user` stays `null`.

   ```
   https://frontend-production-11d4.up.railway.app/?__clerk_ticket=<long-token>
   ```

   **If the page lands on the signed-out splash (Sign In / Sign Up buttons visible), sign in explicitly via the SDK using `evaluate_script`:**

   ```js
   async () => {
     const r = await fetch("https://uc-velocity-production.up.railway.app/testing/clerk-sign-in?email=jayp@ucsh.com", { cache: "no-store" });
     const { ticket } = await r.json();
     const signIn = await window.Clerk.client.signIn.create({ strategy: "ticket", ticket });
     await window.Clerk.setActive({ session: signIn.createdSessionId });
     return { userId: window.Clerk.user?.id, sessionId: window.Clerk.session?.id };
   }
   ```

   Then `navigate_page` to `/projects` (or any signed-in route) so the React `<Show when="signed-in">` shell re-renders.

4. **Take a snapshot.** You should see the signed-in shell (sidebar with Projects/Profiles/Inventory/Reports/Settings/Migration, plus `UserButton` in the top-right).

Tickets are **single-use and expire in 10 minutes** — mint a fresh one if a flow re-mounts ClerkProvider or you wait too long.

⚠️ **WebFetch caches responses for ~15 min.** If you call WebFetch on the sign-in URL twice within that window, the second call returns the *same* (already-consumed) ticket. Use `evaluate_script`-with-`fetch` (as above) or `cache: "no-store"` inside the browser to guarantee freshness.

---

## App architecture (relevant to testing)

The frontend is a **single-page view-state machine**, not a router. `frontend/src/App.tsx` holds `useState<AppView>` with these views:

| View | Reached from |
| --- | --- |
| `projects` (default) | Sidebar → Projects |
| `project-details` | Click a project row, or click a search-result chip for a quote/PO |
| `profiles` | Sidebar → Profiles |
| `inventory` | Sidebar → Inventory |
| `reports` | Sidebar → Reports |
| `settings` | Sidebar → Settings |
| `migration` | Sidebar → Migration |

Implications for testing:
- **No URL changes** when navigating. `take_snapshot` is the only reliable way to confirm you are on a given view — do not rely on `location.pathname`.
- **Pages are lazy-loaded** (`React.lazy` + `Suspense` for Profiles, ProjectDetails, Reports, Settings, Migration). The first click on each sidebar item shows "Loading…" briefly. Use `wait_for` (text disappears) before snapshotting again.
- **Project search term and the selected project survive drill-in.** Re-entering the projects list returns you to the same scroll position.

### Inventory module (the biggest editor surface lives here-adjacent)
- Three tabs: Parts, Labour, Miscellaneous. Each has Add/Edit/Delete with a shadcn `Dialog`.
- Delete uses the **native `window.confirm`** (see `App.tsx` lines 116, 125, 145). You must register a `handle_dialog` before clicking the trash icon — otherwise the MCP call hangs while the browser blocks on the confirm.

### Project Details / Quote Editor
- The largest implementation surface: `QuoteEditor.tsx` (~3800 lines). Three modes: view, edit, invoicing.
- Edits are **staged in `Map`/`Set` structures and committed on one button press.** Do not assume an edit is persisted until you see the commit succeed.
- Version conflict detection: opening edit mode captures `current_version`; if another client bumps it before commit, the commit returns a 409 and the editor offers a refresh.

### Project Details / PO Editor
- Mirrors QuoteEditor but two item types only (part / misc — no labor).
- Edit mode is **only available for `Draft` POs that have no receivings.** If you need to test the editor, create a fresh Draft PO via the test path; do not switch a real-state PO to Draft.

---

## shadcn / Radix-specific gotchas

The reference testing kit was MUI-based. UC Velocity uses **shadcn/ui on top of Radix Primitives**, so the surface behaves differently:

| Pattern | Behavior | Workaround |
| --- | --- | --- |
| `Dialog`, `AlertDialog`, `Popover`, `Select`, `DropdownMenu` | All portal into `document.body`, so they are not children of the trigger in the DOM. | Always `take_snapshot` **after** opening any overlay — references from the pre-open snapshot won't include the dialog. |
| `Select` (`@radix-ui/react-select`) | Options render in a portal; `click` on the trigger does not focus an option. | Open the select, snapshot, then click the option by its visible label or `uid` from the new snapshot. |
| `Tabs` | Tab contents are rendered in the DOM only when active (default), so you cannot find an inactive tab's controls in the snapshot. | Click the tab first, then snapshot. |
| `Toast` (`@radix-ui/react-toast`) | Toast appears in a portal and auto-dismisses on a timer (default ~5s). | Snapshot immediately after the action; do not rely on the toast being there a few seconds later. |
| Native `window.confirm` / `window.alert` | Used for delete confirmations (`App.tsx` 116/125/145) and in some error paths (`App.tsx` 121, 130, 151). They block every MCP call until dismissed. | Always set up `handle_dialog` before triggering an action that may emit a confirm/alert. |
| Date picker (`react-day-picker`) | Renders a grid of `button` elements with `aria-label` like `"Friday, May 1st, 2026"`. | Match by `aria-label`, not by visible day number — multiple months in view share day numbers. |
| `SearchableSelect` (cmdk-based) | Free-text typing filters the list; arrow keys navigate; Enter selects. | Use `fill` on the input then `press_key Enter`, rather than clicking a row — clicking can race with the filter render. |

---

## Tooling at a glance

| MCP | Use for |
| --- | --- |
| `chrome-devtools` | All browser interaction. Snapshot, click, fill, navigate, screenshot, console messages, network requests, performance traces. |
| `Railway` | Probe and set backend env vars (`TESTING_ENABLED`, `CLERK_SECRET_KEY`), tail logs (`mcp__Railway__get-logs` with `logType: deploy`), check deployment status. |
| `clerk` | When available: create/list/delete Clerk users in the dev instance, troubleshoot the test-user state without leaving the agent. Expect an OAuth handshake on first tool call. |
| `auggie` (codebase retrieval) | Look up how a feature works before driving it — do not grep around blindly. |

For backend log inspection while a test runs, prefer `mcp__Railway__get-logs` with `lines: 100` and a `filter` like `@status:500` or `testing` — full streaming is rarely needed.

---

## Network and console hygiene

Before declaring a test passed, also check:
- `list_console_messages` — flag any `error` or `warning` that was not present on a clean reload. The app emits some expected dev warnings (React 19 strict-mode double-render in `useEffect`); ignore those, but anything mentioning `Failed to fetch`, `404`, `500`, or a stack trace is a real finding.
- `list_network_requests` — filter for non-2xx. Pay attention to the request body of any failed `POST`/`PATCH`/`DELETE` since those are the staged-changes commits.

---

## When to update this file

Update `testing/CLAUDE.md` whenever you:
- discover a new gotcha that cost you more than a couple of tool calls to figure out
- learn the canonical click path for a flow that was previously underspecified here
- find that an existing instruction is now wrong (e.g., a portaled component moved, a button label changed, a confirm dialog became a Radix `AlertDialog`)
- add or remove an env var on the backend that the agent should know about

Keep the updates short and concrete. The goal is "next session is faster" not "comprehensive documentation."

---

## Starter journey — Projects list smoke test

A 1-minute round-trip that validates auth + the default landing surface. Do this on every cold session before driving any real feature.

1. Probe `/testing/status`. Both flags must be `true`.
2. Mint a ticket for `jayp@ucsh.com`.
3. Navigate `https://frontend-production-11d4.up.railway.app/?__clerk_ticket=...`.
4. `wait_for` text `Projects` (sidebar) to appear. `take_snapshot`.
5. Confirm the snapshot includes: sidebar with all six nav items, a `UserButton` in the top-right, and a project list (or an empty-state). If the list is empty when it should not be, the backend is likely returning 401/403 — check `list_network_requests` for the `GET /projects/...` call.
6. No further actions, no cleanup. This is a read-only journey.

If this journey fails, **stop** and diagnose before attempting feature-specific tests — every downstream flow depends on it.
