import { useState, useEffect, useMemo, lazy, Suspense } from "react"
import { useLocation, useNavigate } from "react-router-dom"
import { Show, SignInButton, SignUpButton, UserButton } from "@clerk/react"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Input } from "@/components/ui/input"
import { PartForm } from "@/components/forms/PartForm"
import { LaborForm } from "@/components/forms/LaborForm"
import { MiscForm } from "@/components/forms/MiscForm"
import { ThemeToggle } from "@/components/theme-toggle"
import { ProjectsPage } from "@/pages/ProjectsPage"
import { api } from "@/api/client"
import { useIsAdmin } from "@/hooks/use-is-admin"
import { VirtualizedTable, headerCellClass, cellClass } from "@/components/ui/virtualized-table"

// Lazy-loaded pages: keep ProjectsPage eager (default landing), defer the rest.
const ProfilesPage = lazy(() =>
  import("@/pages/ProfilesPage").then((m) => ({ default: m.ProfilesPage }))
)
const ProjectDetailsPage = lazy(() =>
  import("@/pages/ProjectDetailsPage").then((m) => ({ default: m.ProjectDetailsPage }))
)
const ReportsPage = lazy(() =>
  import("@/pages/ReportsPage").then((m) => ({ default: m.ReportsPage }))
)
const SettingsPage = lazy(() =>
  import("@/pages/SettingsPage").then((m) => ({ default: m.SettingsPage }))
)
const MigrationPage = lazy(() =>
  import("@/pages/MigrationPage").then((m) => ({ default: m.MigrationPage }))
)

function PageFallback() {
  return (
    <div className="p-8 text-center text-muted-foreground text-sm">Loading…</div>
  )
}
import type { Part, Labor, Miscellaneous } from "@/types"
import {
  Package,
  Wrench,
  Plus,
  Trash2,
  Pencil,
  Users,
  FolderOpen,
  Boxes,
  FileText,
  Search,
  BarChart3,
  Settings,
  DatabaseZap,
} from "lucide-react"
import { Toaster } from "@/components/ui/toaster"

type AppView = "profiles" | "projects" | "project-details" | "inventory" | "reports" | "settings" | "migration"

type ParsedRoute =
  | { view: Exclude<AppView, "project-details"> }
  | {
      view: "project-details"
      projectId: number
      initialDoc: { type: "quote" | "po" | "invoice"; id: number } | null
    }

function parseRoute(pathname: string): ParsedRoute {
  if (pathname === "/profiles") return { view: "profiles" }
  if (pathname === "/inventory") return { view: "inventory" }
  if (pathname === "/reports") return { view: "reports" }
  if (pathname === "/settings") return { view: "settings" }
  if (pathname === "/admin/migration") return { view: "migration" }
  // /projects/:id, /projects/:id/quotes|pos|invoices/:docId
  const projMatch = pathname.match(/^\/projects\/(\d+)(?:\/(quotes|pos|invoices)\/(\d+))?$/)
  if (projMatch) {
    const projectId = parseInt(projMatch[1], 10)
    const docSeg = projMatch[2]
    const docId = projMatch[3] ? parseInt(projMatch[3], 10) : null
    let initialDoc: { type: "quote" | "po" | "invoice"; id: number } | null = null
    if (docId !== null) {
      if (docSeg === "quotes") initialDoc = { type: "quote", id: docId }
      else if (docSeg === "pos") initialDoc = { type: "po", id: docId }
      else if (docSeg === "invoices") initialDoc = { type: "invoice", id: docId }
    }
    return { view: "project-details", projectId, initialDoc }
  }
  // /, /projects, anything else → projects landing
  return { view: "projects" }
}

