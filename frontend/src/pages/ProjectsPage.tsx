import { useEffect, useMemo, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { StatusBadge } from "@/components/ui/status-badge"
import { formatDate, EMPTY_VALUE } from "@/lib/format"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import { VirtualizedTable, headerCellClass, cellClass } from "@/components/ui/virtualized-table"
import { ProjectForm } from "@/components/forms/ProjectForm"
import type { ProjectFormInput } from "@/components/forms/ProjectForm"
import { api } from "@/api/client"
import type { ProjectListView, ProjectSearchResult } from "@/types"
import { Plus, Trash2, Pencil, FolderOpen, Search, FileText, ShoppingCart, Loader2, ChevronUp, ChevronDown, ArrowUpDown } from "lucide-react"

interface ProjectsPageProps {
  onSelectProject: (projectId: number) => void
  onSelectChildDoc: (projectId: number, doc: { type: "quote" | "po"; id: number }) => void
  searchTerm: string
  onSearchTermChange: (value: string) => void
}

const SEARCH_DEBOUNCE_MS = 300

// Responsive grid: 3 cols on mobile (Name + Status + Actions), 4 at sm (+UCA#), 8 at lg (full).
// Cells use `hidden …:flex` so the column order falls into place at each breakpoint.
const PROJECTS_GRID_COLS =
  "grid-cols-[minmax(0,1fr)_auto_auto] " +
  "sm:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_auto_auto] " +
  "lg:grid-cols-[minmax(0,2fr)_minmax(0,1.5fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_auto_minmax(0,1fr)_auto]"

const toSearchResultShape = (p: ProjectListView): ProjectSearchResult => ({
  ...p,
  matched_quotes: [],
  matched_pos: [],
})

const toFormInput = (p: ProjectListView): ProjectFormInput => ({
  id: p.id,
  name: p.name,
  customer_id: p.customer_id,
  status: p.status,
  ucsh_project_number: p.ucsh_project_number,
  project_lead: p.project_lead,
  uca_project_number: p.uca_project_number,
  created_on: p.created_on,
})

export function ProjectsPage({
  onSelectProject,
  onSelectChildDoc,
  searchTerm,
  onSearchTermChange,
}: ProjectsPageProps) {
  const [baseProjects, setBaseProjects] = useState<ProjectSearchResult[]>([])
  const [searchResults, setSearchResults] = useState<ProjectSearchResult[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [searchLoading, setSearchLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [debouncedTerm, setDebouncedTerm] = useState(searchTerm)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingProject, setEditingProject] = useState<ProjectFormInput | null>(null)

  // UX-4 filter controls
  const [showArchived, setShowArchived] = useState(false)
  type SortColumn = "name" | "customer_name" | "uca_project_number" | "ucsh_project_number" | "project_lead" | "status" | "created_on"
  const [sortBy, setSortBy] = useState<SortColumn>("uca_project_number")
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc")

  // Apply archived filter + sort to the raw results before render.
  const rawProjects = searchResults ?? baseProjects
  const archivedCount = useMemo(
    () => rawProjects.filter((p) => p.status.toLowerCase() === "archived").length,
    [rawProjects]
  )
  const displayProjects = useMemo(() => {
    const filtered = showArchived
      ? rawProjects
      : rawProjects.filter((p) => p.status.toLowerCase() !== "archived")
    const sorted = [...filtered].sort((a, b) => {
      const av = (a[sortBy] ?? "") as string
      const bv = (b[sortBy] ?? "") as string
      const cmp = av.localeCompare(bv, undefined, { numeric: true, sensitivity: "base" })
      return sortDir === "asc" ? cmp : -cmp
    })
    return sorted
  }, [rawProjects, showArchived, sortBy, sortDir])

  const toggleSort = (col: SortColumn) => {
    if (sortBy === col) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"))
    } else {
      setSortBy(col)
      setSortDir(col === "uca_project_number" ? "desc" : "asc")
    }
  }

  const renderSortIcon = (col: SortColumn) => {
    if (sortBy !== col) return <ArrowUpDown className="h-3 w-3 opacity-40" />
    return sortDir === "asc"
      ? <ChevronUp className="h-3 w-3" />
      : <ChevronDown className="h-3 w-3" />
  }

  const fetchBaseList = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await api.projects.getListView()
      setBaseProjects(data.map(toSearchResultShape))
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch projects")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchBaseList()
  }, [])

  useEffect(() => {
    const handle = setTimeout(() => setDebouncedTerm(searchTerm), SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(handle)
  }, [searchTerm])

  useEffect(() => {
    if (searchTerm.trim() && searchTerm !== debouncedTerm) {
      setSearchLoading(true)
    }
  }, [searchTerm, debouncedTerm])

  useEffect(() => {
    const term = debouncedTerm.trim()
    if (!term) {
      setSearchResults(null)
      setSearchLoading(false)
      return
    }
    let cancelled = false
    setSearchLoading(true)
    api.projects
      .search(term)
      .then((data) => {
        if (!cancelled) {
          setSearchResults(data)
          setSearchLoading(false)
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Search failed")
          setSearchLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [debouncedTerm])

  const refreshAll = async () => {
    await fetchBaseList()
    const term = debouncedTerm.trim()
    if (!term) {
      setSearchResults(null)
      return
    }
    try {
      const data = await api.projects.search(term)
      setSearchResults(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Search failed")
    }
  }

  const handleDelete = async (id: number, e: React.MouseEvent) => {
    e.stopPropagation()
    if (!confirm("Are you sure you want to delete this project? All quotes and purchase orders will be deleted.")) return
    try {
      await api.projects.delete(id)
      refreshAll()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to delete project")
    }
  }

  const handleEdit = (project: ProjectListView, e: React.MouseEvent) => {
    e.stopPropagation()
    setEditingProject(toFormInput(project))
    setDialogOpen(true)
  }

  const handleAdd = () => {
    setEditingProject(null)
    setDialogOpen(true)
  }

  const handleDialogClose = (open: boolean) => {
    if (!open) {
      setEditingProject(null)
    }
    setDialogOpen(open)
  }

  // Sort headers — icon-only sort arrows pair with the column label, so they
  // already have an accessible name. Kept as a small helper for clarity.
  const SortHeader = ({ col, label, className = "" }: { col: SortColumn; label: string; className?: string }) => (
    <div className={`${headerCellClass} ${className}`}>
      <button
        type="button"
        onClick={() => toggleSort(col)}
        className="inline-flex items-center gap-1 hover:text-foreground"
        aria-label={`Sort by ${label}`}
      >
        {label} {renderSortIcon(col)}
      </button>
    </div>
  )

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-start gap-3 flex-wrap">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold">Projects</h1>
          <p className="text-muted-foreground">Manage projects, quotes, and purchase orders</p>
        </div>
        <Button onClick={handleAdd} className="gap-2 shrink-0">
          <Plus className="h-4 w-4" />
          <span className="hidden sm:inline">New Project</span>
          <span className="sm:hidden">New</span>
        </Button>
      </div>

      {error && (
        <div className="p-4 bg-destructive/10 border border-destructive/20 rounded-md text-destructive">
          {error}
        </div>
      )}

      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative max-w-md flex-1 min-w-[260px]">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search projects, POs, quotes, vendors..."
            value={searchTerm}
            onChange={(e) => onSearchTermChange(e.target.value)}
            className="pl-9 pr-9"
          />
          {searchLoading && (
            <Loader2 className="absolute right-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground animate-spin" />
          )}
        </div>
        <Button
          variant={showArchived ? "secondary" : "outline"}
          size="sm"
          onClick={() => setShowArchived((v) => !v)}
          className="gap-2"
        >
          {showArchived ? "Hide archived" : `Show archived (${archivedCount})`}
        </Button>
      </div>

      {loading ? (
        <div className="bg-card rounded-lg border shadow-sm p-8 text-center text-muted-foreground">
          Loading...
        </div>
      ) : displayProjects.length === 0 ? (
        <div className="bg-card rounded-lg border shadow-sm p-8 text-center text-muted-foreground">
          {baseProjects.length === 0
            ? "No projects found. Create your first project to get started."
            : "No projects match your search."}
        </div>
      ) : (
        <VirtualizedTable
          items={displayProjects}
          rowHeight={60}
          measureRows
          height="calc(100vh - 280px)"
          gridCols={PROJECTS_GRID_COLS}
          header={
            <>
              <SortHeader col="name" label="Project Name" />
              <SortHeader col="customer_name" label="Customer" className="hidden lg:flex" />
              <SortHeader col="uca_project_number" label="UCA #" className="hidden sm:flex" />
              <SortHeader col="ucsh_project_number" label="UCSH #" className="hidden lg:flex" />
              <SortHeader col="project_lead" label="Project Lead" className="hidden lg:flex" />
              <SortHeader col="status" label="Status" />
              <SortHeader col="created_on" label="Created On" className="hidden lg:flex" />
              <div className={`${headerCellClass} text-right`}>Actions</div>
            </>
          }
          getKey={(p) => p.id}
          getRowProps={(project) => ({
            role: "button",
            tabIndex: 0,
            className: "cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
            "aria-label": `Open project ${project.name}`,
            onClick: () => onSelectProject(project.id),
            onKeyDown: (e: React.KeyboardEvent<HTMLDivElement>) => {
              if (e.target !== e.currentTarget) return // let inner controls own the key
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault()
                onSelectProject(project.id)
              }
            },
          })}
          renderRow={(project) => {
            const hasChildMatches = project.matched_quotes.length > 0 || project.matched_pos.length > 0
            return (
              <>
                <div className={`${cellClass} font-medium`}>
                  <FolderOpen className="h-4 w-4 text-muted-foreground mr-2 shrink-0" />
                  <span className="truncate">{project.name}</span>
                </div>
                <div className={`${cellClass} hidden lg:flex truncate`}>{project.customer_name}</div>
                <div className={`${cellClass} hidden sm:flex font-mono text-sm truncate`}>
                  {project.uca_project_number}
                </div>
                <div className={`${cellClass} hidden lg:flex text-muted-foreground truncate`}>
                  {project.ucsh_project_number || EMPTY_VALUE}
                </div>
                <div className={`${cellClass} hidden lg:flex text-muted-foreground truncate`}>
                  {project.project_lead || EMPTY_VALUE}
                </div>
                <div className={cellClass}>
                  <StatusBadge status={project.status} />
                </div>
                <div className={`${cellClass} hidden lg:flex text-muted-foreground`}>
                  {formatDate(project.created_on)}
                </div>
                <div className={`${cellClass} justify-end gap-1`}>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={(e) => handleEdit(project, e)}
                    aria-label={`Edit project ${project.name}`}
                  >
                    <Pencil className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={(e) => handleDelete(project.id, e)}
                    className="text-destructive hover:text-destructive hover:bg-destructive/10"
                    aria-label={`Delete project ${project.name}`}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
                {hasChildMatches && (
                  <div className="col-span-full bg-muted/20 border-l-2 border-l-primary/40 px-4 py-2 pl-10 flex flex-col gap-1 text-xs">
                    {project.matched_quotes.map((q) => (
                      <button
                        key={`q-${q.id}`}
                        type="button"
                        className="flex items-center gap-2 text-muted-foreground hover:text-foreground transition-colors w-fit focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-sm"
                        onClick={(e) => {
                          e.stopPropagation()
                          onSelectChildDoc(project.id, { type: "quote", id: q.id })
                        }}
                        aria-label={`Open quote ${q.quote_number} in project ${project.name}`}
                      >
                        <span className="text-muted-foreground/60" aria-hidden>↳</span>
                        <FileText className="h-3 w-3" />
                        <span>Quote</span>
                        <span className="font-mono">{q.quote_number}</span>
                      </button>
                    ))}
                    {project.matched_pos.map((po) => (
                      <button
                        key={`po-${po.id}`}
                        type="button"
                        className="flex items-center gap-2 text-muted-foreground hover:text-foreground transition-colors w-fit focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-sm"
                        onClick={(e) => {
                          e.stopPropagation()
                          onSelectChildDoc(project.id, { type: "po", id: po.id })
                        }}
                        aria-label={`Open purchase order ${po.po_number} for ${po.vendor_name} in project ${project.name}`}
                      >
                        <span className="text-muted-foreground/60" aria-hidden>↳</span>
                        <ShoppingCart className="h-3 w-3" />
                        <span>PO</span>
                        <span className="font-mono">{po.po_number}</span>
                        <span className="text-muted-foreground/70">— {po.vendor_name}</span>
                      </button>
                    ))}
                  </div>
                )}
              </>
            )
          }}
        />
      )}

      <Dialog open={dialogOpen} onOpenChange={handleDialogClose}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editingProject ? "Edit Project" : "Create New Project"}</DialogTitle>
            <DialogDescription>
              {editingProject ? "Update the project details below." : "Create a new project for a customer."}
            </DialogDescription>
          </DialogHeader>
          <ProjectForm
            project={editingProject ?? undefined}
            onSuccess={() => {
              handleDialogClose(false)
              refreshAll()
            }}
            onCancel={() => handleDialogClose(false)}
          />
        </DialogContent>
      </Dialog>
    </div>
  )
}
