import { Fragment, useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { ProjectForm } from "@/components/forms/ProjectForm"
import type { ProjectFormInput } from "@/components/forms/ProjectForm"
import { api } from "@/api/client"
import type { ProjectListView, ProjectSearchResult } from "@/types"
import { Plus, Trash2, Pencil, FolderOpen, Search, FileText, ShoppingCart } from "lucide-react"

interface ProjectsPageProps {
  onSelectProject: (projectId: number) => void
  onSelectChildDoc: (projectId: number, doc: { type: "quote" | "po"; id: number }) => void
  searchTerm: string
  onSearchTermChange: (value: string) => void
}

const SEARCH_DEBOUNCE_MS = 300

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
  const [error, setError] = useState<string | null>(null)
  const [debouncedTerm, setDebouncedTerm] = useState(searchTerm)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingProject, setEditingProject] = useState<ProjectFormInput | null>(null)

  // When a search is active, show search results; otherwise show the full list.
  const displayProjects = searchResults ?? baseProjects

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

  // Debounce raw search term so we don't fire a request on every keystroke.
  useEffect(() => {
    const handle = setTimeout(() => setDebouncedTerm(searchTerm), SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(handle)
  }, [searchTerm])

  // Run cross-entity search when the debounced term changes. Use a cancelled flag
  // so a stale response from an earlier query can't overwrite a newer one.
  useEffect(() => {
    const term = debouncedTerm.trim()
    if (!term) {
      setSearchResults(null)
      return
    }
    let cancelled = false
    api.projects
      .search(term)
      .then((data) => {
        if (!cancelled) setSearchResults(data)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Search failed")
      })
    return () => {
      cancelled = true
    }
  }, [debouncedTerm])

  // After a mutation, refresh both the base list and any active search.
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

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "active":
        return <Badge className="bg-green-500/10 text-green-600 border-green-500/20">Active</Badge>
      case "completed":
        return <Badge variant="secondary">Completed</Badge>
      case "on_hold":
        return <Badge variant="outline">On Hold</Badge>
      default:
        return <Badge variant="outline">{status}</Badge>
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold">Projects</h1>
          <p className="text-muted-foreground">Manage projects, quotes, and purchase orders</p>
        </div>
        <Button onClick={handleAdd} className="gap-2">
          <Plus className="h-4 w-4" />
          New Project
        </Button>
      </div>

      {error && (
        <div className="p-4 bg-destructive/10 border border-destructive/20 rounded-md text-destructive">
          {error}
        </div>
      )}

      <div className="relative max-w-md">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <Input
          placeholder="Search projects, POs, quotes, vendors..."
          value={searchTerm}
          onChange={(e) => onSearchTermChange(e.target.value)}
          className="pl-9"
        />
      </div>

      <div className="bg-card rounded-lg border shadow-sm">
        {loading ? (
          <div className="p-8 text-center text-muted-foreground">Loading...</div>
        ) : displayProjects.length === 0 ? (
          <div className="p-8 text-center text-muted-foreground">
            {baseProjects.length === 0
              ? "No projects found. Create your first project to get started."
              : "No projects match your search."}
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>UCA #</TableHead>
                <TableHead>UCSH #</TableHead>
                <TableHead>Project Name</TableHead>
                <TableHead>Customer</TableHead>
                <TableHead>Project Lead</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Created On</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {displayProjects.map((project) => {
                const hasChildMatches = project.matched_quotes.length > 0 || project.matched_pos.length > 0
                return (
                  <Fragment key={project.id}>
                    <TableRow
                      className="cursor-pointer hover:bg-muted/50"
                      onClick={() => onSelectProject(project.id)}
                    >
                      <TableCell className="font-mono text-sm">{project.uca_project_number}</TableCell>
                      <TableCell className="text-muted-foreground">{project.ucsh_project_number || "-"}</TableCell>
                      <TableCell className="font-medium">
                        <div className="flex items-center gap-2">
                          <FolderOpen className="h-4 w-4 text-muted-foreground" />
                          {project.name}
                        </div>
                      </TableCell>
                      <TableCell>{project.customer_name}</TableCell>
                      <TableCell className="text-muted-foreground">{project.project_lead || "-"}</TableCell>
                      <TableCell>{getStatusBadge(project.status)}</TableCell>
                      <TableCell className="text-muted-foreground">
                        {new Date(project.created_on).toLocaleDateString()}
                      </TableCell>
                      <TableCell className="text-right space-x-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={(e) => handleEdit(project, e)}
                        >
                          <Pencil className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={(e) => handleDelete(project.id, e)}
                          className="text-destructive hover:text-destructive hover:bg-destructive/10"
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </TableCell>
                    </TableRow>
                    {hasChildMatches && (
                      <TableRow className="bg-muted/20 hover:bg-muted/30 border-l-2 border-l-primary/40">
                        <TableCell colSpan={8} className="py-2 pl-10">
                          <div className="flex flex-col gap-1 text-xs">
                            {project.matched_quotes.map((q) => (
                              <button
                                key={`q-${q.id}`}
                                type="button"
                                className="flex items-center gap-2 text-muted-foreground hover:text-foreground transition-colors w-fit"
                                onClick={(e) => {
                                  e.stopPropagation()
                                  onSelectChildDoc(project.id, { type: "quote", id: q.id })
                                }}
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
                                className="flex items-center gap-2 text-muted-foreground hover:text-foreground transition-colors w-fit"
                                onClick={(e) => {
                                  e.stopPropagation()
                                  onSelectChildDoc(project.id, { type: "po", id: po.id })
                                }}
                              >
                                <span className="text-muted-foreground/60" aria-hidden>↳</span>
                                <ShoppingCart className="h-3 w-3" />
                                <span>PO</span>
                                <span className="font-mono">{po.po_number}</span>
                                <span className="text-muted-foreground/70">— {po.vendor_name}</span>
                              </button>
                            ))}
                          </div>
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                )
              })}
            </TableBody>
          </Table>
        )}
      </div>

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
