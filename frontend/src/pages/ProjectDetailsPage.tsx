import { useState, useEffect, useRef, useCallback, useMemo, lazy, Suspense } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { StatusBadge } from "@/components/ui/status-badge"
import { formatDate } from "@/lib/format"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
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
import { SearchableSelect } from "@/components/ui/searchable-select"
import type { SearchableSelectOption } from "@/components/ui/searchable-select"
import { ProfileForm } from "@/components/forms/ProfileForm"
import { Label } from "@/components/ui/label"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import { api } from "@/api/client"
import { toast } from "@/hooks/use-toast"
import type { ProjectFull, Profile, Invoice } from "@/types"
import {
  ArrowLeft,
  Plus,
  FileText,
  ShoppingCart,
  Trash2,
  User,
  Mail,
  Phone,
  Receipt,
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  Clock,
  Search,
} from "lucide-react"

const QuoteEditor = lazy(() =>
  import("@/components/editors/QuoteEditor").then((m) => ({ default: m.QuoteEditor }))
)
const POEditor = lazy(() =>
  import("@/components/editors/POEditor").then((m) => ({ default: m.POEditor }))
)
const InvoiceEditor = lazy(() =>
  import("@/components/editors/InvoiceEditor").then((m) => ({ default: m.InvoiceEditor }))
)

function EditorFallback() {
  return (
    <div className="h-full flex items-center justify-center text-muted-foreground">
      <div className="text-sm">Loading editor…</div>
    </div>
  )
}

interface ProjectDetailsPageProps {
  projectId: number
  onBack: () => void
  initialDoc?: { type: "quote" | "po" | "invoice"; id: number } | null
}

type DocumentType = "quote" | "po" | "invoice"
type SelectedDocument = { type: DocumentType; id: number } | null
type TabKey = "quotes" | "pos" | "invoices"

type RecentDoc = {
  type: DocumentType
  id: number
  label: string
  sublabel?: string
  ts: number
}

const RECENT_DOCS_KEY = (projectId: number) => `ucv:recentDocs:${projectId}`

function tabForType(t: DocumentType): TabKey {
  if (t === "quote") return "quotes"
  if (t === "po") return "pos"
  return "invoices"
}

function isTabKey(value: string | null): value is TabKey {
  return value === "quotes" || value === "pos" || value === "invoices"
}

function loadRecentDocs(projectId: number): RecentDoc[] {
  try {
    const raw = localStorage.getItem(RECENT_DOCS_KEY(projectId))
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.slice(0, 3)
  } catch {
    return []
  }
}

