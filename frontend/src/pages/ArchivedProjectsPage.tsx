import { useEffect, useState } from "react"
import { Archive, ChevronLeft, Search } from "lucide-react"
import { api } from "@/api/client"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { formatCurrency } from "@/lib/format"
import type { ArchivedLine, ArchivedProject, ArchivedProjectEntry } from "@/types"

/** Milliseconds to wait after typing before searching. */
const SEARCH_DEBOUNCE_MS = 300

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
 * One archived project, read-only: Vision's work orders and POs with their lines.
 * @param props.project - The archived file.
 * @param props.onBack - Return to the search.
 */
function ArchivedProjectView({ project, onBack }: { project: ArchivedProject; onBack: () => void }) {
  const { work_orders, pos } = project.vision // Vision's version is the record of these jobs
  return (
    <div className="space-y-4">
      <Button variant="ghost" size="sm" onClick={onBack}>
        <ChevronLeft className="h-4 w-4" /> Archived projects
      </Button>
      <div>
        <h1 className="text-2xl font-bold">
          {project.uca} {project.name}
        </h1>
        <p className="text-muted-foreground">
          {project.customer ?? "—"} · started {day(project.created_on)} · last activity {day(project.last_activity)}
        </p>
        <p className="text-xs text-muted-foreground mt-1">
          Archived: no activity since 2018. Read-only, as Vision held it.
        </p>
      </div>

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
    </div>
  )
}

/**
 * Archived projects (#304): search the archive and open a project read-only.
 * The archive is in the bucket, not the database, so opening one is slower.
 */
export function ArchivedProjectsPage() {
  const [term, setTerm] = useState("") // what is typed
  const [query, setQuery] = useState("") // what was searched (after the pause)
  const [results, setResults] = useState<ArchivedProjectEntry[]>([]) // matching projects
  const [total, setTotal] = useState(0) // how many match
  const [archived, setArchived] = useState(0) // how many are archived in all
  const [open, setOpen] = useState<ArchivedProject | null>(null) // the project being viewed
  const [loading, setLoading] = useState(false) // a request is running
  const [error, setError] = useState<string | null>(null) // why the last request failed

  useEffect(() => {
    const t = setTimeout(() => setQuery(term), SEARCH_DEBOUNCE_MS) // search once typing pauses
    return () => clearTimeout(t)
  }, [term])

  useEffect(() => {
    let live = true // ignore a slow answer to an older search
    setLoading(true)
    setError(null)
    api.archive
      .search(query)
      .then((r) => {
        if (!live) return
        setResults(r.projects)
        setTotal(r.total)
        setArchived(r.archived)
      })
      .catch((e) => live && setError(e instanceof Error ? e.message : String(e)))
      .finally(() => live && setLoading(false))
    return () => {
      live = false
    }
  }, [query])

  /**
   * Open one archived project from the bucket.
   * @param uca - Its UCA number.
   */
  const openProject = async (uca: string) => {
    setLoading(true)
    setError(null)
    try {
      setOpen(await api.archive.get(uca))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  if (open) return <ArchivedProjectView project={open} onBack={() => setOpen(null)} />

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Archived projects</h1>
        <p className="text-muted-foreground">
          {archived} projects with no activity since 2018, kept outside the database. Opening one takes a moment.
        </p>
      </div>
      <div className="relative max-w-md">
        <Search className="h-4 w-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
        <Input
          className="pl-9"
          placeholder="UCA number, name or customer"
          value={term}
          onChange={(e) => setTerm(e.target.value)}
        />
      </div>
      {error && (
        <div className="p-3 bg-destructive/10 border border-destructive/20 rounded-md text-destructive text-sm">{error}</div>
      )}
      <div className="bg-card rounded-lg border">
        {results.length === 0 ? (
          <div className="p-8 text-center text-muted-foreground">
            {loading ? "Loading…" : archived === 0 ? "Nothing is archived yet." : "No archived project matches."}
          </div>
        ) : (
          <ul>
            {results.map((p) => (
              <li key={p.uca} className="border-t first:border-t-0">
                <button
                  type="button"
                  onClick={() => openProject(p.uca)}
                  disabled={loading}
                  className="w-full text-left px-4 py-3 hover:bg-muted flex items-center gap-3"
                >
                  <Archive className="h-4 w-4 text-muted-foreground shrink-0" />
                  <span className="font-mono text-sm w-16 shrink-0">{p.uca}</span>
                  <span className="flex-1 min-w-0 truncate">{p.name}</span>
                  <span className="hidden md:block w-56 truncate text-muted-foreground text-sm">{p.customer}</span>
                  <span className="hidden sm:block w-24 text-right text-muted-foreground text-sm">{day(p.last_activity)}</span>
                  <span className="hidden sm:block w-24 text-right text-muted-foreground text-sm">
                    {p.work_orders} WO · {p.pos} PO
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      {total > results.length && (
        <p className="text-sm text-muted-foreground">
          Showing {results.length} of {total}; type more to narrow the search.
        </p>
      )}
    </div>
  )
}
