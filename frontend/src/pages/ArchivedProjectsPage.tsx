import { useEffect, useMemo, useState } from "react"
import { useNavigate } from "react-router-dom"
import {
  Archive,
  ArchiveRestore,
  ChevronLeft,
  Printer,
  Search,
  ArrowUpDown,
  ChevronUp,
  ChevronDown,
  Loader2,
} from "lucide-react"
import { api } from "@/api/client"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { VirtualizedTable, headerCellClass, cellClass } from "@/components/ui/virtualized-table"
import { SearchableSelect } from "@/components/ui/searchable-select"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { toast } from "@/hooks/use-toast"
import { formatCurrency } from "@/lib/format"
import { browseArchived, distinctCustomers, distinctYears } from "@/lib/archive-browse"
import type { ArchiveSortKey } from "@/lib/archive-browse"
import type {
  ArchivedLine,
  ArchivedProject,
  ArchivedProjectEntry,
  ArchivedWorkOrder,
  Project,
  Quote,
  QuoteLineItem,
} from "@/types"

/** Responsive grid for the archived list: UCA + name + actions on mobile, more columns as it widens. */
const ARCHIVE_GRID_COLS =
  "grid-cols-[5rem_minmax(0,1fr)_auto] " +
  "sm:grid-cols-[5rem_minmax(0,1.4fr)_6rem_5rem] " +
  "lg:grid-cols-[5rem_minmax(0,1.6fr)_minmax(0,1.2fr)_7rem_5rem_5rem]"

/**
 * A stored date-time as a plain day.
 * @param v - ISO-like text ("2005-03-09 00:00:00").
 * @returns "2005-03-09", or a dash.
 */
function day(v: string | null | undefined): string {
  return v ? v.slice(0, 10) : "—"
}

/**
 * A money amount, or a dash when there is none.
 * @param v - The amount.
 * @returns Formatted currency.
 */
function money(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : formatCurrency(v)
}

/** A raw archived value as a number, treating null/undefined as 0. */
function num(v: unknown): number {
  return v == null ? 0 : Number(v)
}

/** A raw archived value as a number, or undefined when absent. */
function numOrU(v: unknown): number | undefined {
  return v == null ? undefined : Number(v)
}

/** A raw archived value as a string, or undefined when absent. */
function strOrU(v: unknown): string | undefined {
  return v == null ? undefined : String(v)
}

/**
 * Rebuild one quote line item from its raw archived row for printing.
 * The archive stores the line's own pricing (base_cost + markup, or unit_price),
 * so the shared pricing math reaches an exact total; the catalog objects
 * (part/labor/miscellaneous) are not archived, so the printed description falls
 * back to the stored description, exactly as a migrated line already prints.
 * @param l - The raw ``quote_line_items`` row.
 * @returns A QuoteLineItem the PDF and pricing helpers accept.
 */
function toLineItem(l: Record<string, unknown>): QuoteLineItem {
  return {
    id: num(l.id),
    quote_id: num(l.quote_id),
    item_type: (l.item_type as QuoteLineItem["item_type"]) ?? "part",
    description: strOrU(l.description),
    description_override: strOrU(l.description_override),
    quantity: num(l.quantity),
    unit_price: numOrU(l.unit_price),
    qty_pending: num(l.qty_pending),
    qty_fulfilled: num(l.qty_fulfilled),
    is_pms: Boolean(l.is_pms),
    pms_percent: numOrU(l.pms_percent),
    base_cost: numOrU(l.base_cost),
    markup_percent: numOrU(l.markup_percent),
  }
}

/**
 * Rebuild one Velocity quote from its raw archived rows for printing.
 * @param row - The raw ``quotes`` row.
 * @param lines - Every archived ``quote_line_items`` row (filtered here by quote id).
 * @param uca - The project's UCA number, for the quote number fallback.
 * @returns A Quote the QuotePDF document accepts.
 */