export function ProjectDetailsPage({ projectId, onBack, initialDoc }: ProjectDetailsPageProps) {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [project, setProject] = useState<ProjectFull | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedDoc, setSelectedDoc] = useState<SelectedDocument>(() =>
    initialDoc ? { type: initialDoc.type, id: initialDoc.id } : null
  )

  // Sidebar nav state — tab lives in the URL search param ?tab= so it survives
  // refresh and is part of every shareable link.
  const urlTab = searchParams.get("tab")
  const initialTab: TabKey = isTabKey(urlTab)
    ? urlTab
    : initialDoc ? tabForType(initialDoc.type) : "quotes"
  const [activeTab, setActiveTab] = useState<TabKey>(initialTab)
  const [filter, setFilter] = useState("")
  const [debouncedFilter, setDebouncedFilter] = useState("")
  const [highlightedIndex, setHighlightedIndex] = useState(0)
  const [cmdOpen, setCmdOpen] = useState(false)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [recentDocs, setRecentDocs] = useState<RecentDoc[]>(() => loadRecentDocs(projectId))

  // Delete confirmation (replaces window.confirm)
  const [deleteConfirm, setDeleteConfirm] = useState<{ type: "quote" | "po"; id: number } | null>(null)

  // Unsaved changes navigation guard
  const editorDirtyRef = useRef(false)
  const [navConfirmOpen, setNavConfirmOpen] = useState(false)
  const pendingNavAction = useRef<(() => void) | null>(null)

  const handleEditorDirtyChange = useCallback((isDirty: boolean) => {
    editorDirtyRef.current = isDirty
  }, [])

  // Guarded navigation: checks dirty state before allowing navigation
  const guardedNavigate = useCallback((action: () => void) => {
    if (editorDirtyRef.current) {
      pendingNavAction.current = action
      setNavConfirmOpen(true)
    } else {
      action()
    }
  }, [])

  const handleConfirmNavigation = useCallback(() => {
    setNavConfirmOpen(false)
    editorDirtyRef.current = false
    pendingNavAction.current?.()
    pendingNavAction.current = null
  }, [])

  const handleCancelNavigation = useCallback(() => {
    setNavConfirmOpen(false)
    pendingNavAction.current = null
  }, [])

  // Dialog states
  const [quoteDialogOpen, setQuoteDialogOpen] = useState(false)
  const [poDialogOpen, setPoDialogOpen] = useState(false)
  const [vendors, setVendors] = useState<Profile[]>([])
  const [selectedVendorId, setSelectedVendorId] = useState<string>("")

  // Invoices from all quotes
  const [invoices, setInvoices] = useState<(Invoice & { quoteId: number; quoteNumber: string })[]>([])

  const fetchProject = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await api.projects.get(projectId)
      setProject(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch project")
    } finally {
      setLoading(false)
    }
  }

  const fetchVendors = async () => {
    try {
      const data = await api.profiles.getAll("vendor")
      setVendors(data)
    } catch (err) {
      console.error("Failed to fetch vendors", err)
    }
  }

  const fetchInvoices = async (quotes: { id: number; quote_number: string }[]) => {
    try {
      // Fan out once per quote instead of waiting on each round-trip serially.
      const results = await Promise.all(quotes.map((q) => api.quotes.getInvoices(q.id)))
      const allInvoices = results.flatMap((arr, i) =>
        arr.map((inv) => ({ ...inv, quoteId: quotes[i].id, quoteNumber: quotes[i].quote_number }))
      )
      allInvoices.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
      setInvoices(allInvoices)
    } catch (err) {
      console.error("Failed to fetch invoices", err)
    }
  }

  useEffect(() => {
    fetchProject()
    fetchVendors()
  }, [projectId])

  // Fetch invoices when project is loaded
  useEffect(() => {
    if (project?.quotes) {
      fetchInvoices(project.quotes)
    }
  }, [project?.quotes])

  // Debounce filter (150ms)
  useEffect(() => {
    const t = setTimeout(() => setDebouncedFilter(filter), 150)
    return () => clearTimeout(t)
  }, [filter])

  // Reset keyboard highlight when filter or tab changes
  useEffect(() => {
    setHighlightedIndex(0)
  }, [activeTab, debouncedFilter])

  // Sync active tab to the URL search param (?tab=) so it survives refresh
  // and forms part of every shareable link.
  useEffect(() => {
    const current = searchParams.get("tab")
    if (current !== activeTab) {
      const next = new URLSearchParams(searchParams)
      next.set("tab", activeTab)
      setSearchParams(next, { replace: true })
    }
  }, [activeTab, searchParams, setSearchParams])

  // When the URL changes (browser back/forward, deep-link), reflect the
  // incoming initialDoc into local selection so the editor opens the new doc.
  useEffect(() => {
    if (initialDoc) {
      setSelectedDoc({ type: initialDoc.type, id: initialDoc.id })
    }
  }, [initialDoc?.type, initialDoc?.id])

  // Global Cmd/Ctrl+K opens command palette
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) {
        e.preventDefault()
        setCmdOpen((v) => !v)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  // Track recently viewed when a doc becomes selected (or when project data refreshes
  // and a previously-stale entry is now resolvable)
  useEffect(() => {
    if (!selectedDoc || !project) return
    let label = ""
    let sublabel: string | undefined
    if (selectedDoc.type === "quote") {
      const q = project.quotes.find((x) => x.id === selectedDoc.id)
      if (!q) return
      label = `Quote ${q.quote_number}`
      sublabel = q.status
    } else if (selectedDoc.type === "po") {
      const p = project.purchase_orders.find((x) => x.id === selectedDoc.id)
      if (!p) return
      label = `PO ${p.po_number}`
      sublabel = p.vendor.name
    } else {
      const inv = invoices.find((x) => x.id === selectedDoc.id)
      if (!inv) return
      label = `Invoice ${inv.invoice_number ?? `${inv.id} - ${inv.quoteNumber}`}`
      sublabel = inv.status
    }
    setRecentDocs((prev) => {
      const without = prev.filter((d) => !(d.type === selectedDoc.type && d.id === selectedDoc.id))
      const next: RecentDoc[] = [
        { type: selectedDoc.type, id: selectedDoc.id, label, sublabel, ts: Date.now() },
        ...without,
      ].slice(0, 3)
      try {
        localStorage.setItem(RECENT_DOCS_KEY(projectId), JSON.stringify(next))
      } catch {
        // ignore
      }
      return next
    })
  }, [selectedDoc, project, invoices, projectId])

  // Open a doc through the unsaved-changes guard; ensures the right tab is active
  // and the URL reflects the selected doc so back/forward and bookmarks work.
  const openDoc = useCallback((type: DocumentType, id: number) => {
    guardedNavigate(() => {
      const tab = tabForType(type)
      setActiveTab(tab)
      setSelectedDoc({ type, id })
      const seg = type === "quote" ? "quotes" : type === "po" ? "pos" : "invoices"
      navigate(`/projects/${projectId}/${seg}/${id}?tab=${tab}`)
    })
  }, [guardedNavigate, navigate, projectId])

  const handleCreateQuote = async () => {
    try {
      const quote = await api.quotes.create({ project_id: projectId })
      setQuoteDialogOpen(false)
      await fetchProject()
      setActiveTab("quotes")
      setSelectedDoc({ type: "quote", id: quote.id })
      navigate(`/projects/${projectId}/quotes/${quote.id}?tab=quotes`)
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Failed to create quote",
        description: err instanceof Error ? err.message : "Unknown error",
      })
    }
  }

  const handleCreatePO = async () => {
    if (!selectedVendorId) return
    try {
      const po = await api.purchaseOrders.create({
        project_id: projectId,
        vendor_id: parseInt(selectedVendorId),
      })
      setPoDialogOpen(false)
      setSelectedVendorId("")
      await fetchProject()
      setActiveTab("pos")
      setSelectedDoc({ type: "po", id: po.id })
      navigate(`/projects/${projectId}/pos/${po.id}?tab=pos`)
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Failed to create purchase order",
        description: err instanceof Error ? err.message : "Unknown error",
      })
    }
  }

  const requestDeleteQuote = (id: number, e: React.MouseEvent) => {
    e.stopPropagation()
    setDeleteConfirm({ type: "quote", id })
  }

  const requestDeletePO = (id: number, e: React.MouseEvent) => {
    e.stopPropagation()
    setDeleteConfirm({ type: "po", id })
  }

  const performDelete = async () => {
    if (!deleteConfirm) return
    const { type, id } = deleteConfirm
    setDeleteConfirm(null)
    try {
      if (type === "quote") {
        await api.quotes.delete(id)
      } else {
        await api.purchaseOrders.delete(id)
      }
      if (selectedDoc?.type === type && selectedDoc.id === id) {
        setSelectedDoc(null)
        // Drop the doc segment from the URL — keep the active tab in the query string.
        navigate(`/projects/${projectId}?tab=${activeTab}`)
      }
      fetchProject()
    } catch (err) {
      toast({
        variant: "destructive",
        title: `Failed to delete ${type === "po" ? "purchase order" : "quote"}`,
        description: err instanceof Error ? err.message : "Unknown error",
      })
    }
  }

  // Filtered lists (memoized against debounced filter input)
  const filteredQuotes = useMemo(() => {
    if (!project) return []
    const n = debouncedFilter.trim().toLowerCase()
    if (!n) return project.quotes
    return project.quotes.filter((q) => {
      const hay = [
        q.quote_number,
        q.status,
        q.client_po_number ?? "",
        q.hardware_schedule_version ?? "",
      ].join(" ").toLowerCase()
      return hay.includes(n)
    })
  }, [project, debouncedFilter])

  const filteredPOs = useMemo(() => {
    if (!project) return []
    const n = debouncedFilter.trim().toLowerCase()
    if (!n) return project.purchase_orders
    return project.purchase_orders.filter((p) => {
      const hay = [
        p.po_number,
        p.status,
        p.vendor.name,
        new Date(p.created_at).toLocaleDateString(),
      ].join(" ").toLowerCase()
      return hay.includes(n)
    })
  }, [project, debouncedFilter])

  const filteredInvoices = useMemo(() => {
    const n = debouncedFilter.trim().toLowerCase()
    if (!n) return invoices
    return invoices.filter((inv) => {
      const hay = [
        `invoice ${inv.id}`,
        inv.status,
        inv.quoteNumber,
        new Date(inv.created_at).toLocaleDateString(),
      ].join(" ").toLowerCase()
      return hay.includes(n)
    })
  }, [invoices, debouncedFilter])

  // Active items power keyboard nav (Up/Down/Enter)
  const activeItems = useMemo(() => {
    if (activeTab === "quotes") return filteredQuotes.map((q) => ({ type: "quote" as const, id: q.id }))
    if (activeTab === "pos") return filteredPOs.map((p) => ({ type: "po" as const, id: p.id }))
    return filteredInvoices.map((i) => ({ type: "invoice" as const, id: i.id }))
  }, [activeTab, filteredQuotes, filteredPOs, filteredInvoices])

  const onSidebarKeyDown = (e: React.KeyboardEvent) => {
    if (activeItems.length === 0) return
    if (e.key === "ArrowDown") {
      e.preventDefault()
      setHighlightedIndex((i) => Math.min(activeItems.length - 1, i + 1))
    } else if (e.key === "ArrowUp") {
      e.preventDefault()
      setHighlightedIndex((i) => Math.max(0, i - 1))
    } else if (e.key === "Enter") {
      const target = e.target as HTMLElement
      // Let buttons (delete trash, etc.) handle their own Enter
      if (target.tagName === "BUTTON") return
      e.preventDefault()
      const item = activeItems[Math.min(highlightedIndex, activeItems.length - 1)]
      if (item) openDoc(item.type, item.id)
    }
  }

  // Drop stale recents that no longer exist (e.g., deleted docs); render-time only.
  const validRecents = useMemo(() => {
    if (!project) return []
    return recentDocs.filter((d) => {
      if (d.type === "quote") return project.quotes.some((q) => q.id === d.id)
      if (d.type === "po") return project.purchase_orders.some((p) => p.id === d.id)
      return invoices.some((i) => i.id === d.id)
    })
  }, [recentDocs, project, invoices])

  if (loading) {
    return <div className="p-8 text-center text-muted-foreground">Loading...</div>
  }

  if (error || !project) {
    return (
      <div className="p-8">
        <div className="p-4 bg-destructive/10 border border-destructive/20 rounded-md text-destructive">
          {error || "Project not found"}
        </div>
        <Button variant="outline" onClick={onBack} className="mt-4">
          <ArrowLeft className="h-4 w-4 mr-2" />
          Back to Projects
        </Button>
      </div>
    )
  }

  const counts = {
    quotes: project.quotes.length,
    pos: project.purchase_orders.length,
    invoices: invoices.length,
  }

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="border-b bg-card p-4">
        <Button variant="ghost" size="sm" onClick={() => guardedNavigate(onBack)} className="mb-2">
          <ArrowLeft className="h-4 w-4 mr-2" />
          Back to Projects
        </Button>
        <div className="flex justify-between items-start">
          <div>
            <h1 className="text-2xl font-bold">{project.name}</h1>
            <div className="flex items-center gap-4 mt-1 text-sm text-muted-foreground">
              <span className="font-mono">UCA: {project.uca_project_number}</span>
              {project.ucsh_project_number && (
                <span>UCSH: {project.ucsh_project_number}</span>
              )}
              <span>Created: {formatDate(project.created_on)}</span>
            </div>
            <div className="flex items-center gap-4 mt-2 text-sm text-muted-foreground">
              <span className="flex items-center gap-1">
                <User className="h-4 w-4" />
                {project.customer.name}
              </span>
              {project.customer.contacts?.[0]?.email && (
                <span className="flex items-center gap-1">
                  <Mail className="h-4 w-4" />
                  {project.customer.contacts[0].email}
                </span>
              )}
              {project.customer.contacts?.[0]?.phone_numbers?.[0]?.number && (
                <span className="flex items-center gap-1">
                  <Phone className="h-4 w-4" />
                  {project.customer.contacts[0].phone_numbers[0].number}
                </span>
              )}
            </div>
          </div>
          <StatusBadge status={project.status} />
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Sidebar */}
        <div
          className={`${sidebarCollapsed ? "w-12" : "w-72"} border-r bg-muted/30 flex flex-col transition-[width] duration-150`}
        >
          {sidebarCollapsed ? (
            <CollapsedSidebar
              activeTab={activeTab}
              counts={counts}
              onExpand={() => setSidebarCollapsed(false)}
              onSelectTab={(t) => {
                setActiveTab(t)
                setSidebarCollapsed(false)
              }}
              onNewQuote={() => setQuoteDialogOpen(true)}
              onNewPO={() => setPoDialogOpen(true)}
            />
          ) : (
            <>
              <div className="p-3 border-b flex items-center justify-between gap-2">
                <h2 className="font-semibold text-xs text-muted-foreground uppercase tracking-wide">
                  Documents
                </h2>
                <div className="flex items-center gap-1">
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button size="sm" variant="outline" className="h-7 gap-1 px-2">
                        <Plus className="h-3.5 w-3.5" />
                        <span className="text-xs">New</span>
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onSelect={() => setQuoteDialogOpen(true)}>
                        <FileText className="h-4 w-4 mr-2" />
                        New Quote
                      </DropdownMenuItem>
                      <DropdownMenuItem onSelect={() => setPoDialogOpen(true)}>
                        <ShoppingCart className="h-4 w-4 mr-2" />
                        New Purchase Order
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                  {selectedDoc !== null && (
                    <Button
                      size="icon"
                      variant="ghost"
                      className="h-7 w-7"
                      onClick={() => setSidebarCollapsed(true)}
                      aria-label="Collapse sidebar"
                    >
                      <ChevronLeft className="h-4 w-4" />
                    </Button>
                  )}
                </div>
              </div>

              <Tabs
                value={activeTab}
                onValueChange={(v) => setActiveTab(v as TabKey)}
                className="flex-1 flex flex-col min-h-0"
              >
                <div className="px-3 pt-3 pb-2 flex-none space-y-2" onKeyDown={onSidebarKeyDown}>
                  <TabsList className="w-full grid grid-cols-3 h-8">
                    <TabsTrigger value="quotes" className="text-xs px-1">
                      Quotes ({counts.quotes})
                    </TabsTrigger>
                    <TabsTrigger value="pos" className="text-xs px-1">
                      POs ({counts.pos})
                    </TabsTrigger>
                    <TabsTrigger value="invoices" className="text-xs px-1">
                      Invoices ({counts.invoices})
                    </TabsTrigger>
                  </TabsList>
                  <div className="relative">
                    <Search className="h-3.5 w-3.5 absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
                    <Input
                      value={filter}
                      onChange={(e) => setFilter(e.target.value)}
                      placeholder="Filter… (⌘K to search all)"
                      className="h-8 text-xs pl-7"
                    />
                  </div>
                </div>
                <div className="flex-1 min-h-0 px-3 pb-3" onKeyDown={onSidebarKeyDown}>
                  <ScrollArea className="h-full">
                    <div className="space-y-1 pr-2">
                      {activeTab === "quotes" && (
                        filteredQuotes.length === 0 ? (
                          <EmptyListMessage
                            hasFilter={debouncedFilter.length > 0}
                            emptyText="No quotes yet"
                          />
                        ) : (
                          filteredQuotes.map((quote, idx) => (
                            <QuoteRow
                              key={quote.id}
                              quote={quote}
                              isSelected={selectedDoc?.type === "quote" && selectedDoc.id === quote.id}
                              isHighlighted={highlightedIndex === idx}
                              onSelect={() => openDoc("quote", quote.id)}
                              onDelete={(e) => requestDeleteQuote(quote.id, e)}
                            />
                          ))
                        )
                      )}
                      {activeTab === "pos" && (
                        filteredPOs.length === 0 ? (
                          <EmptyListMessage
                            hasFilter={debouncedFilter.length > 0}
                            emptyText="No purchase orders yet"
                          />
                        ) : (
                          filteredPOs.map((po, idx) => (
                            <PORow
                              key={po.id}
                              po={po}
                              isSelected={selectedDoc?.type === "po" && selectedDoc.id === po.id}
                              isHighlighted={highlightedIndex === idx}
                              onSelect={() => openDoc("po", po.id)}
                              onDelete={(e) => requestDeletePO(po.id, e)}
                            />
                          ))
                        )
                      )}
                      {activeTab === "invoices" && (
                        filteredInvoices.length === 0 ? (
                          <EmptyListMessage
                            hasFilter={debouncedFilter.length > 0}
                            emptyText="No invoices yet"
                          />
                        ) : (
                          filteredInvoices.map((invoice, idx) => (
                            <InvoiceRow
                              key={invoice.id}
                              invoice={invoice}
                              isSelected={selectedDoc?.type === "invoice" && selectedDoc.id === invoice.id}
                              isHighlighted={highlightedIndex === idx}
                              onSelect={() => openDoc("invoice", invoice.id)}
                            />
                          ))
                        )
                      )}
                    </div>
                  </ScrollArea>
                </div>
              </Tabs>
            </>
          )}
        </div>

        {/* Document Editor */}
        <div className="flex-1 overflow-auto">
          {selectedDoc === null ? (
            <EmptyEditorState
              recents={validRecents}
              onOpenRecent={(d) => openDoc(d.type, d.id)}
              onOpenPalette={() => setCmdOpen(true)}
            />
          ) : (
            <Suspense fallback={<EditorFallback />}>
              {selectedDoc.type === "quote" ? (
                <QuoteEditor
                  quoteId={selectedDoc.id}
                  onUpdate={() => {
                    fetchProject()
                    if (project?.quotes) fetchInvoices(project.quotes)
                  }}
                />
              ) : selectedDoc.type === "po" ? (
                <POEditor
                  poId={selectedDoc.id}
                  onUpdate={fetchProject}
                  onSelectPO={(newPoId) => setSelectedDoc({ type: "po", id: newPoId })}
                  onDirtyStateChange={handleEditorDirtyChange}
                />
              ) : (
                <InvoiceEditor invoiceId={selectedDoc.id} onUpdate={() => {
                  fetchProject()
                  if (project?.quotes) fetchInvoices(project.quotes)
                }} />
              )}
            </Suspense>
          )}
        </div>
      </div>

      {/* Create Quote Dialog */}
      <Dialog open={quoteDialogOpen} onOpenChange={setQuoteDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create New Quote</DialogTitle>
            <DialogDescription>
              Create a new quote for this project.
            </DialogDescription>
          </DialogHeader>
          <div className="pt-4">
            <Button onClick={handleCreateQuote} className="w-full">
              Create Quote
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Create PO Dialog */}
      <Dialog open={poDialogOpen} onOpenChange={setPoDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create Purchase Order</DialogTitle>
            <DialogDescription>
              Create a new purchase order for a vendor.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 pt-4">
            <div className="space-y-2">
              <Label>Vendor</Label>
              <SearchableSelect<Profile>
                options={vendors.map((vendor): SearchableSelectOption => ({
                  value: vendor.id.toString(),
                  label: vendor.name,
                }))}
                value={selectedVendorId}
                onChange={setSelectedVendorId}
                placeholder="Select a vendor"
                searchPlaceholder="Search vendors..."
                emptyMessage="No vendors found."
                allowCreate={true}
                createLabel="Create New Vendor"
                createDialogTitle="Create New Vendor"
                createForm={<ProfileForm defaultType="vendor" />}
                onCreateSuccess={(newVendor) => {
                  setVendors([...vendors, newVendor])
                  setSelectedVendorId(newVendor.id.toString())
                }}
              />
            </div>
            <Button
              onClick={handleCreatePO}
              className="w-full"
              disabled={!selectedVendorId}
            >
              Create Purchase Order
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Unsaved Changes Navigation Confirmation */}
      <AlertDialog open={navConfirmOpen} onOpenChange={setNavConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-amber-500" />
              Unsaved Changes
            </AlertDialogTitle>
            <AlertDialogDescription>
              You have unsaved changes that will be lost if you navigate away. Are you sure you want to leave?
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={handleCancelNavigation}>Stay</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleConfirmNavigation}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Leave Without Saving
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Delete Confirmation */}
      <AlertDialog
        open={deleteConfirm !== null}
        onOpenChange={(open) => { if (!open) setDeleteConfirm(null) }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Delete {deleteConfirm?.type === "po" ? "purchase order" : "quote"}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={performDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Command Palette (Ctrl/Cmd+K) */}
      <CommandDialog open={cmdOpen} onOpenChange={setCmdOpen}>
        <CommandInput placeholder="Search quotes, POs, and invoices in this project…" />
        <CommandList>
          <CommandEmpty>No matching documents</CommandEmpty>
          {project.quotes.length > 0 && (
            <CommandGroup heading="Quotes">
              {project.quotes.map((q) => (
                <CommandItem
                  key={`q-${q.id}`}
                  value={`quote ${q.quote_number} ${q.status} ${q.client_po_number ?? ""} ${q.hardware_schedule_version ?? ""}`}
                  onSelect={() => {
                    setCmdOpen(false)
                    openDoc("quote", q.id)
                  }}
                >
                  <FileText className="h-4 w-4" />
                  <span className="flex-1">{q.quote_number}</span>
                  <StatusBadge status={q.status} className="text-[10px]" />
                </CommandItem>
              ))}
            </CommandGroup>
          )}
          {project.purchase_orders.length > 0 && (
            <CommandGroup heading="Purchase Orders">
              {project.purchase_orders.map((p) => (
                <CommandItem
                  key={`p-${p.id}`}
                  value={`po ${p.po_number} ${p.status} ${p.vendor.name}`}
                  onSelect={() => {
                    setCmdOpen(false)
                    openDoc("po", p.id)
                  }}
                >
                  <ShoppingCart className="h-4 w-4" />
                  <span className="flex-1">{p.po_number}</span>
                  <span className="text-xs text-muted-foreground truncate max-w-[10rem]">{p.vendor.name}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          )}
          {invoices.length > 0 && (
            <CommandGroup heading="Invoices">
              {invoices.map((inv) => (
                <CommandItem
                  key={`i-${inv.id}`}
                  value={`invoice ${inv.id} ${inv.status} ${inv.quoteNumber}`}
                  onSelect={() => {
                    setCmdOpen(false)
                    openDoc("invoice", inv.id)
                  }}
                >
                  <Receipt className="h-4 w-4" />
                  <span className="flex-1">Invoice {inv.invoice_number ?? `${inv.id} - ${inv.quoteNumber}`}</span>
                  <StatusBadge status={inv.status} className="text-[10px]" />
                </CommandItem>
              ))}
            </CommandGroup>
          )}
        </CommandList>
      </CommandDialog>
    </div>
  )
}

// ===== Subcomponents =====

function EmptyListMessage({ hasFilter, emptyText }: { hasFilter: boolean; emptyText: string }) {
  return (
    <p className="text-xs text-muted-foreground py-2 px-1">
      {hasFilter ? "No matches" : emptyText}
    </p>
  )
}

interface QuoteRowProps {
  quote: ProjectFull["quotes"][number]
  isSelected: boolean
  isHighlighted: boolean
  onSelect: () => void
  onDelete: (e: React.MouseEvent) => void
}

function QuoteRow({ quote, isSelected, isHighlighted, onSelect, onDelete }: QuoteRowProps) {
  return (
    <div
      className={`flex items-center justify-between p-2 rounded-md cursor-pointer group transition-colors ${
        isSelected
          ? "bg-primary/10 text-primary"
          : isHighlighted
          ? "bg-muted ring-1 ring-ring/40"
          : "hover:bg-muted"
      }`}
      onClick={onSelect}
    >
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium flex items-center gap-2">
          <span className="truncate">{quote.quote_number}</span>
          <StatusBadge status={quote.status} className="text-[10px] px-1.5 py-0" />
        </div>
        <div className="text-xs text-muted-foreground truncate">
          PO# {quote.client_po_number || "—"}
        </div>
        <div className="text-xs text-muted-foreground truncate">
          HW Schedule {quote.hardware_schedule_version || "—"}
        </div>
      </div>
      <Button
        size="sm"
        variant="ghost"
        className="opacity-0 group-hover:opacity-100 h-6 w-6 p-0 flex-shrink-0"
        onClick={onDelete}
        aria-label={`Delete quote ${quote.quote_number}`}
      >
        <Trash2 className="h-3 w-3 text-destructive" />
      </Button>
    </div>
  )
}

interface PORowProps {
  po: ProjectFull["purchase_orders"][number]
  isSelected: boolean
  isHighlighted: boolean
  onSelect: () => void
  onDelete: (e: React.MouseEvent) => void
}

function PORow({ po, isSelected, isHighlighted, onSelect, onDelete }: PORowProps) {
  return (
    <div
      className={`flex items-center justify-between p-2 rounded-md cursor-pointer group transition-colors ${
        isSelected
          ? "bg-primary/10 text-primary"
          : isHighlighted
          ? "bg-muted ring-1 ring-ring/40"
          : "hover:bg-muted"
      }`}
      onClick={onSelect}
    >
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium truncate">{po.po_number}</div>
        <div className="text-xs text-muted-foreground truncate">{po.vendor.name}</div>
      </div>
      <Button
        size="sm"
        variant="ghost"
        className="opacity-0 group-hover:opacity-100 h-6 w-6 p-0 flex-shrink-0"
        onClick={onDelete}
        aria-label={`Delete purchase order ${po.po_number}`}
      >
        <Trash2 className="h-3 w-3 text-destructive" />
      </Button>
    </div>
  )
}

interface InvoiceRowProps {
  invoice: Invoice & { quoteId: number; quoteNumber: string }
  isSelected: boolean
  isHighlighted: boolean
  onSelect: () => void
}

function InvoiceRow({ invoice, isSelected, isHighlighted, onSelect }: InvoiceRowProps) {
  return (
    <div
      className={`flex items-center justify-between p-2 rounded-md cursor-pointer group transition-colors ${
        isSelected
          ? "bg-primary/10 text-primary"
          : isHighlighted
          ? "bg-muted ring-1 ring-ring/40"
          : "hover:bg-muted"
      }`}
      onClick={onSelect}
    >
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium flex items-center gap-2">
          <span className="truncate">Invoice {invoice.invoice_number ?? `${invoice.id} - ${invoice.quoteNumber}`}</span>
          <StatusBadge status={invoice.status} className="text-[10px] px-1.5 py-0" />
        </div>
        <div className="text-xs text-muted-foreground">
          {formatDate(invoice.created_at)}
        </div>
      </div>
    </div>
  )
}

interface CollapsedSidebarProps {
  activeTab: TabKey
  counts: { quotes: number; pos: number; invoices: number }
  onExpand: () => void
  onSelectTab: (t: TabKey) => void
  onNewQuote: () => void
  onNewPO: () => void
}

function CollapsedSidebar({
  activeTab,
  counts,
  onExpand,
  onSelectTab,
  onNewQuote,
  onNewPO,
}: CollapsedSidebarProps) {
  const tabs: { key: TabKey; Icon: typeof FileText; label: string; count: number }[] = [
    { key: "quotes", Icon: FileText, label: "Quotes", count: counts.quotes },
    { key: "pos", Icon: ShoppingCart, label: "Purchase Orders", count: counts.pos },
    { key: "invoices", Icon: Receipt, label: "Invoices", count: counts.invoices },
  ]
  return (
    <div className="flex flex-col items-center py-3 gap-2">
      <Button
        size="icon"
        variant="ghost"
        onClick={onExpand}
        className="h-8 w-8"
        aria-label="Expand sidebar"
      >
        <ChevronRight className="h-4 w-4" />
      </Button>
      <Separator className="w-6" />
      {tabs.map(({ key, Icon, label, count }) => (
        <Button
          key={key}
          size="icon"
          variant={activeTab === key ? "secondary" : "ghost"}
          onClick={() => onSelectTab(key)}
          aria-label={`${label} (${count})`}
          className="h-8 w-8 relative"
        >
          <Icon className="h-4 w-4" />
          {count > 0 && (
            <span className="absolute -top-1 -right-1 bg-primary text-primary-foreground rounded-full text-[9px] h-4 min-w-[16px] px-1 flex items-center justify-center leading-none">
              {count}
            </span>
          )}
        </Button>
      ))}
      <Separator className="w-6" />
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button size="icon" variant="ghost" className="h-8 w-8" aria-label="New document">
            <Plus className="h-4 w-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent side="right" align="start">
          <DropdownMenuItem onSelect={onNewQuote}>
            <FileText className="h-4 w-4 mr-2" />
            New Quote
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={onNewPO}>
            <ShoppingCart className="h-4 w-4 mr-2" />
            New Purchase Order
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}

interface EmptyEditorStateProps {
  recents: RecentDoc[]
  onOpenRecent: (d: RecentDoc) => void
  onOpenPalette: () => void
}

function EmptyEditorState({ recents, onOpenRecent, onOpenPalette }: EmptyEditorStateProps) {
  if (recents.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-muted-foreground p-8">
        <div className="text-center">
          <FileText className="h-12 w-12 mx-auto mb-4 opacity-50" />
          <p>Select a document from the sidebar or create a new one</p>
          <p className="text-xs mt-3">
            Press{" "}
            <kbd className="px-1.5 py-0.5 bg-muted rounded text-foreground font-mono text-[10px]">⌘K</kbd>
            {" "}or{" "}
            <kbd className="px-1.5 py-0.5 bg-muted rounded text-foreground font-mono text-[10px]">Ctrl K</kbd>
            {" "}for global search
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="h-full flex items-center justify-center text-muted-foreground p-8">
      <div className="w-full max-w-md">
        <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-3 flex items-center gap-2">
          <Clock className="h-4 w-4" />
          Recently viewed
        </div>
        <div className="space-y-2">
          {recents.map((d) => {
            const Icon = d.type === "quote" ? FileText : d.type === "po" ? ShoppingCart : Receipt
            return (
              <button
                key={`${d.type}-${d.id}`}
                className="w-full text-left p-3 rounded-md border bg-card hover:bg-accent transition-colors flex items-center gap-3"
                onClick={() => onOpenRecent(d)}
              >
                <Icon className="h-4 w-4 text-muted-foreground flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-sm text-foreground truncate">{d.label}</div>
                  {d.sublabel && (
                    <div className="text-xs text-muted-foreground truncate">{d.sublabel}</div>
                  )}
                </div>
              </button>
            )
          })}
        </div>
        <div className="text-xs text-muted-foreground mt-4 text-center">
          <button
            type="button"
            className="hover:text-foreground underline-offset-4 hover:underline"
            onClick={onOpenPalette}
          >
            Press <kbd className="px-1.5 py-0.5 bg-muted rounded text-foreground font-mono text-[10px]">⌘K</kbd> to search across all documents
          </button>
        </div>
      </div>
    </div>
  )
}