function App() {
  const navigate = useNavigate()
  const location = useLocation()
  const route = useMemo(() => parseRoute(location.pathname), [location.pathname])
  const currentView: AppView = route.view
  const selectedProjectId = route.view === "project-details" ? route.projectId : null
  const pendingInitialDoc = route.view === "project-details" ? route.initialDoc : null

  // Projects page search term — lifted here so it survives drilling into a project and coming back.
  const [projectSearchTerm, setProjectSearchTerm] = useState("")

  // Migration is an admin-only destructive surface; non-admins shouldn't see it.
  const isAdmin = useIsAdmin()
  useEffect(() => {
    if (currentView === "migration" && !isAdmin) {
      navigate("/projects", { replace: true })
    }
  }, [currentView, isAdmin, navigate])

  // Inventory state
  const [parts, setParts] = useState<Part[]>([])
  const [laborItems, setLaborItems] = useState<Labor[]>([])
  const [miscItems, setMiscItems] = useState<Miscellaneous[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [inventorySearchTerm, setInventorySearchTerm] = useState("")

  // Dialog state
  const [partDialogOpen, setPartDialogOpen] = useState(false)
  const [laborDialogOpen, setLaborDialogOpen] = useState(false)
  const [miscDialogOpen, setMiscDialogOpen] = useState(false)

  // Edit state
  const [editingPart, setEditingPart] = useState<Part | null>(null)
  const [editingLabor, setEditingLabor] = useState<Labor | null>(null)
  const [editingMisc, setEditingMisc] = useState<Miscellaneous | null>(null)

  // Fetch inventory data when viewing inventory
  const fetchInventory = async () => {
    setLoading(true)
    setError(null)
    try {
      const [partsData, laborData, miscData] = await Promise.all([
        api.parts.getAll(),
        api.labor.getAll(),
        api.misc.getAll(),
      ])
      setParts(partsData)
      setLaborItems(laborData)
      setMiscItems(miscData)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch data")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (currentView === "inventory") {
      fetchInventory()
    }
  }, [currentView])

  const handleDeletePart = async (id: number) => {
    if (!confirm("Are you sure you want to delete this part?")) return
    try {
      await api.parts.delete(id)
      fetchInventory()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to delete part")
    }
  }

  const handleDeleteLabor = async (id: number) => {
    if (!confirm("Are you sure you want to delete this labor item?")) return
    try {
      await api.labor.delete(id)
      fetchInventory()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to delete labor")
    }
  }

  const handleEditPart = (part: Part) => {
    setEditingPart(part)
    setPartDialogOpen(true)
  }

  const handleEditLabor = (labor: Labor) => {
    setEditingLabor(labor)
    setLaborDialogOpen(true)
  }

  const handleDeleteMisc = async (id: number) => {
    if (!confirm("Are you sure you want to delete this miscellaneous item?")) return
    try {
      await api.misc.delete(id)
      fetchInventory()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to delete miscellaneous item")
    }
  }

  const handleEditMisc = (misc: Miscellaneous) => {
    setEditingMisc(misc)
    setMiscDialogOpen(true)
  }

  const handleAddPart = () => {
    setEditingPart(null)
    setPartDialogOpen(true)
  }

  const handleAddLabor = () => {
    setEditingLabor(null)
    setLaborDialogOpen(true)
  }

  const handleAddMisc = () => {
    setEditingMisc(null)
    setMiscDialogOpen(true)
  }

  const handlePartDialogClose = (open: boolean) => {
    if (!open) {
      setEditingPart(null)
    }
    setPartDialogOpen(open)
  }

  const handleLaborDialogClose = (open: boolean) => {
    if (!open) {
      setEditingLabor(null)
    }
    setLaborDialogOpen(open)
  }

  const handleMiscDialogClose = (open: boolean) => {
    if (!open) {
      setEditingMisc(null)
    }
    setMiscDialogOpen(open)
  }

  const handleSelectProject = (projectId: number) => {
    navigate(`/projects/${projectId}`)
  }

  const handleSelectChildDoc = (projectId: number, doc: { type: "quote" | "po"; id: number }) => {
    const seg = doc.type === "quote" ? "quotes" : "pos"
    navigate(`/projects/${projectId}/${seg}/${doc.id}`)
  }

  const handleBackToProjects = () => {
    navigate("/projects")
  }

  const renderContent = () => {
    switch (currentView) {
      case "profiles":
        return <ProfilesPage />

      case "projects":
        return (
          <ProjectsPage
            onSelectProject={handleSelectProject}
            onSelectChildDoc={handleSelectChildDoc}
            searchTerm={projectSearchTerm}
            onSearchTermChange={setProjectSearchTerm}
          />
        )

      case "reports":
        return <ReportsPage />

      case "settings":
        return <SettingsPage />

      case "migration":
        // Defensive gate in case state somehow lands here for a non-admin
        // (the useEffect above will then redirect to projects on the next tick).
        if (!isAdmin) return null
        return <MigrationPage />

      case "project-details":
        if (selectedProjectId === null) {
          navigate("/projects", { replace: true })
          return null
        }
        return (
          <ProjectDetailsPage
            projectId={selectedProjectId}
            onBack={handleBackToProjects}
            initialDoc={pendingInitialDoc}
          />
        )

      case "inventory": {
        // Filter functions for search
        const filteredParts = parts.filter((part) => {
          const term = inventorySearchTerm.toLowerCase()
          if (!term) return true
          return (
            part.part_number.toLowerCase().includes(term) ||
            part.description.toLowerCase().includes(term)
          )
        })

        const filteredLaborItems = laborItems.filter((labor) => {
          const term = inventorySearchTerm.toLowerCase()
          if (!term) return true
          return (
            labor.description.toLowerCase().includes(term)
          )
        })

        const filteredMiscItems = miscItems.filter((misc) => {
          if (misc.is_system_item) return false
          const term = inventorySearchTerm.toLowerCase()
          if (!term) return true
          return misc.description.toLowerCase().includes(term)
        })

        return (
          <div className="space-y-6">
            <div>
              <h1 className="text-2xl font-bold">Inventory</h1>
              <p className="text-muted-foreground">Manage parts and labor items</p>
            </div>

            {error && (
              <div className="p-4 bg-destructive/10 border border-destructive/20 rounded-md text-destructive">
                {error}
              </div>
            )}

            <Tabs defaultValue="parts" className="w-full">
              <div className="flex justify-between items-center mb-4">
                <TabsList>
                  <TabsTrigger value="parts" className="gap-2">
                    <Package className="h-4 w-4" />
                    Parts
                  </TabsTrigger>
                  <TabsTrigger value="labor" className="gap-2">
                    <Wrench className="h-4 w-4" />
                    Labour
                  </TabsTrigger>
                  <TabsTrigger value="misc" className="gap-2">
                    <FileText className="h-4 w-4" />
                    Miscellaneous
                  </TabsTrigger>
                </TabsList>
              </div>

              {/* Search Bar */}
              <div className="relative max-w-sm mb-4">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder="Search inventory..."
                  value={inventorySearchTerm}
                  onChange={(e) => setInventorySearchTerm(e.target.value)}
                  className="pl-9"
                />
              </div>

              {/* Parts Tab */}
              <TabsContent value="parts">
                <div className="bg-card rounded-lg border shadow-sm overflow-hidden">
                  <div className="p-4 border-b flex justify-between items-center">
                    <h2 className="text-lg font-semibold">Parts Inventory ({filteredParts.length.toLocaleString()})</h2>
                    <Button onClick={handleAddPart} className="gap-2">
                      <Plus className="h-4 w-4" />
                      Add Part
                    </Button>
                  </div>

                  {loading ? (
                    <div className="p-8 text-center text-muted-foreground">Loading...</div>
                  ) : (
                    <VirtualizedTable
                      items={filteredParts}
                      rowHeight={52}
                      height="calc(100vh - 360px)"
                      gridCols="grid-cols-[1.4fr_3fr_1fr_1fr_1fr_minmax(110px,auto)]"
                      header={
                        <>
                          <div className={headerCellClass}>Part Number</div>
                          <div className={headerCellClass}>Description</div>
                          <div className={`${headerCellClass} text-right`}>Cost</div>
                          <div className={`${headerCellClass} text-right`}>Markup</div>
                          <div className={`${headerCellClass} text-right`}>Price</div>
                          <div className={`${headerCellClass} text-right`}>Actions</div>
                        </>
                      }
                      getKey={(p) => p.id}
                      emptyMessage={inventorySearchTerm
                        ? "No parts matching your search."
                        : "No parts found. Add your first part to get started."}
                      renderRow={(part) => (
                        <>
                          <div className={`${cellClass} font-medium`}>{part.part_number}</div>
                          <div className={`${cellClass} text-muted-foreground truncate`}>{part.description}</div>
                          <div className={`${cellClass} text-muted-foreground justify-end`}>${part.cost.toFixed(2)}</div>
                          <div className={`${cellClass} text-muted-foreground justify-end`}>{part.markup_percent ?? 0}%</div>
                          <div className={`${cellClass} font-medium justify-end`}>${(part.cost * (1 + (part.markup_percent ?? 0) / 100)).toFixed(2)}</div>
                          <div className={`${cellClass} justify-end gap-1`}>
                            <Button variant="ghost" size="sm" onClick={() => handleEditPart(part)}>
                              <Pencil className="h-4 w-4" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => handleDeletePart(part.id)}
                              className="text-destructive hover:text-destructive hover:bg-destructive/10"
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </div>
                        </>
                      )}
                    />
                  )}
                </div>
              </TabsContent>

              {/* Labor Tab */}
              <TabsContent value="labor">
                <div className="bg-card rounded-lg border shadow-sm overflow-hidden">
                  <div className="p-4 border-b flex justify-between items-center">
                    <h2 className="text-lg font-semibold">Labour Items ({filteredLaborItems.length.toLocaleString()})</h2>
                    <Button onClick={handleAddLabor} className="gap-2">
                      <Plus className="h-4 w-4" />
                      Add Labour
                    </Button>
                  </div>

                  {loading ? (
                    <div className="p-8 text-center text-muted-foreground">Loading...</div>
                  ) : (
                    <VirtualizedTable
                      items={filteredLaborItems}
                      rowHeight={52}
                      height="calc(100vh - 360px)"
                      gridCols="grid-cols-[3fr_1fr_1fr_1fr_1fr_minmax(110px,auto)]"
                      header={
                        <>
                          <div className={headerCellClass}>Labour Description</div>
                          <div className={`${headerCellClass} text-right`}>Hours</div>
                          <div className={`${headerCellClass} text-right`}>Rate</div>
                          <div className={`${headerCellClass} text-right`}>Markup</div>
                          <div className={`${headerCellClass} text-right`}>Total Cost</div>
                          <div className={`${headerCellClass} text-right`}>Actions</div>
                        </>
                      }
                      getKey={(l) => l.id}
                      emptyMessage={inventorySearchTerm
                        ? "No labour items matching your search."
                        : "No labour items found. Add your first labour item to get started."}
                      renderRow={(labor) => (
                        <>
                          <div className={`${cellClass} font-medium truncate`}>{labor.description}</div>
                          <div className={`${cellClass} text-muted-foreground justify-end`}>{labor.hours}</div>
                          <div className={`${cellClass} text-muted-foreground justify-end`}>${labor.rate.toFixed(2)}/hr</div>
                          <div className={`${cellClass} text-muted-foreground justify-end`}>{labor.markup_percent}%</div>
                          <div className={`${cellClass} font-medium justify-end`}>${(labor.hours * labor.rate * (1 + labor.markup_percent / 100)).toFixed(2)}</div>
                          <div className={`${cellClass} justify-end gap-1`}>
                            <Button variant="ghost" size="sm" onClick={() => handleEditLabor(labor)}>
                              <Pencil className="h-4 w-4" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => handleDeleteLabor(labor.id)}
                              className="text-destructive hover:text-destructive hover:bg-destructive/10"
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </div>
                        </>
                      )}
                    />
                  )}
                </div>
              </TabsContent>

              {/* Miscellaneous Tab */}
              <TabsContent value="misc">
                <div className="bg-card rounded-lg border shadow-sm overflow-hidden">
                  <div className="p-4 border-b flex justify-between items-center">
                    <h2 className="text-lg font-semibold">Miscellaneous Items ({filteredMiscItems.length.toLocaleString()})</h2>
                    <Button onClick={handleAddMisc} className="gap-2">
                      <Plus className="h-4 w-4" />
                      Add Misc
                    </Button>
                  </div>

                  {loading ? (
                    <div className="p-8 text-center text-muted-foreground">Loading...</div>
                  ) : (
                    <VirtualizedTable
                      items={filteredMiscItems}
                      rowHeight={52}
                      height="calc(100vh - 360px)"
                      gridCols="grid-cols-[3fr_1fr_1fr_1fr_minmax(110px,auto)]"
                      header={
                        <>
                          <div className={headerCellClass}>Misc Description</div>
                          <div className={`${headerCellClass} text-right`}>Unit Price</div>
                          <div className={`${headerCellClass} text-right`}>Markup</div>
                          <div className={`${headerCellClass} text-right`}>Total Cost</div>
                          <div className={`${headerCellClass} text-right`}>Actions</div>
                        </>
                      }
                      getKey={(m) => m.id}
                      emptyMessage={inventorySearchTerm
                        ? "No miscellaneous items matching your search."
                        : "No miscellaneous items found. Add your first miscellaneous item to get started."}
                      renderRow={(misc) => (
                        <>
                          <div className={`${cellClass} font-medium truncate`}>{misc.description}</div>
                          <div className={`${cellClass} text-muted-foreground justify-end`}>${misc.unit_price.toFixed(2)}</div>
                          <div className={`${cellClass} text-muted-foreground justify-end`}>{misc.markup_percent}%</div>
                          <div className={`${cellClass} font-medium justify-end`}>${(misc.unit_price * (1 + misc.markup_percent / 100)).toFixed(2)}</div>
                          <div className={`${cellClass} justify-end gap-1`}>
                            <Button variant="ghost" size="sm" onClick={() => handleEditMisc(misc)}>
                              <Pencil className="h-4 w-4" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => handleDeleteMisc(misc.id)}
                              className="text-destructive hover:text-destructive hover:bg-destructive/10"
                              title="Delete"
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </div>
                        </>
                      )}
                    />
                  )}
                </div>
              </TabsContent>
            </Tabs>
          </div>
        )
      }

      default:
        return null
    }
  }

  return (
    <>
    <Show when="signed-out">
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="text-center space-y-6">
          <div>
            <h1 className="text-3xl font-bold">UC Velocity</h1>
            <p className="text-muted-foreground">ERP System</p>
          </div>
          <div className="flex gap-3 justify-center">
            <SignInButton mode="modal">
              <Button>Sign In</Button>
            </SignInButton>
            <SignUpButton mode="modal">
              <Button variant="outline">Sign Up</Button>
            </SignUpButton>
          </div>
        </div>
      </div>
    </Show>

    <Show when="signed-in">
    <div className="min-h-screen bg-background flex">
      {/* Sidebar */}
      <aside className="w-64 border-r bg-card flex flex-col">
        <div className="p-6 border-b">
          <div className="flex items-start justify-between">
            <div>
              <h1 className="text-xl font-bold">UC Velocity</h1>
              <p className="text-sm text-muted-foreground">ERP System</p>
            </div>
            <div className="flex items-center gap-1">
              <ThemeToggle />
              <UserButton />
            </div>
          </div>
        </div>

        <ScrollArea className="flex-1">
          <nav className="p-4 space-y-2">
            <Button
              variant={currentView === "projects" || currentView === "project-details" ? "secondary" : "ghost"}
              className="w-full justify-start gap-2"
              onClick={() => navigate("/projects")}
            >
              <FolderOpen className="h-4 w-4" />
              Projects
            </Button>
            <Button
              variant={currentView === "profiles" ? "secondary" : "ghost"}
              className="w-full justify-start gap-2"
              onClick={() => navigate("/profiles")}
            >
              <Users className="h-4 w-4" />
              Profiles
            </Button>
            <Button
              variant={currentView === "inventory" ? "secondary" : "ghost"}
              className="w-full justify-start gap-2"
              onClick={() => navigate("/inventory")}
            >
              <Boxes className="h-4 w-4" />
              Inventory
            </Button>
            <Button
              variant={currentView === "reports" ? "secondary" : "ghost"}
              className="w-full justify-start gap-2"
              onClick={() => navigate("/reports")}
            >
              <BarChart3 className="h-4 w-4" />
              Reports
            </Button>
            <Button
              variant={currentView === "settings" ? "secondary" : "ghost"}
              className="w-full justify-start gap-2"
              onClick={() => navigate("/settings")}
            >
              <Settings className="h-4 w-4" />
              Settings
            </Button>
            {isAdmin && (
              <Button
                variant={currentView === "migration" ? "secondary" : "ghost"}
                className="w-full justify-start gap-2"
                onClick={() => navigate("/admin/migration")}
              >
                <DatabaseZap className="h-4 w-4" />
                Migration
              </Button>
            )}
          </nav>
        </ScrollArea>
      </aside>

      {/* Main Content */}
      <main className="flex-1 overflow-auto">
        <Suspense fallback={<PageFallback />}>
          {currentView === "project-details" ? (
            renderContent()
          ) : (
            <div className="p-6">{renderContent()}</div>
          )}
        </Suspense>
      </main>

      {/* Part Dialog */}
      <Dialog open={partDialogOpen} onOpenChange={handlePartDialogClose}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editingPart ? "Edit Part" : "Add New Part"}</DialogTitle>
            <DialogDescription>
              {editingPart ? "Update the part details below." : "Create a new part in the inventory."}
            </DialogDescription>
          </DialogHeader>
          <PartForm
            part={editingPart ?? undefined}
            onSuccess={() => {
              handlePartDialogClose(false)
              fetchInventory()
            }}
            onCancel={() => handlePartDialogClose(false)}
          />
        </DialogContent>
      </Dialog>

      {/* Labor Dialog */}
      <Dialog open={laborDialogOpen} onOpenChange={handleLaborDialogClose}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{editingLabor ? "Edit Labour" : "Add New Labour"}</DialogTitle>
            <DialogDescription>
              {editingLabor ? "Update the labour item details below." : "Create a new labour item."}
            </DialogDescription>
          </DialogHeader>
          <LaborForm
            labor={editingLabor ?? undefined}
            onSuccess={() => {
              handleLaborDialogClose(false)
              fetchInventory()
            }}
            onCancel={() => handleLaborDialogClose(false)}
          />
        </DialogContent>
      </Dialog>

      {/* Miscellaneous Dialog */}
      <Dialog open={miscDialogOpen} onOpenChange={handleMiscDialogClose}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{editingMisc ? "Edit Miscellaneous" : "Add New Miscellaneous"}</DialogTitle>
            <DialogDescription>
              {editingMisc ? "Update the miscellaneous item details below." : "Create a new miscellaneous item."}
            </DialogDescription>
          </DialogHeader>
          <MiscForm
            misc={editingMisc ?? undefined}
            onSuccess={() => {
              handleMiscDialogClose(false)
              fetchInventory()
            }}
            onCancel={() => handleMiscDialogClose(false)}
          />
        </DialogContent>
      </Dialog>

      <Toaster />
    </div>
    </Show>
    </>
  )
}

export default App
