import { useState, useEffect, useCallback, useRef } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api } from "@/api/client"
import { toast } from "@/hooks/use-toast"
import type { ReopenableQuote } from "@/types"
import { Unlock, Search, ChevronLeft, ChevronRight, Loader2 } from "lucide-react"
import { formatDate } from "@/lib/format"

const PAGE_SIZE = 50 // rows fetched per page from the server
const REOPEN_CHUNK = 500 // must stay <= the backend's BULK_REOPEN_MAX per call

/**
 * Global cross-project tool to reopen migrated quotes stuck at "Closed" (Issue #164).
 *
 * The UC Vision importer marked every migrated line fully fulfilled, so every migrated
 * quote computes to Closed - including jobs that are still ongoing. This page lists the
 * quotes currently eligible to reopen (GET /quotes/reopenable), lets staff search and
 * tick the ones that are actually ongoing, and reopens them in bulk
 * (POST /quotes/reopen-bulk). Selecting a quote resets its fulfillment so it recomputes
 * to Work Order (has a client PO) or Draft. Reopened quotes fall out of the list.
 *
 * Access: admin-only. App.tsx gates the route/nav via useIsAdmin (client-side, matching the
 * Migration surface); the per-project and per-quote reopen paths stay open to all staff.
 *
 * @returns The reopen-migrated-quotes admin view.
 */
