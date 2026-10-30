import type { ArchivedProjectEntry } from "@/types"

/** A sortable column of the archived-projects list (#326). */
export type ArchiveSortKey = "uca" | "name" | "customer" | "last_activity" | "work_orders" | "pos"

/** The browse controls: search text, the two filters, and the sort. */
export interface BrowseOpts {
  query?: string
  customer?: string
  year?: string
  sortBy: ArchiveSortKey
  sortDir: "asc" | "desc"
}

/**
 * The four-digit year of an ISO-ish date string.
 * @param v - "2011-05-01" or "2011-05-01 00:00:00".
 * @returns "2011", or "" when absent.
 */
export function yearOf(v: string | null | undefined): string {
  return v ? String(v).slice(0, 4) : ""
}

/**
 * Distinct customer names present in the rows, sorted, blanks dropped.
 * @param rows - The archived rows.
 * @returns Unique customer names for the filter dropdown.
 */
export function distinctCustomers(rows: ArchivedProjectEntry[]): string[] {
  const set = new Set<string>()
  for (const r of rows) if (r.customer) set.add(r.customer)         // skip null/empty customers
  return [...set].sort((a, b) => a.localeCompare(b))
}

/**
 * Distinct years of last activity present in the rows, newest first.
 * @param rows - The archived rows.
 * @returns Unique years for the filter dropdown.
 */
export function distinctYears(rows: ArchivedProjectEntry[]): string[] {
  const set = new Set<string>()
  for (const r of rows) {
    const y = yearOf(r.last_activity)
    if (y) set.add(y)                                              // skip rows with no activity date
  }
  return [...set].sort((a, b) => b.localeCompare(a))               // newest year first
}

/**
 * Filter and sort the archived rows for the browse-all list (#326).
 * The search term matches UCA, name or customer (case-insensitive); the customer
 * and year filters are exact; UCA breaks ties so the order is stable.
 * @param rows - Every archived row loaded from the table.
 * @param opts - Search text, filters, and the sort column/direction.
 * @returns A new, filtered, sorted array.
 */
export function browseArchived(rows: ArchivedProjectEntry[], opts: BrowseOpts): ArchivedProjectEntry[] {
  const term = (opts.query || "").trim().toLowerCase()             // what was typed, normalised
  const filtered = rows.filter((r) => {
    if (term && ![r.uca, r.name, r.customer].some((v) => String(v ?? "").toLowerCase().includes(term))) return false
    if (opts.customer && (r.customer || "") !== opts.customer) return false   // exact customer match
    if (opts.year && yearOf(r.last_activity) !== opts.year) return false      // exact year match
    return true
  })
  const dir = opts.sortDir === "asc" ? 1 : -1                      // flip the comparator for descending
  return [...filtered].sort((a, b) => {
    const k = opts.sortBy
    let cmp: number
    if (k === "work_orders" || k === "pos") cmp = (a[k] ?? 0) - (b[k] ?? 0)   // numeric columns
    else cmp = String(a[k] ?? "").localeCompare(String(b[k] ?? ""))           // text columns
    if (cmp === 0) cmp = String(a.uca).localeCompare(String(b.uca))           // stable tiebreak on UCA
    return dir * cmp
  })
}