function toQuote(row: Record<string, unknown>, lines: Record<string, unknown>[], uca: string): Quote {
  const seq = num(row.quote_sequence)                                  // per-project sequence
  const version = num(row.current_version) || 1                        // version, default 1
  // The printed number uses the visible version (line-item changes only, #202), as the live app does
  const visible = row.item_list_version != null ? num(row.item_list_version) : version
  const stored = typeof row.quote_number === "string" ? row.quote_number : ""
  const quoteId = num(row.id)
  return {
    id: quoteId,
    project_id: num(row.project_id),
    quote_sequence: seq,
    // prefer a stored number; otherwise the standard "{uca}-{seq:04d}-{visible version}" form
    quote_number: stored || `${uca}-${String(seq).padStart(4, "0")}-${visible}`,
    created_at: typeof row.created_at === "string" ? row.created_at : new Date().toISOString(),
    status: (row.status as Quote["status"]) ?? "Closed",
    current_version: version,
    client_po_number: strOrU(row.client_po_number) ?? null,
    work_description: strOrU(row.work_description) ?? null,
    markup_control_enabled: Boolean(row.markup_control_enabled),
    line_items: lines.filter((l) => num(l.quote_id) === quoteId).map(toLineItem),  // this quote's lines only
  }
}

/**
 * Open a generated PDF blob in a new tab.
 * @param blob - The rendered PDF.
 */
function openBlob(blob: Blob) {
  const url = URL.createObjectURL(blob)                                // a temporary object URL for the tab
  window.open(url, "_blank")
}

/**
 * The lines of one archived work order or PO.
 * @param props.lines - Its lines.
 * @param props.po - True for PO lines (received instead of fulfilled).
 */
