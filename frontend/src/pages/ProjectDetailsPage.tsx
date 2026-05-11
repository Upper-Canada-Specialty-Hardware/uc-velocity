import { useState, useEffect, useRef, useCallback, lazy, Suspense } from "react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
// Card components available if needed
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Label } from "@/components/ui/label"
import { api } from "@/api/client"

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
} from "lucide-react"

interface ProjectDetailsPageProps {
  projectId: number
  onBack: () => void
  initialDoc?: { type: "quote" | "po"; id: number } | null
}

type DocumentType = "quote" | "po" | "invoice"
type SelectedDocument = { type: DocumentType; id: number } | null

export function ProjectDetailsPage({ projectId, onBack, initialDoc }: ProjectDetailsPageProps) {
  const [project, setProject] = useState<ProjectFull | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  // Seed once from initialDoc on mount so deep-links from search open the right doc.
  // Further navigation inside the page should not be overridden by prop changes.
  const [selectedDoc, setSelectedDoc] = useState<SelectedDocument>(() =>
    initialDoc ? { type: initialDoc.type, id: initialDoc.id } : null
  )

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
      const allInvoices: (Invoice & { quoteId: number; quoteNumber: string })[] = []
      for (const quote of quotes) {
        const quoteInvoices = await api.quotes.getInvoices(quote.id)
        allInvoices.push(...quoteInvoices.map(inv => ({ ...inv, quoteId: quote.id, quoteNumber: quote.quote_number })))
      }
      // Sort by created_at descending
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

  const handleCreateQuote = async () => {
    try {
      const quote = await api.quotes.create({ project_id: projectId })
      setQuoteDialogOpen(false)
      await fetchProject()
      setSelectedDoc({ type: "quote", id: quote.id })
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to create quote")
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
      setSelectedDoc({ type: "po", id: po.id })
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to create purchase order")
    }
  }

  const handleDeleteQuote = async (id: number, e: React.MouseEvent) => {
    e.stopPropagation()
    if (!confirm("Delete this quote?")) return
    try {
      await api.quotes.delete(id)
      if (selectedDoc?.type === "quote" && selectedDoc.id === id) {
        setSelectedDoc(null)
      }
      fetchProject()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to delete quote")
    }
  }

  const handleDeletePO = async (id: number, e: React.MouseEvent) => {
    e.stopPropagation()
    if (!confirm("Delete this purchase order?")) return
    try {
      await api.purchaseOrders.delete(id)
      if (selectedDoc?.type === "po" && selectedDoc.id === id) {
        setSelectedDoc(null)
      }
      fetchProject()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to delete PO")
    }
  }

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
              <span>Created: {new Date(project.created_on).toLocaleDateString()}</span>
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
          <Badge className="bg-green-500/10 text-green-600 border-green-500/20">
            {project.status}
          </Badge>
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Sidebar */}
        <div className="w-72 border-r bg-muted/30 flex flex-col">
          <div className="p-4 border-b">
            <h2 className="font-semibold text-sm text-muted-foreground uppercase tracking-wide">
              Documents
            </h2>
          </div>

          <div className="flex-1 flex flex-col overflow-hidden min-h-0">
            {/* Quotes Section */}
            <div className="flex-1 flex flex-col min-h-0 p-4 pb-2">
              <div className="flex items-center justify-between mb-2 flex-none">
                <h3 className="text-sm font-medium flex items-center gap-2">
                  <FileText className="h-4 w-4" />
                  Quotes
                </h3>
                <Button size="sm" variant="ghost" onClick={() => setQuoteDialogOpen(true)}>
                  <Plus className="h-4 w-4" />
                </Button>
              </div>
              <ScrollArea className="flex-1 min-h-0">
                <div className="space-y-1 pr-2">
                  {project.quotes.length === 0 ? (
                    <p className="text-xs text-muted-foreground py-2">No quotes yet</p>
                  ) : (
                    project.quotes.map((quote) => (
                      <div
                        key={quote.id}
                        className={`flex items-center justify-between p-2 rounded-md cursor-pointer group ${
                          selectedDoc?.type === "quote" && selectedDoc.id === quote.id
                            ? "bg-primary/10 text-primary"
                            : "hover:bg-muted"
                        }`}
                        onClick={() => guardedNavigate(() => setSelectedDoc({ type: "quote", id: quote.id }))}
                      >
                        <div className="flex-1 min-w-0">
                          <div className="text-sm font-medium flex items-center gap-2">
                            {quote.quote_number}
                            <Badge
                              variant="secondary"
                              className={`text-[10px] px-1.5 py-0 ${
                                quote.status === "Draft" ? "bg-gray-100 text-gray-600" :
                                quote.status === "Work Order" ? "bg-blue-100 text-blue-600" :
                                quote.status === "Invoiced" ? "bg-amber-100 text-amber-600" :
                                "bg-green-100 text-green-600"
                              }`}
                            >
                              {quote.status}
                            </Badge>
                          </div>
                          <div className="text-xs text-muted-foreground">
                            {new Date(quote.created_at).toLocaleDateString()}
                          </div>
                        </div>
                        <Button
                          size="sm"
                          variant="ghost"
                          className="opacity-0 group-hover:opacity-100 h-6 w-6 p-0 flex-shrink-0"
                          onClick={(e) => handleDeleteQuote(quote.id, e)}
                        >
                          <Trash2 className="h-3 w-3 text-destructive" />
                        </Button>
                      </div>
                    ))
                  )}
                </div>
              </ScrollArea>
            </div>

            <Separator className="flex-none" />

            {/* Purchase Orders Section */}
            <div className="flex-1 flex flex-col min-h-0 p-4 py-2">
              <div className="flex items-center justify-between mb-2 flex-none">
                <h3 className="text-sm font-medium flex items-center gap-2">
                  <ShoppingCart className="h-4 w-4" />
                  Purchase Orders
                </h3>
                <Button size="sm" variant="ghost" onClick={() => setPoDialogOpen(true)}>
                  <Plus className="h-4 w-4" />
                </Button>
              </div>
              <ScrollArea className="flex-1 min-h-0">
                <div className="space-y-1 pr-2">
                  {project.purchase_orders.length === 0 ? (
                    <p className="text-xs text-muted-foreground py-2">No purchase orders yet</p>
                  ) : (
                    project.purchase_orders.map((po) => (
                      <div
                        key={po.id}
                        className={`flex items-center justify-between p-2 rounded-md cursor-pointer group ${
                          selectedDoc?.type === "po" && selectedDoc.id === po.id
                            ? "bg-primary/10 text-primary"
                            : "hover:bg-muted"
                        }`}
                        onClick={() => guardedNavigate(() => setSelectedDoc({ type: "po", id: po.id }))}
                      >
                        <div>
                          <div className="text-sm font-medium">{po.po_number}</div>
                          <div className="text-xs text-muted-foreground">
                            {po.vendor.name}
                          </div>
                        </div>
                        <Button
                          size="sm"
                          variant="ghost"
                          className="opacity-0 group-hover:opacity-100 h-6 w-6 p-0"
                          onClick={(e) => handleDeletePO(po.id, e)}
                        >
                          <Trash2 className="h-3 w-3 text-destructive" />
                        </Button>
                      </div>
                    ))
                  )}
                </div>
              </ScrollArea>
            </div>

            <Separator className="flex-none" />

            {/* Invoices Section */}
            <div className="flex-1 flex flex-col min-h-0 p-4 pt-2">
              <div className="flex items-center justify-between mb-2 flex-none">
                <h3 className="text-sm font-medium flex items-center gap-2">
                  <Receipt className="h-4 w-4" />
                  Invoices
                </h3>
              </div>
              <ScrollArea className="flex-1 min-h-0">
                <div className="space-y-1 pr-2">
                  {invoices.length === 0 ? (
                    <p className="text-xs text-muted-foreground py-2">No invoices yet</p>
                  ) : (
                    invoices.map((invoice) => (
                      <div
                        key={invoice.id}
                        className={`flex items-center justify-between p-2 rounded-md cursor-pointer group ${
                          selectedDoc?.type === "invoice" && selectedDoc.id === invoice.id
                            ? "bg-primary/10 text-primary"
                            : "hover:bg-muted"
                        }`}
                        onClick={() => guardedNavigate(() => setSelectedDoc({ type: "invoice", id: invoice.id }))}
                      >
                        <div>
                          <div className="text-sm font-medium flex items-center gap-2">
                            Invoice #{invoice.id}
                            <Badge
                              variant={
                                invoice.status === "Paid"
                                  ? "default"
                                  : invoice.status === "Voided"
                                  ? "destructive"
                                  : "secondary"
                              }
                              className="text-xs"
                            >
                              {invoice.status}
                            </Badge>
                          </div>
                          <div className="text-xs text-muted-foreground">
                            {invoice.quoteNumber} - {new Date(invoice.created_at).toLocaleDateString()}
                          </div>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </ScrollArea>
            </div>
          </div>
        </div>

        {/* Document Editor */}
        <div className="flex-1 overflow-auto">
          {selectedDoc === null ? (
            <div className="h-full flex items-center justify-center text-muted-foreground">
              <div className="text-center">
                <FileText className="h-12 w-12 mx-auto mb-4 opacity-50" />
                <p>Select a document from the sidebar or create a new one</p>
              </div>
            </div>
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
              {vendors.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No vendors found. Please create a vendor first.
                </p>
              ) : (
                <Select value={selectedVendorId} onValueChange={setSelectedVendorId}>
                  <SelectTrigger>
                    <SelectValue placeholder="Select a vendor" />
                  </SelectTrigger>
                  <SelectContent>
                    {vendors.map((vendor) => (
                      <SelectItem key={vendor.id} value={vendor.id.toString()}>
                        {vendor.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </div>
            <Button
              onClick={handleCreatePO}
              className="w-full"
              disabled={!selectedVendorId || vendors.length === 0}
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
    </div>
  )
}