export function ReopenMigratedQuotesPage() {
  // Current page of eligible quotes + the unfiltered total (for the "x of y" footer).
  const [rows, setRows] = useState<ReopenableQuote[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(true)

  // Search box + its debounced value (the value actually sent to the server).
  const [search, setSearch] = useState("")
  const [debouncedSearch, setDebouncedSearch] = useState("")

  // Ticked quote ids - a Set so selection survives paging - plus an in-flight guard.
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)

  // Monotonic request id: only the latest fetch is allowed to apply its result, so
  // overlapping fetches (e.g. a search-while-paged double-fire) can't land out of order.
  const reqSeq = useRef(0)

  // Debounce the search input so typing doesn't fire a request per keystroke.
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 300) // settle after 300ms idle
    return () => clearTimeout(t) // cancel a pending settle on the next keystroke
  }, [search])

  // A new search term always restarts at the first page.
  useEffect(() => {
    setOffset(0)
  }, [debouncedSearch])

  /**
   * Load the current page of eligible quotes from the server.
   *
   * Reads the paginated /quotes/reopenable endpoint for the active offset and search
   * term, replacing the visible rows and total. Errors surface as a toast.
   *
   * @returns Promise that resolves once the page state is updated.
   */
  const fetchPage = useCallback(async () => {
    const seq = ++reqSeq.current // claim this as the newest in-flight request
    setLoading(true) // show the loading row while the request is out
    try {
      const res = await api.quotes.listReopenable({
        offset,
        limit: PAGE_SIZE,
        search: debouncedSearch || undefined, // omit empty search entirely
      })
      if (seq !== reqSeq.current) return // a newer request started -> drop this stale result
      setRows(res.items) // this page's rows
      setTotal(res.total) // total matches across all pages
    } catch (err) {
      if (seq !== reqSeq.current) return // stale failure -> ignore
      toast({
        variant: "destructive",
        title: "Failed to load quotes",
        description: err instanceof Error ? err.message : "Could not load reopenable quotes.",
      })
    } finally {
      if (seq === reqSeq.current) setLoading(false) // only the latest request clears loading
    }
  }, [offset, debouncedSearch])

  // Refetch whenever the page or the search term changes.
  useEffect(() => {
    fetchPage()
  }, [fetchPage])

  /**
   * Add or remove one quote id from the ticked selection.
   *
   * @param id - The quote id whose checkbox was toggled.
   */
  const toggle = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev) // clone so React re-renders
      if (next.has(id)) next.delete(id) // untick -> drop
      else next.add(id) // tick -> add
      return next
    })
  }

  // Ids on the visible page, and whether every one of them is already ticked.
  const pageIds = rows.map((r) => r.id)
  const allOnPageSelected = pageIds.length > 0 && pageIds.every((id) => selectedIds.has(id))

  /**
   * Tick or untick every row on the current page at once.
   *
   * @param checked - True to select all visible rows, false to deselect them.
   */
  const toggleAllOnPage = (checked: boolean) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (checked) pageIds.forEach((id) => next.add(id)) // select the visible page
      else pageIds.forEach((id) => next.delete(id)) // clear the visible page
      return next
    })
  }

  /**
   * Reopen every ticked quote, then refresh the page.
   *
   * Splits the selection into chunks no larger than the backend's per-call cap and
   * reopens each chunk, aggregating the reopened/skipped counts for one summary toast.
   * Reopened quotes stop being eligible, so they drop off the refreshed list.
   *
   * @returns Promise that resolves once all chunks are processed and the page reloads.
   */
  const reopenSelected = async () => {
    const ids = [...selectedIds] // Set -> array for chunking
    if (ids.length === 0) return // nothing ticked
    setBusy(true) // guard the button
    let reopened = 0
    let skipped = 0
    try {
      for (let i = 0; i < ids.length; i += REOPEN_CHUNK) {
        const chunk = ids.slice(i, i + REOPEN_CHUNK) // one <= 500-id batch
        const res = await api.quotes.reopenBulk(chunk)
        reopened += res.reopened_count // tally across chunks
        skipped += res.skipped_count
      }
      toast({
        title: "Reopen complete",
        description: `Reopened ${reopened}${skipped ? `, skipped ${skipped}` : ""}.`,
      })
      setSelectedIds(new Set()) // clear the (now-reopened) selection
      // Reopened rows drop out of the eligible list, shrinking the total. Return to page 1 so
      // the offset can't land past the new total (which would show a false empty state).
      if (offset === 0) {
        await fetchPage() // already on page 1: setOffset won't refire the effect, so refetch here
      } else {
        setOffset(0) // moving to page 1 refires the fetch effect at offset 0
      }
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Reopen failed",
        description: err instanceof Error ? err.message : "Could not reopen the selected quotes.",
      })
    } finally {
      setBusy(false)
    }
  }

  // Footer "x-y of total" bounds for the current page.
  const pageStart = total === 0 ? 0 : offset + 1
  const pageEnd = Math.min(offset + PAGE_SIZE, total)

  return (
    <div className="p-6 space-y-4">
      {/* Heading + explanation of why these quotes look Closed. */}
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Unlock className="h-6 w-6" /> Reopen Migrated Quotes
        </h1>
        <p className="text-muted-foreground max-w-3xl">
          Quotes imported from UC Vision came in fully fulfilled, so they all show as Closed.
          Search for the jobs that are still ongoing, tick them, and reopen them so they become
          open Work Orders again. Each reopen is recorded in the quote's audit trail.
        </p>
      </div>

      {/* Search (server-side) on the left; selection count + reopen action on the right. */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            className="pl-9"
            placeholder="Search by project or customer..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="ml-auto flex items-center gap-3">
          <span className="text-sm text-muted-foreground">{selectedIds.size} selected</span>
          <Button onClick={reopenSelected} disabled={busy || selectedIds.size === 0} className="gap-2">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Unlock className="h-4 w-4" />}
            {busy ? "Reopening..." : `Reopen ${selectedIds.size} selected`}
          </Button>
        </div>
      </div>

      {/* Results table: checkbox + guidance columns (projected status is the key hint). */}
      <div className="border rounded-md overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr className="text-left">
              <th className="w-10 p-2">
                <input
                  type="checkbox"
                  className="h-4 w-4 accent-primary"
                  aria-label="Select all on this page"
                  checked={allOnPageSelected}
                  onChange={(e) => toggleAllOnPage(e.target.checked)}
                />
              </th>
              <th className="p-2 font-medium">Quote</th>
              <th className="p-2 font-medium">Project</th>
              <th className="p-2 font-medium">Customer</th>
              <th className="p-2 font-medium">Created</th>
              <th className="p-2 font-medium text-right">Lines</th>
              <th className="p-2 font-medium">On reopen</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={7} className="p-8 text-center text-muted-foreground">
                  Loading…
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={7} className="p-8 text-center text-muted-foreground">
                  {debouncedSearch
                    ? "No migrated quotes match your search."
                    : "No migrated quotes are eligible to reopen."}
                </td>
              </tr>
            ) : (
              rows.map((r) => (
                <tr key={r.id} className="border-t hover:bg-muted/30">
                  <td className="p-2">
                    <input
                      type="checkbox"
                      className="h-4 w-4 accent-primary"
                      aria-label={`Select quote ${r.quote_number}`}
                      checked={selectedIds.has(r.id)}
                      onChange={() => toggle(r.id)}
                    />
                  </td>
                  <td className="p-2 font-medium">{r.quote_number}</td>
                  <td className="p-2">
                    {r.uca_project_number}
                    <span className="text-muted-foreground"> · {r.project_name}</span>
                  </td>
                  <td className="p-2">{r.customer_name ?? "—"}</td>
                  <td className="p-2">{formatDate(r.created_at)}</td>
                  <td className="p-2 text-right">{r.line_item_count}</td>
                  <td className="p-2">{r.projected_status}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Page position + prev/next controls. */}
      <div className="flex items-center justify-between text-sm">
        <span className="text-muted-foreground">
          {pageStart}–{pageEnd} of {total}
        </span>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={offset === 0 || loading}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
          >
            <ChevronLeft className="h-4 w-4" /> Prev
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={offset + PAGE_SIZE >= total || loading}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            Next <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  )
}