function Lines({ lines, po }: { lines: ArchivedLine[]; po?: boolean }) {
  if (!lines.length) return <p className="text-sm text-muted-foreground">No lines.</p>
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-muted-foreground">
          <tr>
            <th className="py-1 pr-3">Item</th>
            <th className="py-1 pr-3">Description</th>
            <th className="py-1 pr-3 text-right">Qty</th>
            <th className="py-1 pr-3 text-right">Unit price</th>
            <th className="py-1 pr-3 text-right">{po ? "Received" : "Fulfilled"}</th>
            <th className="py-1 text-right">Pending</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((l, i) => (
            <tr key={l.vid ?? i} className="border-t align-top">
              <td className="py-1 pr-3 font-mono text-xs">{l.ref ?? l.part_number ?? l.item_type ?? "—"}</td>
              <td className="py-1 pr-3 whitespace-pre-wrap">{l.description ?? "—"}</td>
              <td className="py-1 pr-3 text-right">{l.quantity ?? "—"}</td>
              <td className="py-1 pr-3 text-right">{money(l.unit_price)}</td>
              <td className="py-1 pr-3 text-right">{(po ? l.qty_received : l.qty_fulfilled) ?? "—"}</td>
              <td className="py-1 text-right">{l.qty_pending ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/**
 * One archived project, read-only: Vision's work orders and POs, plus restore and print (#319/#322/#323).
 * @param props.project - The archived file.
 * @param props.onBack - Return to the list.
 * @param props.onRestored - Called after a successful unarchive, so the list reloads.
 */
function ArchivedProjectView({
  project,
  onBack,
  onRestored,
}: {
  project: ArchivedProject
  onBack: () => void
  onRestored: () => void
}) {
  const navigate = useNavigate()
  const { work_orders, pos } = project.vision                         // Vision's version is the record of these jobs
  const velocityQuotes = project.velocity?.quotes ?? []               // raw Velocity quote rows, if this was in Velocity
  const velocityLines = project.velocity?.quote_line_items ?? []      // their line items
  const canRestore = project.velocity_project_id != null              // only a Velocity project can be put back
  const [confirmRestore, setConfirmRestore] = useState(false)         // the restore confirmation dialog
  const [restoring, setRestoring] = useState(false)                   // a restore is in flight
  const [printing, setPrinting] = useState(false)                     // a PDF is rendering

  /** Put this project back in the database, then go to it (or report why it cannot). */
  const doRestore = async () => {
    setConfirmRestore(false)
    setRestoring(true)
    try {
      const res = await api.archive.unarchive(project.uca)            // POST /archive/projects/{uca}/unarchive
      toast({ title: `${project.uca} restored`, description: "The project is back in the database." })
      onRestored()                                                    // let the list refresh in the background
      if (res.project_id != null) navigate(`/projects/${res.project_id}`)  // open the live project
    } catch (e) {
      toast({ variant: "destructive", title: "Could not restore", description: e instanceof Error ? e.message : String(e) })
    } finally {
      setRestoring(false)
    }
  }

  /** Render a Velocity quote to a PDF and open it. */
  const printQuote = async (row: Record<string, unknown>) => {
    setPrinting(true)
    try {
      const [{ pdf }, { QuotePDF }, companySettings] = await Promise.all([
        import("@react-pdf/renderer"),
        import("@/components/pdf/QuotePDF"),
        api.companySettings.get(),
      ])
      const quote = toQuote(row, velocityLines, project.uca)          // raw rows -> a Quote the PDF accepts
      const pdfProject = {                                            // only the fields QuotePDF reads
        uca_project_number: project.uca,
        name: project.name ?? "",
        customer: { name: project.customer ?? "", address: "", postal_code: "" },
      } as unknown as Project
      openBlob(await pdf(<QuotePDF quote={quote} project={pdfProject} companySettings={companySettings} mode="full" />).toBlob())
    } catch (e) {
      toast({ variant: "destructive", title: "Could not print", description: e instanceof Error ? e.message : String(e) })
    } finally {
      setPrinting(false)
    }
  }

  /** Render one Vision work order to a plain PDF and open it. */
  const printWorkOrder = async (wo: ArchivedWorkOrder) => {
    setPrinting(true)
    try {
      const [{ pdf }, { ArchivedWorkOrderPDF }, companySettings] = await Promise.all([
        import("@react-pdf/renderer"),
        import("@/components/pdf/ArchivedWorkOrderPDF"),
        api.companySettings.get(),
      ])
      openBlob(
        await pdf(
          <ArchivedWorkOrderPDF
            workOrder={wo}
            uca={project.uca}
            projectName={project.name}
            customer={project.customer}
            companySettings={companySettings}
          />
        ).toBlob()
      )
    } catch (e) {
      toast({ variant: "destructive", title: "Could not print", description: e instanceof Error ? e.message : String(e) })
    } finally {
      setPrinting(false)
    }
  }

  return (
    <div className="space-y-4">
      <Button variant="ghost" size="sm" onClick={onBack}>
        <ChevronLeft className="h-4 w-4" /> Archived projects
      </Button>
      <div className="flex justify-between items-start gap-3 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold">
            {project.uca} {project.name}
          </h1>
          <p className="text-muted-foreground">
            {project.customer ?? "—"} · started {day(project.created_on)} · last activity {day(project.last_activity)}
          </p>
          <p className="text-xs text-muted-foreground mt-1">
            {canRestore ? "Archived from the live database. Read-only until restored." : "Read-only, as Vision held it."}
          </p>
        </div>
        {canRestore && (
          <Button onClick={() => setConfirmRestore(true)} disabled={restoring || printing} className="gap-2 shrink-0">
            {restoring ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArchiveRestore className="h-4 w-4" />}
            Restore to database
          </Button>
        )}
      </div>

      {velocityQuotes.length > 0 && (
        <>
          <h2 className="text-lg font-semibold">Velocity quotes ({velocityQuotes.length})</h2>
          <div className="bg-card rounded-lg border divide-y">
            {velocityQuotes.map((q, i) => (
              <div key={num(q.id) || i} className="flex items-center justify-between gap-3 px-3 py-2">
                <span className="font-mono text-sm">
                  {typeof q.quote_number === "string" && q.quote_number
                    ? q.quote_number
                    : `${project.uca}-${String(num(q.quote_sequence)).padStart(4, "0")}-${num(q.current_version) || 1}`}
                </span>
                <Button variant="outline" size="sm" onClick={() => printQuote(q)} disabled={printing} className="gap-2 shrink-0">
                  <Printer className="h-4 w-4" /> Print
                </Button>
              </div>
            ))}
          </div>
        </>
      )}

      <h2 className="text-lg font-semibold">Work orders ({work_orders.length})</h2>
      {work_orders.map((wo, i) => (
        <div key={wo.wo ?? i} className="bg-card rounded-lg border p-3 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">WO {wo.wo ?? "—"}</span>
            <Badge variant="outline">{wo.closed ? "closed" : "open"}</Badge>
            <span className="text-sm text-muted-foreground">
              {day(wo.created_at)}
              {wo.client_po_number && ` · client PO ${wo.client_po_number}`}
            </span>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => printWorkOrder(wo)}
              disabled={printing}
              className="gap-2 ml-auto"
            >
              <Printer className="h-4 w-4" /> Print
            </Button>
          </div>
          {wo.work_description && <p className="text-sm whitespace-pre-wrap">{wo.work_description}</p>}
          <Lines lines={wo.lines} />
        </div>
      ))}

      {pos.length > 0 && <h2 className="text-lg font-semibold">Purchase orders ({pos.length})</h2>}
      {pos.map((po, i) => (
        <div key={po.number ?? i} className="bg-card rounded-lg border p-3 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">PO {po.number ?? "—"}</span>
            {po.status && <Badge variant="outline">{po.status}</Badge>}
            <span className="text-sm text-muted-foreground">
              {po.vendor ?? "—"} · {day(po.created_at)}
            </span>
          </div>
          {po.work_description && <p className="text-sm whitespace-pre-wrap">{po.work_description}</p>}
          <Lines lines={po.lines} po />
        </div>
      ))}

      <AlertDialog open={confirmRestore} onOpenChange={(o) => { if (!o) setConfirmRestore(false) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Restore {project.uca}?</AlertDialogTitle>
            <AlertDialogDescription>
              This puts <span className="font-medium">{project.name}</span> back in the live database and reopens it.
              It will show in Projects again.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={doRestore}>Restore</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

/**
 * Archived projects (#304/#326): browse the whole archive, filter and sort it client-side, and
 * open one read-only. Every archived row is loaded once (about 2,145), so searching, the customer
 * and year filters, and the column sort are instant; opening a project still reads its file.
 */
export function ArchivedProjectsPage() {
  const [rows, setRows] = useState<ArchivedProjectEntry[]>([])        // every archived row
  const [archivedTotal, setArchivedTotal] = useState(0)              // how many are archived in all
  const [loading, setLoading] = useState(true)                       // the initial load is running
  const [error, setError] = useState<string | null>(null)           // why the last request failed
  const [open, setOpen] = useState<ArchivedProject | null>(null)    // the project being viewed
  const [opening, setOpening] = useState(false)                     // a file is being opened

  // Browse controls (all client-side).
  const [query, setQuery] = useState("")                            // the search box
  const [customer, setCustomer] = useState("")                      // the customer filter ("" = all)
  const [year, setYear] = useState("")                              // the year-of-last-activity filter ("" = all)
  const [sortBy, setSortBy] = useState<ArchiveSortKey>("last_activity")
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc")    // newest activity first by default

  /** Load every archived row once. */
  const load = () => {
    setLoading(true)
    setError(null)
    api.archive
      .search("", true)                                              // all=true: the whole archive, unpaged
      .then((r) => {
        setRows(r.projects)
        setArchivedTotal(r.archived)
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  // The filter dropdown options, derived from the loaded rows.
  const customerOptions = useMemo(
    () => [{ value: "", label: "All customers" }, ...distinctCustomers(rows).map((c) => ({ value: c, label: c }))],
    [rows]
  )
  const yearOptions = useMemo(
    () => [{ value: "", label: "All years" }, ...distinctYears(rows).map((y) => ({ value: y, label: y }))],
    [rows]
  )

  // The rows actually shown: filtered by search + customer + year, then sorted.
  const view = useMemo(
    () => browseArchived(rows, { query, customer, year, sortBy, sortDir }),
    [rows, query, customer, year, sortBy, sortDir]
  )

  /** Toggle the sort: same column flips direction, a new column sorts it ascending (activity descending). */
  const toggleSort = (col: ArchiveSortKey) => {
    if (sortBy === col) setSortDir((d) => (d === "asc" ? "desc" : "asc"))
    else {
      setSortBy(col)
      setSortDir(col === "last_activity" ? "desc" : "asc")
    }
  }

  const sortIcon = (col: ArchiveSortKey) => {
    if (sortBy !== col) return <ArrowUpDown className="h-3 w-3 opacity-40" />
    return sortDir === "asc" ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />
  }

  /**
   * One sortable column header.
   * @param props.col - The column key.
   * @param props.label - Its visible label.
   * @param props.className - Extra classes (used to hide a column on small screens).
   */
  const SortHeader = ({ col, label, className = "" }: { col: ArchiveSortKey; label: string; className?: string }) => {
    const ariaSort: "ascending" | "descending" | "none" =
      sortBy === col ? (sortDir === "asc" ? "ascending" : "descending") : "none"
    return (
      <div className={`${headerCellClass} ${className}`} role="columnheader" aria-sort={ariaSort}>
        <button type="button" onClick={() => toggleSort(col)} className="inline-flex items-center gap-1 hover:text-foreground" aria-label={`Sort by ${label}`}>
          {label} {sortIcon(col)}
        </button>
      </div>
    )
  }

  /**
   * Open one archived project from the store.
   * @param uca - Its UCA number.
   */
  const openProject = async (uca: string) => {
    setOpening(true)
    setError(null)
    try {
      setOpen(await api.archive.get(uca))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setOpening(false)
    }
  }

  if (open) return <ArchivedProjectView project={open} onBack={() => setOpen(null)} onRestored={load} />

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Archived projects</h1>
        <p className="text-muted-foreground">
          Projects kept outside the live database. Search, filter and sort the whole archive; open one to read it.
        </p>
      </div>

      <div className="flex items-end gap-3 flex-wrap">
        <div className="relative max-w-md flex-1 min-w-[240px]">
          <Search className="h-4 w-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <Input
            className="pl-9"
            placeholder="UCA number, name or customer"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search archived projects"
          />
        </div>
        <div className="w-56">
          <SearchableSelect options={customerOptions} value={customer} onChange={setCustomer} placeholder="All customers" searchPlaceholder="Filter customers…" />
        </div>
        <div className="w-32">
          <SearchableSelect options={yearOptions} value={year} onChange={setYear} placeholder="All years" searchPlaceholder="Filter years…" />
        </div>
      </div>

      {error && (
        <div className="p-3 bg-destructive/10 border border-destructive/20 rounded-md text-destructive text-sm">{error}</div>
      )}

      <p className="text-sm text-muted-foreground">
        {loading ? "Loading…" : `${view.length} of ${archivedTotal} archived`}
      </p>

      {loading ? (
        <div className="bg-card rounded-lg border p-8 text-center text-muted-foreground">Loading…</div>
      ) : view.length === 0 ? (
        <div className="bg-card rounded-lg border p-8 text-center text-muted-foreground">
          {archivedTotal === 0 ? "Nothing is archived yet." : "No archived project matches."}
        </div>
      ) : (
        <VirtualizedTable
          tableLabel="Archived projects"
          items={view}
          rowHeight={52}
          height="calc(100vh - 320px)"
          gridCols={ARCHIVE_GRID_COLS}
          header={
            <>
              <SortHeader col="uca" label="UCA #" />
              <SortHeader col="name" label="Project" />
              <SortHeader col="customer" label="Customer" className="hidden lg:flex" />
              <SortHeader col="last_activity" label="Last activity" className="hidden sm:flex" />
              <SortHeader col="work_orders" label="WOs" className="hidden sm:flex justify-end" />
              <SortHeader col="pos" label="POs" className="hidden lg:flex justify-end" />
            </>
          }
          getKey={(p) => p.uca}
          getRowProps={(p) => ({
            role: "button",
            tabIndex: 0,
            className: "cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
            "aria-label": `Open archived project ${p.uca} ${p.name ?? ""}`,
            onClick: () => openProject(p.uca),
            onKeyDown: (e: React.KeyboardEvent<HTMLDivElement>) => {
              if (e.target !== e.currentTarget) return
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault()
                openProject(p.uca)
              }
            },
          })}
          renderRow={(p) => (
            <>
              <div className={`${cellClass} font-mono text-sm`}>
                <Archive className="h-4 w-4 text-muted-foreground mr-2 shrink-0" />
                {p.uca}
              </div>
              <div className={`${cellClass} truncate`}>{p.name}</div>
              <div className={`${cellClass} hidden lg:flex text-muted-foreground truncate`}>{p.customer}</div>
              <div className={`${cellClass} hidden sm:flex text-muted-foreground`}>{day(p.last_activity)}</div>
              <div className={`${cellClass} hidden sm:flex justify-end text-muted-foreground`}>{p.work_orders}</div>
              <div className={`${cellClass} hidden lg:flex justify-end text-muted-foreground`}>{p.pos}</div>
            </>
          )}
        />
      )}

      {opening && (
        <div className="fixed inset-0 bg-background/50 flex items-center justify-center z-50">
          <div className="bg-card rounded-lg border px-4 py-3 flex items-center gap-2 text-sm">
            <Loader2 className="h-4 w-4 animate-spin" /> Opening…
          </div>
        </div>
      )}
    </div>
  )
}
