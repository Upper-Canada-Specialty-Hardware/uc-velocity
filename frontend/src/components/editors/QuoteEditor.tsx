import { useState, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
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
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import { SearchableSelect } from "@/components/ui/searchable-select"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { SearchableSelectOption } from "@/components/ui/searchable-select"
import { api } from "@/api/client"
import type {
  Quote, QuoteLineItem, QuoteLineItemCreate, QuoteLineItemUpdate,
  LineItemType, Part, Labor, Miscellaneous, CostCode,
  StagedFulfillment, InvoiceCreate, QuoteEditorMode, StagedEdit, StagedAdd,
  StagedLineItemChange, CommitEditsRequest
} from "@/types"
import { Plus, Minus, Trash2, Wrench, Package, FileText, Pencil, ClipboardCheck, Receipt, Percent, Info, Copy, Car, MapPin, X, Lock, GitCommit, Eye, AlertTriangle, Check, CheckCircle2, Printer, Loader2, Hash, ChevronUp, ChevronDown, ArrowLeft } from "lucide-react"
import { StatusBadge } from "@/components/ui/status-badge"
import { formatDate } from "@/lib/format"
import type { CompanySettings, Project, SystemRate } from '@/types'
import { QuoteAuditTrail } from "./QuoteAuditTrail"
import { PartForm } from "@/components/forms/PartForm"
import { LaborForm } from "@/components/forms/LaborForm"
import { MiscForm } from "@/components/forms/MiscForm"
import { toast } from "@/hooks/use-toast"
import {
  getLineItemBaseCost,
  getLineItemUnitPrice as _getLineItemUnitPrice,
  getLineItemSubtotal as _getLineItemSubtotal,
  getLineItemTotal as _getLineItemTotal,
  calculateNonPmsTotal as _calculateNonPmsTotal,
  getEffectiveUnitPrice as _getEffectiveUnitPrice,
  getEffectiveLineItemTotal as _getEffectiveLineItemTotal,
  getFulfilledLineItemValue as _getFulfilledLineItemValue,
  calculateSectionTotals as _calculateSectionTotals,
  calculateQuoteTotal,
} from "@/lib/pricing"

interface QuoteEditorProps {
  quoteId: number
  onUpdate?: () => void
  onSelectQuote?: (quoteId: number) => void
}

export function QuoteEditor({ quoteId, onUpdate, onSelectQuote }: QuoteEditorProps) {
  const [quote, setQuote] = useState<Quote | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Add dialog states
  const [addDialogOpen, setAddDialogOpen] = useState(false)
  const [addDialogType, setAddDialogType] = useState<LineItemType>("part")
  // Dialog mode: "select" shows picker + details panel; "edit-inventory" shows the
  // inventory edit form so the user can update the master record without leaving the quote.
  const [addDialogMode, setAddDialogMode] = useState<"select" | "edit-inventory">("select")

  // Edit dialog states
  const [editDialogOpen, setEditDialogOpen] = useState(false)
  const [editingLineItem, setEditingLineItem] = useState<QuoteLineItem | null>(null)

  // Auto-add labor dialog
  const [autoAddLaborDialogOpen, setAutoAddLaborDialogOpen] = useState(false)
  const [pendingPart, setPendingPart] = useState<Part | null>(null)
  const [linkedLaborToAdd, setLinkedLaborToAdd] = useState<Labor[]>([])

  // Available items for selection
  const [parts, setParts] = useState<Part[]>([])
  const [laborItems, setLaborItems] = useState<Labor[]>([])
  const [miscItems, setMiscItems] = useState<Miscellaneous[]>([])

  // Add form fields
  const [selectedPartId, setSelectedPartId] = useState<string>("")
  const [selectedLaborId, setSelectedLaborId] = useState<string>("")
  const [selectedMiscId, setSelectedMiscId] = useState<string>("")
  const [quantity, setQuantity] = useState("1")

  // Edit form fields
  const [editQuantity, setEditQuantity] = useState("1")

  // Staged fulfillments (session only - not persisted until invoice created)
  const [stagedFulfillments, setStagedFulfillments] = useState<Map<number, number>>(new Map())

  // Stepper input values for each line item (separate from staged fulfillments for validation)
  const [stepperValues, setStepperValues] = useState<Map<number, string>>(new Map())
  // Validation errors for stepper inputs
  const [stepperErrors, setStepperErrors] = useState<Map<number, string>>(new Map())

  // Invoice preview modal
  const [previewModalOpen, setPreviewModalOpen] = useState(false)

  // Confirmation dialog before invoice creation
  const [confirmDialogOpen, setConfirmDialogOpen] = useState(false)

  // Creating invoice
  const [isCreatingInvoice, setIsCreatingInvoice] = useState(false)

  // Client PO Number editing
  const [clientPoNumber, setClientPoNumber] = useState("")
  const [isEditingClientPo, setIsEditingClientPo] = useState(false)
  const [savingClientPo, setSavingClientPo] = useState(false)

  // Work Description editing
  const [workDescription, setWorkDescription] = useState("")
  const [isEditingWorkDescription, setIsEditingWorkDescription] = useState(false)
  const [savingWorkDescription, setSavingWorkDescription] = useState(false)

  // Hardware Schedule Version editing
  const [hardwareScheduleVersion, setHardwareScheduleVersion] = useState("")
  const [isEditingHardwareScheduleVersion, setIsEditingHardwareScheduleVersion] = useState(false)
  const [savingHardwareScheduleVersion, setSavingHardwareScheduleVersion] = useState(false)

  // PMS (Project Management Services) dialog states
  const [pmsDialogOpen, setPmsDialogOpen] = useState(false)
  const [pmsType, setPmsType] = useState<"percent" | "dollar">("dollar")
  const [pmsValue, setPmsValue] = useState("")

  // Markup Control states (section-level)
  const [markupControlDialogOpen, setMarkupControlDialogOpen] = useState(false)
  const [pendingPartsMarkup, setPendingPartsMarkup] = useState("")
  const [pendingLaborMarkup, setPendingLaborMarkup] = useState("")
  const [pendingMiscMarkup, setPendingMiscMarkup] = useState("")
  const [togglingMarkupControl, setTogglingMarkupControl] = useState(false)

  // Edit Markup states (for modifying markup while enabled)
  const [editMarkupDialogOpen, setEditMarkupDialogOpen] = useState(false)
  const [editingPartsMarkup, setEditingPartsMarkup] = useState("")
  const [editingLaborMarkup, setEditingLaborMarkup] = useState("")
  const [editingMiscMarkup, setEditingMiscMarkup] = useState("")
  const [updatingMarkupPercent, setUpdatingMarkupPercent] = useState(false)

  // Section Markup dialog state
  const [sectionMarkupDialogOpen, setSectionMarkupDialogOpen] = useState(false)
  const [sectionMarkupSection, setSectionMarkupSection] = useState<LineItemType | null>(null)
  const [sectionMarkupValue, setSectionMarkupValue] = useState("")
  const [applyingSectionMarkup, setApplyingSectionMarkup] = useState(false)

  // Clone quote state
  const [isCloning, setIsCloning] = useState(false)

  // Cost codes
  const [costCodes, setCostCodes] = useState<CostCode[]>([])
  const [savingCostCode, setSavingCostCode] = useState(false)

  // Print PDF state
  const [isPrinting, setIsPrinting] = useState(false)
  const [printDialogOpen, setPrintDialogOpen] = useState(false)

  // Company settings (for HST rate display)
  const [companySettings, setCompanySettings] = useState<CompanySettings | null>(null)

  // Parking and Travel Distance dialog states
  const [travelDistanceDialogOpen, setTravelDistanceDialogOpen] = useState(false)
  const [travelDistanceItems, setTravelDistanceItems] = useState<SystemRate[]>([])
  const [selectedTravelDistanceId, setSelectedTravelDistanceId] = useState<string>("")
  const [addingParking, setAddingParking] = useState(false)
  const [addingTravelDistance, setAddingTravelDistance] = useState(false)

  // ===== Edit Mode State (Issue #8: Commit-based workflow) =====
  const [editorMode, setEditorMode] = useState<QuoteEditorMode>("view")
  const [clientPoMissingDialogOpen, setClientPoMissingDialogOpen] = useState(false)
  const [stagedEdits, setStagedEdits] = useState<Map<number, StagedEdit>>(new Map())
  const [stagedAdds, setStagedAdds] = useState<StagedAdd[]>([])
  const [stagedDeletes, setStagedDeletes] = useState<Set<number>>(new Set())
  const [nextTempId, setNextTempId] = useState(-1) // Negative IDs for staged adds
  const [isCommitting, setIsCommitting] = useState(false)
  const [discardConfirmOpen, setDiscardConfirmOpen] = useState(false)
  const [editPreviewOpen, setEditPreviewOpen] = useState(false)
  const [commitConfirmOpen, setCommitConfirmOpen] = useState(false)
  const [noPendingDialogOpen, setNoPendingDialogOpen] = useState(false)
  // Total Margin and other advanced totals are hidden by default to reduce surface noise.
  const [showAdvancedTotals, setShowAdvancedTotals] = useState(false)

  // Quote version tracking for Flow 7E - detect external changes during invoicing or editing
  const [initialQuoteVersion, setInitialQuoteVersion] = useState<number | null>(null)
  const [editModeStartVersion, setEditModeStartVersion] = useState<number | null>(null)
  const [quoteChangedDialogOpen, setQuoteChangedDialogOpen] = useState(false)

  // Computed: Is quote frozen (has been invoiced)?
  const hasBeenInvoiced = quote?.line_items.some(item => item.qty_fulfilled > 0) ?? false

  // Computed: Are there any staged changes?
  const hasStagedChanges = stagedEdits.size > 0 || stagedAdds.length > 0 || stagedDeletes.size > 0

  // Computed: Total count of staged changes
  const stagedChangesCount = stagedEdits.size + stagedAdds.length + stagedDeletes.size

  // Computed: Does any line item have qty_pending > 0? (Flow 1 precondition)
  const hasAnyPendingQuantity = quote?.line_items.some(item => item.qty_pending > 0) ?? false

  const fetchQuote = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await api.quotes.get(quoteId)

      // Flow 7E: Detect external changes during invoicing or edit mode
      if (editorMode === "invoicing" && initialQuoteVersion !== null && data.current_version !== initialQuoteVersion) {
        // Quote was modified externally - clear staging and warn user
        clearInvoicingState()
        setQuoteChangedDialogOpen(true)
        // Update the version to the new value so subsequent fetches don't re-trigger
        setInitialQuoteVersion(data.current_version)
      } else if (editorMode === "edit" && editModeStartVersion !== null && data.current_version !== editModeStartVersion) {
        // Quote was modified externally while editing - clear staging and warn user
        clearEditModeState()
        setQuoteChangedDialogOpen(true)
        // Update the version to the new value so subsequent fetches don't re-trigger
        setEditModeStartVersion(data.current_version)
      }

      setQuote(data)
      setClientPoNumber(data.client_po_number || "")
      setWorkDescription(data.work_description || "")
      setHardwareScheduleVersion(data.hardware_schedule_version || "")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch quote")
    } finally {
      setLoading(false)
    }
  }

  const fetchResources = async () => {
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
      console.error("Failed to fetch resources", err)
    }
  }

  useEffect(() => {
    fetchQuote()
    fetchResources()
    api.companySettings.get().then(setCompanySettings).catch(() => {})
    api.costCodes.getAll().then(setCostCodes).catch(() => {})
  }, [quoteId])

  // Computed: Are there staged invoicing changes?
  const hasStagedInvoicing = editorMode === "invoicing" && stagedFulfillments.size > 0

  // Computed: Are there any unsaved changes (invoicing OR edit mode)?
  const hasAnyUnsavedChanges = hasStagedInvoicing || (editorMode === "edit" && hasStagedChanges)

  // Navigation guard: Warn when leaving with staged quantities or edit mode changes
  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (hasAnyUnsavedChanges) {
        e.preventDefault()
        e.returnValue = "You have unsaved changes that will be lost. Are you sure you want to leave?"
        return e.returnValue
      }
    }

    window.addEventListener("beforeunload", handleBeforeUnload)

    return () => {
      window.removeEventListener("beforeunload", handleBeforeUnload)
    }
  }, [hasAnyUnsavedChanges])

  const openAddDialog = (type: LineItemType) => {
    setAddDialogType(type)
    setSelectedPartId("")
    setSelectedLaborId("")
    setSelectedMiscId("")
    setQuantity("1")
    setAddDialogMode("select")
    setAddDialogOpen(true)
  }

  // Derived selections — re-read from inventory arrays so that inline edits to the
  // master record automatically refresh the details panel and the staged-add preview
  // (option a re-snapshot for stale state).
  const selectedPart =
    addDialogType === "part" && selectedPartId
      ? parts.find((p) => p.id === parseInt(selectedPartId)) ?? null
      : null
  const selectedLabor =
    addDialogType === "labor" && selectedLaborId
      ? laborItems.find((l) => l.id === parseInt(selectedLaborId)) ?? null
      : null
  const selectedMisc =
    addDialogType === "misc" && selectedMiscId
      ? miscItems.find((m) => m.id === parseInt(selectedMiscId)) ?? null
      : null

  const handlePartUpdateSuccess = (updated?: Part) => {
    if (updated) {
      setParts((prev) => prev.map((p) => (p.id === updated.id ? updated : p)))
      toast({ description: `Part "${updated.part_number}" updated` })
    }
    setAddDialogMode("select")
  }

  const handleLaborUpdateSuccess = (updated?: Labor) => {
    if (updated) {
      setLaborItems((prev) => prev.map((l) => (l.id === updated.id ? updated : l)))
      toast({ description: `Labour "${updated.description}" updated` })
    }
    setAddDialogMode("select")
  }

  const handleMiscUpdateSuccess = (updated?: Miscellaneous) => {
    if (updated) {
      setMiscItems((prev) => prev.map((m) => (m.id === updated.id ? updated : m)))
      toast({ description: `Misc "${updated.description}" updated` })
    }
    setAddDialogMode("select")
  }

  const openEditDialog = (item: QuoteLineItem) => {
    setEditingLineItem(item)
    setEditQuantity(item.quantity.toString())
    setEditDialogOpen(true)
  }

  const handleAddLineItem = async () => {
    const qty = parseFloat(quantity) || 1

    if (addDialogType === "part") {
      if (!selectedPartId) return
      const part = parts.find((p) => p.id === parseInt(selectedPartId))
      if (!part) return
      const unitPrice = part.cost * (1 + (part.markup_percent ?? 0) / 100)

      // Check if part has linked labor items
      if (part.labor_items && part.labor_items.length > 0) {
        setPendingPart(part)
        setLinkedLaborToAdd(part.labor_items)
        setAddDialogOpen(false)
        setAutoAddLaborDialogOpen(true)
        return
      }

      // In edit mode, stage the add instead of immediate API call
      if (editorMode === "edit") {
        stageAdd({
          item_type: "part",
          part_id: part.id,
          quantity: qty,
          unit_price: unitPrice,
          base_cost: part.cost,
          markup_percent: part.markup_percent ?? 0,
          part: part, // For display
        })
        setAddDialogOpen(false)
        return
      }

      // Direct API call when not in edit mode (should not happen since Add is disabled)
      try {
        await api.quotes.addLine(quoteId, {
          item_type: "part",
          part_id: part.id,
          quantity: qty,
          unit_price: unitPrice,
        })
        setAddDialogOpen(false)
        fetchQuote()
        onUpdate?.()
      } catch (err) {
        alert(err instanceof Error ? err.message : "Failed to add line item")
      }

    } else if (addDialogType === "labor") {
      if (!selectedLaborId) return
      const labor = laborItems.find((l) => l.id === parseInt(selectedLaborId))
      if (!labor) return
      const unitPrice = labor.rate * labor.hours * (1 + labor.markup_percent / 100)

      // In edit mode, stage the add
      if (editorMode === "edit") {
        stageAdd({
          item_type: "labor",
          labor_id: labor.id,
          quantity: qty,
          unit_price: unitPrice,
          base_cost: labor.rate * labor.hours,
          markup_percent: labor.markup_percent,
          labor: labor, // For display
        })
        setAddDialogOpen(false)
        return
      }

      try {
        await api.quotes.addLine(quoteId, {
          item_type: "labor",
          labor_id: labor.id,
          quantity: qty,
          unit_price: unitPrice,
        })
        setAddDialogOpen(false)
        fetchQuote()
        onUpdate?.()
      } catch (err) {
        alert(err instanceof Error ? err.message : "Failed to add line item")
      }

    } else if (addDialogType === "misc") {
      if (!selectedMiscId) return
      const misc = miscItems.find((m) => m.id === parseInt(selectedMiscId))
      if (!misc) return
      const unitPrice = misc.unit_price * (1 + misc.markup_percent / 100)

      // In edit mode, stage the add
      if (editorMode === "edit") {
        stageAdd({
          item_type: "misc",
          misc_id: misc.id,
          quantity: qty,
          unit_price: unitPrice,
          base_cost: misc.unit_price,
          markup_percent: misc.markup_percent,
          miscellaneous: misc, // For display
        })
        setAddDialogOpen(false)
        return
      }

      try {
        await api.quotes.addLine(quoteId, {
          item_type: "misc",
          misc_id: misc.id,
          quantity: qty,
          unit_price: unitPrice,
        })
        setAddDialogOpen(false)
        fetchQuote()
        onUpdate?.()
      } catch (err) {
        alert(err instanceof Error ? err.message : "Failed to add line item")
      }
    }
  }

  const handleConfirmAutoAddLabor = async () => {
    if (!pendingPart) return
    const partQuantity = parseFloat(quantity) || 1

    // In edit mode, stage the adds
    if (editorMode === "edit") {
      // Stage the part
      stageAdd({
        item_type: "part",
        part_id: pendingPart.id,
        quantity: partQuantity,
        unit_price: pendingPart.cost * (1 + (pendingPart.markup_percent ?? 0) / 100),
        base_cost: pendingPart.cost,
        markup_percent: pendingPart.markup_percent ?? 0,
        part: pendingPart,
      })

      // Stage all linked labor items
      for (const labor of linkedLaborToAdd) {
        stageAdd({
          item_type: "labor",
          labor_id: labor.id,
          quantity: partQuantity,
          unit_price: labor.rate * labor.hours * (1 + labor.markup_percent / 100),
          base_cost: labor.rate * labor.hours,
          markup_percent: labor.markup_percent,
          labor: labor,
        })
      }

      setAutoAddLaborDialogOpen(false)
      setPendingPart(null)
      setLinkedLaborToAdd([])
      return
    }

    try {
      // First add the part
      const partLineItem: QuoteLineItemCreate = {
        item_type: "part",
        part_id: pendingPart.id,
        quantity: partQuantity,
        unit_price: pendingPart.cost * (1 + (pendingPart.markup_percent ?? 0) / 100),
      }
      await api.quotes.addLine(quoteId, partLineItem)

      // Then add all linked labor items with same quantity as the part
      for (const labor of linkedLaborToAdd) {
        const laborLineItem: QuoteLineItemCreate = {
          item_type: "labor",
          labor_id: labor.id,
          quantity: partQuantity,
          unit_price: labor.rate * labor.hours * (1 + labor.markup_percent / 100),
        }
        await api.quotes.addLine(quoteId, laborLineItem)
      }

      setAutoAddLaborDialogOpen(false)
      setPendingPart(null)
      setLinkedLaborToAdd([])
      fetchQuote()
      onUpdate?.()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to add items")
    }
  }

  const handleSkipAutoAddLabor = async () => {
    if (!pendingPart) return

    try {
      // Just add the part without labor
      const partLineItem: QuoteLineItemCreate = {
        item_type: "part",
        part_id: pendingPart.id,
        quantity: parseFloat(quantity) || 1,
        unit_price: pendingPart.cost * (1 + (pendingPart.markup_percent ?? 0) / 100),
      }
      await api.quotes.addLine(quoteId, partLineItem)

      setAutoAddLaborDialogOpen(false)
      setPendingPart(null)
      setLinkedLaborToAdd([])
      fetchQuote()
      onUpdate?.()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to add part")
    }
  }

  const handleUpdateLineItem = async () => {
    if (!editingLineItem) return

    const newQuantity = parseFloat(editQuantity) || 1

    // In edit mode, stage the edit instead of immediate API call
    if (editorMode === "edit") {
      const changes: Partial<Omit<StagedEdit, "originalItem">> = {}

      if (newQuantity !== editingLineItem.quantity) {
        changes.quantity = newQuantity
      }

      if (Object.keys(changes).length > 0) {
        stageEdit(editingLineItem, changes)
      }

      setEditDialogOpen(false)
      setEditingLineItem(null)
      return
    }

    // Direct API call when not in edit mode
    const updateData: QuoteLineItemUpdate = {
      quantity: newQuantity,
    }

    try {
      await api.quotes.updateLine(quoteId, editingLineItem.id, updateData)
      setEditDialogOpen(false)
      setEditingLineItem(null)
      fetchQuote()
      onUpdate?.()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to update line item")
    }
  }

  const handleDeleteLine = async (lineId: number) => {
    if (!confirm("Delete this line item?")) return
    try {
      await api.quotes.deleteLine(quoteId, lineId)
      fetchQuote()
      onUpdate?.()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to delete line item")
    }
  }

  // ===== Edit Mode Handlers (Issue #8: Commit-based workflow) =====

  const enterEditMode = () => {
    if (hasBeenInvoiced) return // Cannot edit frozen quotes
    if (editorMode === "invoicing") {
      alert("Please complete or cancel invoice staging before entering Edit mode.")
      return
    }
    // Store the quote's current_version for Flow 7E change detection
    setEditModeStartVersion(quote?.current_version ?? null)
    setEditorMode("edit")
  }

  const exitEditMode = (confirmDiscard = false) => {
    if (hasStagedChanges && !confirmDiscard) {
      setDiscardConfirmOpen(true)
      return
    }
    // Clear all staged changes and timestamp
    setStagedEdits(new Map())
    setStagedAdds([])
    setStagedDeletes(new Set())
    setEditModeStartVersion(null)
    setEditorMode("view")
    setDiscardConfirmOpen(false)
  }

  // ===== Invoice Staging Mode Handlers =====
  const enterInvoicingMode = () => {
    if (editorMode === "edit") {
      alert("Please commit or discard edit changes before entering Invoice mode.")
      return
    }
    // Guard: Client PO Number is required before entering invoicing mode
    if (!quote?.client_po_number) {
      setClientPoMissingDialogOpen(true)
      return
    }
    // Guard: At least one item must have qty_pending > 0 (Flow 1 precondition)
    if (!hasAnyPendingQuantity) {
      setNoPendingDialogOpen(true)
      return
    }
    // Store the quote's current_version for Flow 7E change detection
    setInitialQuoteVersion(quote.current_version)
    setEditorMode("invoicing")
  }

  const exitInvoicingMode = (confirm = false) => {
    if (confirm && stagedFulfillments.size > 0) {
      // Proceed to create invoice confirmation
      setConfirmDialogOpen(true)
    } else {
      // Cancel - clear staged fulfillments, stepper values/errors, and return to view
      clearInvoicingState()
      setInitialQuoteVersion(null)
      setEditorMode("view")
    }
  }

  // Helper to clear all invoicing staging state
  const clearInvoicingState = () => {
    setStagedFulfillments(new Map())
    setStepperValues(new Map())
    setStepperErrors(new Map())
  }

  // Helper to clear all edit mode staging state
  const clearEditModeState = () => {
    setStagedEdits(new Map())
    setStagedAdds([])
    setStagedDeletes(new Set())
    setEditModeStartVersion(null)
  }

  const stageEdit = (item: QuoteLineItem, changes: Partial<Omit<StagedEdit, "originalItem">>) => {
    const newStagedEdits = new Map(stagedEdits)
    const existing = newStagedEdits.get(item.id)

    if (existing) {
      // Merge with existing staged edit
      newStagedEdits.set(item.id, { ...existing, ...changes })
    } else {
      // Create new staged edit
      newStagedEdits.set(item.id, { originalItem: item, ...changes })
    }

    // Check if all values are back to original - if so, remove the staged edit
    const staged = newStagedEdits.get(item.id)!
    const isUnchanged =
      (staged.quantity === undefined || staged.quantity === item.quantity) &&
      (staged.unit_price === undefined || staged.unit_price === item.unit_price) &&
      (staged.description === undefined || staged.description === item.description) &&
      (staged.markup_percent === undefined || staged.markup_percent === item.markup_percent) &&
      (staged.base_cost === undefined || staged.base_cost === item.base_cost)

    if (isUnchanged) {
      newStagedEdits.delete(item.id)
    }

    setStagedEdits(newStagedEdits)
  }

  const stageAdd = (newItem: Omit<StagedAdd, "tempId">) => {
    const tempId = nextTempId
    setNextTempId(prev => prev - 1)
    setStagedAdds(prev => [...prev, { ...newItem, tempId }])
  }

  const unstageAdd = (tempId: number) => {
    setStagedAdds(prev => prev.filter(item => item.tempId !== tempId))
  }

  const updateStagedAdd = (
    tempId: number,
    changes: Partial<Pick<StagedAdd, "quantity" | "base_cost" | "markup_percent" | "description">>
  ) => {
    setStagedAdds(prev => prev.map(a => a.tempId === tempId ? { ...a, ...changes } : a))
  }

  const stageDelete = (lineItemId: number) => {
    setStagedDeletes(prev => new Set(prev).add(lineItemId))
    // Remove from staged edits if it was being edited
    if (stagedEdits.has(lineItemId)) {
      const newStagedEdits = new Map(stagedEdits)
      newStagedEdits.delete(lineItemId)
      setStagedEdits(newStagedEdits)
    }
  }

  const unstageDelete = (lineItemId: number) => {
    setStagedDeletes(prev => {
      const newSet = new Set(prev)
      newSet.delete(lineItemId)
      return newSet
    })
  }

  const handleCommitChanges = async () => {
    if (!hasStagedChanges) return

    setIsCommitting(true)
    setCommitConfirmOpen(false)

    try {
      // Pre-submit staleness check: fetch latest quote to detect external changes
      if (editModeStartVersion !== null) {
        const freshQuote = await api.quotes.get(quoteId)
        if (freshQuote.current_version !== editModeStartVersion) {
          // Quote was modified externally - clear staging, update state, and warn user
          clearEditModeState()
          setQuote(freshQuote)
          setClientPoNumber(freshQuote.client_po_number || "")
          setWorkDescription(freshQuote.work_description || "")
          setHardwareScheduleVersion(freshQuote.hardware_schedule_version || "")
          setEditModeStartVersion(freshQuote.current_version)
          setQuoteChangedDialogOpen(true)
          setIsCommitting(false)
          return
        }
      }

      const changes: StagedLineItemChange[] = []

      // Add staged adds
      for (const add of stagedAdds) {
        changes.push({
          action: "add",
          item_type: add.item_type,
          labor_id: add.labor_id,
          part_id: add.part_id,
          misc_id: add.misc_id,
          description: add.description,
          quantity: add.quantity,
          unit_price: add.unit_price,
          is_pms: add.is_pms,
          pms_percent: add.pms_percent,
        })
      }

      // Add staged edits
      for (const [lineItemId, edit] of stagedEdits) {
        changes.push({
          action: "edit",
          line_item_id: lineItemId,
          quantity: edit.quantity,
          unit_price: edit.unit_price,
          description: edit.description,
          markup_percent: edit.markup_percent,
          base_cost: edit.base_cost,
        })
      }

      // Add staged deletes
      for (const lineItemId of stagedDeletes) {
        changes.push({
          action: "delete",
          line_item_id: lineItemId,
        })
      }

      const request: CommitEditsRequest = { changes }
      await api.quotes.commitEdits(quoteId, request)

      // Clear staged changes and exit edit mode
      setStagedEdits(new Map())
      setStagedAdds([])
      setStagedDeletes(new Set())
      setEditorMode("view")

      // Refresh quote data
      fetchQuote()
      onUpdate?.()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to commit changes")
    } finally {
      setIsCommitting(false)
    }
  }

  // Helper to get display value considering staged edits
  const getDisplayQuantity = (item: QuoteLineItem): number => {
    const staged = stagedEdits.get(item.id)
    return staged?.quantity ?? item.quantity
  }

  const getDisplayUnitPrice = (item: QuoteLineItem): number | undefined => {
    const staged = stagedEdits.get(item.id)
    return staged?.unit_price ?? item.unit_price
  }

  // Check if controls should be enabled based on mode
  const canEdit = editorMode === "edit" && !hasBeenInvoiced

  const handleSaveClientPoNumber = async () => {
    setSavingClientPo(true)
    try {
      await api.quotes.update(quoteId, { client_po_number: clientPoNumber.trim() || null })
      setIsEditingClientPo(false)
      fetchQuote()
      onUpdate?.()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to update Client PO Number")
    } finally {
      setSavingClientPo(false)
    }
  }

  const handleSaveWorkDescription = async () => {
    setSavingWorkDescription(true)
    try {
      await api.quotes.update(quoteId, { work_description: workDescription.trim() || null })
      setIsEditingWorkDescription(false)
      fetchQuote()
      onUpdate?.()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to update Work Description")
    } finally {
      setSavingWorkDescription(false)
    }
  }

  const handleSaveHardwareScheduleVersion = async () => {
    setSavingHardwareScheduleVersion(true)
    try {
      await api.quotes.update(quoteId, { hardware_schedule_version: hardwareScheduleVersion.trim() || null })
      setIsEditingHardwareScheduleVersion(false)
      fetchQuote()
      onUpdate?.()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to update Hardware Schedule Version")
    } finally {
      setSavingHardwareScheduleVersion(false)
    }
  }

  const handleCostCodeChange = async (value: string) => {
    const costCodeId = parseInt(value)
    if (isNaN(costCodeId)) return
    setSavingCostCode(true)
    try {
      await api.quotes.update(quoteId, { cost_code_id: costCodeId })
      fetchQuote()
      onUpdate?.()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to update cost code")
    } finally {
      setSavingCostCode(false)
    }
  }

  // Markup Control handlers
  const handleToggleMarkupControl = () => {
    if (!quote) return

    if (hasStagedChanges) {
      alert("Please commit or discard your staged changes before modifying Markup Control.")
      return
    }

    if (!quote.markup_control_enabled) {
      // Open dialog to get section markup percents
      setPendingPartsMarkup("")
      setPendingLaborMarkup("")
      setPendingMiscMarkup("")
      setMarkupControlDialogOpen(true)
    } else {
      // Disabling - confirm and call API
      if (!confirm("Disable Markup Control? This will restore individual markups.")) {
        return
      }
      handleDisableMarkupControl()
    }
  }

  const handleDisableMarkupControl = async () => {
    setTogglingMarkupControl(true)
    try {
      await api.quotes.toggleMarkupControl(quoteId, { enabled: false })
      fetchQuote()
      onUpdate?.()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to disable markup control")
    } finally {
      setTogglingMarkupControl(false)
    }
  }

  const handleConfirmEnableMarkupControl = async () => {
    const partsP = parseFloat(pendingPartsMarkup) || 0
    const laborP = parseFloat(pendingLaborMarkup) || 0
    const miscP = parseFloat(pendingMiscMarkup) || 0

    if (partsP < 0 || laborP < 0 || miscP < 0) {
      alert("Markup percentages must be 0 or greater")
      return
    }

    setTogglingMarkupControl(true)
    try {
      await api.quotes.toggleMarkupControl(quoteId, {
        enabled: true,
        parts_markup_percent: partsP,
        labor_markup_percent: laborP,
        misc_markup_percent: miscP,
      })
      setMarkupControlDialogOpen(false)
      setPendingPartsMarkup("")
      setPendingLaborMarkup("")
      setPendingMiscMarkup("")
      fetchQuote()
      onUpdate?.()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to enable markup control")
    } finally {
      setTogglingMarkupControl(false)
    }
  }

  // Edit Markup handlers (for modifying markup while enabled)
  const handleOpenEditMarkup = () => {
    if (!quote || !quote.markup_control_enabled) return
    setEditingPartsMarkup(quote.parts_markup_percent?.toString() || "0")
    setEditingLaborMarkup(quote.labor_markup_percent?.toString() || "0")
    setEditingMiscMarkup(quote.misc_markup_percent?.toString() || "0")
    setEditMarkupDialogOpen(true)
  }

  const handleConfirmUpdateMarkup = async () => {
    if (hasStagedChanges) {
      alert("Please commit or discard your staged changes before modifying Markup Control.")
      return
    }

    const partsP = parseFloat(editingPartsMarkup) || 0
    const laborP = parseFloat(editingLaborMarkup) || 0
    const miscP = parseFloat(editingMiscMarkup) || 0

    if (partsP < 0 || laborP < 0 || miscP < 0) {
      alert("Markup percentages must be 0 or greater")
      return
    }

    setUpdatingMarkupPercent(true)
    try {
      await api.quotes.toggleMarkupControl(quoteId, {
        enabled: true,
        parts_markup_percent: partsP,
        labor_markup_percent: laborP,
        misc_markup_percent: miscP,
      })
      setEditMarkupDialogOpen(false)
      setEditingPartsMarkup("")
      setEditingLaborMarkup("")
      setEditingMiscMarkup("")
      fetchQuote()
      onUpdate?.()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to update markup")
    } finally {
      setUpdatingMarkupPercent(false)
    }
  }

  // PMS dialog handlers
  const openPmsDialog = async (type: "percent" | "dollar") => {
    setPmsType(type)
    if (type === "percent") {
      try {
        const { default_pms_percent } = await api.systemRates.getPmsDefault()
        setPmsValue(default_pms_percent != null ? String(default_pms_percent) : "")
      } catch {
        setPmsValue("")
      }
    } else {
      setPmsValue("")
    }
    setPmsDialogOpen(true)
  }

  const handleAddPmsItem = async () => {
    const value = parseFloat(pmsValue)
    if (isNaN(value) || value <= 0) {
      alert("Please enter a valid positive number")
      return
    }

    const lineItem: QuoteLineItemCreate = {
      item_type: "labor",
      description: "Project Management Services",
      quantity: 1,
      is_pms: true,
    }

    if (pmsType === "percent") {
      lineItem.pms_percent = value
      // Set initial unit_price (will be recalculated dynamically on display)
      lineItem.unit_price = calculateNonPmsTotal() * value / 100
    } else {
      lineItem.unit_price = value
    }

    try {
      await api.quotes.addLine(quoteId, lineItem)
      setPmsDialogOpen(false)
      setPmsValue("")
      fetchQuote()
      onUpdate?.()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to add PMS item")
    }
  }

  const getLineItemDescription = (item: QuoteLineItem): string => {
    if (item.item_type === "labor") {
      if (item.labor) {
        return item.labor.description
      }
      // PMS or custom labor item without inventory reference
      return item.description || ""
    }
    if (item.item_type === "part" && item.part) {
      return item.part.part_number
    }
    if (item.item_type === "misc" && item.miscellaneous) {
      return item.miscellaneous.description
    }
    return item.description || ""
  }

  const getDescriptionLabel = (item: QuoteLineItem): string => {
    if (item.item_type === "labor") return "Labour Description"
    if (item.item_type === "part") return "Part Number"
    return "Misc Description"
  }

  // Pricing functions — delegate to shared @/lib/pricing module
  const getLineItemUnitPrice = (item: QuoteLineItem): number => _getLineItemUnitPrice(item)
  const getLineItemSubtotal = (item: QuoteLineItem): number => _getLineItemSubtotal(item)
  const getLineItemTotal = (item: QuoteLineItem): number => _getLineItemTotal(item)

  const calculateNonPmsTotal = (): number => {
    if (!quote) return 0
    return _calculateNonPmsTotal(quote.line_items)
  }

  const getEffectiveUnitPrice = (item: QuoteLineItem): number =>
    _getEffectiveUnitPrice(item, calculateNonPmsTotal())

  const getEffectiveLineItemTotal = (item: QuoteLineItem): number =>
    _getEffectiveLineItemTotal(item, calculateNonPmsTotal())

  const calculateTotal = (): number => {
    if (!quote) return 0
    return calculateQuoteTotal(quote.line_items)
  }

  // Calculate weighted average markup percentage
  // Formula: Σ(Markup% × Unit Cost × Qty) / Σ(Unit Cost × Qty)
  const calculateAverageMarkup = (): number => {
    if (!quote || quote.line_items.length === 0) return 0

    let totalWeightedMarkup = 0
    let totalBaseCost = 0

    for (const item of quote.line_items) {
      const baseCost = getLineItemBaseCost(item)
      let markupPercent = item.markup_percent ?? 0

      // PMS items have 0 markup by definition
      if (item.is_pms) {
        markupPercent = 0
      }

      const weightedCost = baseCost * item.quantity
      totalWeightedMarkup += markupPercent * weightedCost
      totalBaseCost += weightedCost
    }

    return totalBaseCost > 0 ? totalWeightedMarkup / totalBaseCost : 0
  }

  // Calculate total margin percentage
  // Margin = (Selling Price - Manufacturing Cost) / Selling Price × 100
  // Manufacturing cost = Parts cost only (Labor/Misc have 0 mfg cost)
  const calculateTotalMargin = (): number => {
    if (!quote) return 0

    const totalSellingPrice = calculateTotal()
    if (totalSellingPrice === 0) return 0

    let totalManufacturingCost = 0

    for (const item of quote.line_items) {
      // Only Parts have manufacturing cost
      if (item.part) {
        totalManufacturingCost += item.part.cost * item.quantity
      }
      // Labor and Misc have 0 manufacturing cost
      // PMS items also have 0 manufacturing cost
    }

    return ((totalSellingPrice - totalManufacturingCost) / totalSellingPrice) * 100
  }

  // Calculate total labor hours (excluding PMS items)
  const calculateTotalLaborHours = (): number => {
    if (!quote) return 0
    return quote.line_items
      .filter(item => item.item_type === "labor" && !item.is_pms && item.labor)
      .reduce((sum, item) => {
        const laborHours = item.labor?.hours || 0
        return sum + (laborHours * item.quantity)
      }, 0)
  }

  // ===== Projected Calculations (for Edit Mode comparison) =====
  // These include staged changes: adds, edits, deletes

  const calculateProjectedTotal = (): number => {
    if (!quote) return 0

    // Start with existing items, excluding deleted ones
    let projectedItems: { unitPrice: number; quantity: number; isPms: boolean; pmsPercent?: number }[] = []

    for (const item of quote.line_items) {
      if (stagedDeletes.has(item.id)) continue // Skip deleted items

      const editedItem = stagedEdits.get(item.id)
      const quantity = editedItem?.quantity ?? item.quantity
      // Calculate unit price from unit cost + markup, accounting for staged changes
      const baseCost = editedItem?.base_cost ?? getLineItemBaseCost(item)
      const markup = editedItem?.markup_percent ?? item.markup_percent ?? 0
      const unitPrice = item.is_pms
        ? getLineItemUnitPrice(item)
        : baseCost * (1 + markup / 100)

      projectedItems.push({
        unitPrice,
        quantity,
        isPms: item.is_pms,
        pmsPercent: item.pms_percent ?? undefined,
      })
    }

    // Add staged adds
    for (const add of stagedAdds) {
      const addBaseCost = add.base_cost ?? (
        add.part ? add.part.cost :
        add.labor ? add.labor.hours * add.labor.rate :
        add.miscellaneous ? add.miscellaneous.unit_price : 0
      )
      const addMarkup = add.markup_percent ?? (
        add.part ? (add.part.markup_percent ?? 0) :
        add.labor ? add.labor.markup_percent :
        add.miscellaneous ? add.miscellaneous.markup_percent : 0
      )
      const unitPrice = addBaseCost * (1 + (addMarkup ?? 0) / 100)
      projectedItems.push({
        unitPrice,
        quantity: add.quantity,
        isPms: add.is_pms ?? false,
        pmsPercent: add.pms_percent ?? undefined,
      })
    }

    // Calculate non-PMS total first
    const nonPmsTotal = projectedItems
      .filter(item => !item.isPms)
      .reduce((sum, item) => {
        return sum + item.unitPrice * item.quantity
      }, 0)

    // Calculate PMS total
    const pmsTotal = projectedItems
      .filter(item => item.isPms)
      .reduce((sum, item) => {
        if (item.pmsPercent != null) {
          const unitPrice = nonPmsTotal * item.pmsPercent / 100
          return sum + unitPrice * item.quantity
        }
        return sum + item.unitPrice * item.quantity
      }, 0)

    return nonPmsTotal + pmsTotal
  }

  const calculateProjectedMarkup = (): number => {
    if (!quote) return 0

    let totalWeightedMarkup = 0
    let totalBaseCost = 0

    // Existing items (excluding deleted)
    for (const item of quote.line_items) {
      if (stagedDeletes.has(item.id)) continue

      const editedItem = stagedEdits.get(item.id)
      const quantity = editedItem?.quantity ?? item.quantity
      const baseCost = editedItem?.base_cost ?? getLineItemBaseCost(item)
      let markupPercent = editedItem?.markup_percent ?? item.markup_percent ?? 0

      if (item.is_pms) markupPercent = 0

      const weightedCost = baseCost * quantity
      totalWeightedMarkup += markupPercent * weightedCost
      totalBaseCost += weightedCost
    }

    // Staged adds
    for (const add of stagedAdds) {
      const baseCost = add.base_cost ?? (
        add.part ? add.part.cost :
        add.labor ? add.labor.hours * add.labor.rate :
        add.miscellaneous ? add.miscellaneous.unit_price : 0
      )
      let markupPercent = add.markup_percent ?? (
        add.part ? (add.part.markup_percent ?? 0) :
        add.labor ? add.labor.markup_percent :
        add.miscellaneous ? add.miscellaneous.markup_percent : 0
      ) ?? 0

      if (add.is_pms) markupPercent = 0

      const weightedCost = baseCost * add.quantity
      totalWeightedMarkup += markupPercent * weightedCost
      totalBaseCost += weightedCost
    }

    return totalBaseCost > 0 ? totalWeightedMarkup / totalBaseCost : 0
  }

  const calculateProjectedMargin = (): number => {
    if (!quote) return 0

    const projectedSellingPrice = calculateProjectedTotal()
    if (projectedSellingPrice === 0) return 0

    let totalManufacturingCost = 0

    // Existing items (excluding deleted)
    for (const item of quote.line_items) {
      if (stagedDeletes.has(item.id)) continue

      const editedItem = stagedEdits.get(item.id)
      const quantity = editedItem?.quantity ?? item.quantity

      if (item.part) {
        totalManufacturingCost += item.part.cost * quantity
      }
    }

    // Staged adds
    for (const add of stagedAdds) {
      if (add.part) {
        totalManufacturingCost += add.part.cost * add.quantity
      }
    }

    return ((projectedSellingPrice - totalManufacturingCost) / projectedSellingPrice) * 100
  }

  // Calculate section totals for display
  const calculateSectionTotals = (items: QuoteLineItem[], useEffectiveTotal = false) =>
    _calculateSectionTotals(items, calculateNonPmsTotal(), useEffectiveTotal)

  // Calculate staged total for a single line item
  const getStagedLineItemTotal = (item: QuoteLineItem): number => {
    const stagedQty = stagedFulfillments.get(item.id) || 0
    if (stagedQty === 0) return 0
    const unitPrice = getEffectiveUnitPrice(item)
    return unitPrice * stagedQty
  }

  const getFulfilledLineItemValue = (item: QuoteLineItem): number =>
    _getFulfilledLineItemValue(item, calculateNonPmsTotal())

  // Calculate staged totals for a section
  const calculateStagedSectionTotals = (items: QuoteLineItem[]) => {
    return {
      stagedQty: items.reduce((sum, item) => sum + (stagedFulfillments.get(item.id) || 0), 0),
      stagedTotal: items.reduce((sum, item) => sum + getStagedLineItemTotal(item), 0),
      itemCount: items.filter(item => stagedFulfillments.has(item.id)).length
    }
  }

  // Calculate quote-wide staged totals
  const calculateStagedGrandTotal = () => {
    if (!quote) return { stagedQty: 0, stagedTotal: 0, itemCount: 0 }
    return calculateStagedSectionTotals(quote.line_items)
  }

  // Calculate already invoiced totals
  const calculateInvoicedTotals = () => {
    if (!quote) return { invoicedQty: 0, invoicedTotal: 0 }
    return {
      invoicedQty: quote.line_items.reduce((sum, item) => sum + item.qty_fulfilled, 0),
      invoicedTotal: quote.line_items.reduce((sum, item) => {
        const unitPrice = getEffectiveUnitPrice(item)
        return sum + unitPrice * item.qty_fulfilled
      }, 0)
    }
  }

  // Round up to nearest 8
  const roundUpToNearest8 = (value: number): number => {
    return Math.ceil(value / 8) * 8
  }

  // Fetch travel distance items for dialog
  const fetchTravelDistanceItems = async () => {
    try {
      const items = await api.systemRates.getTravelDistance()
      setTravelDistanceItems(items)
    } catch (err) {
      console.error("Failed to fetch travel distance items", err)
    }
  }

  // Handler for "Calculate & Add Parking" button
  const handleCalculateAndAddParking = async () => {
    if (!quote) return

    setAddingParking(true)
    try {
      // Get the parking system rate
      const parkingRate = await api.systemRates.getParking()
      const miscId = parkingRate.linked_misc_id
      if (!miscId) {
        alert("Parking rate has no linked miscellaneous item. Please check Settings.")
        return
      }

      // Calculate total labor hours (excluding PMS)
      const totalLaborHours = calculateTotalLaborHours()

      // Round up to nearest 8
      const parkingQty = roundUpToNearest8(totalLaborHours)

      if (parkingQty === 0) {
        alert("No labor hours to calculate parking from. Add labor items first.")
        return
      }

      const parkingPrice = parkingRate.unit_price * (1 + parkingRate.markup_percent / 100)

      // Check if parking line item already exists in quote
      const existingParkingLine = quote.line_items.find(
        item => item.item_type === "misc" &&
                item.misc_id === miscId
      )

      // In edit mode, stage the changes instead of making direct API calls
      if (editorMode === "edit") {
        // Also check staged adds for already-staged parking
        const stagedParkingAdd = stagedAdds.find(
          add => add.item_type === "misc" && add.misc_id === miscId
        )

        if (existingParkingLine) {
          // Stage an edit to the existing line item's quantity
          stageEdit(existingParkingLine, { quantity: parkingQty })
        } else if (stagedParkingAdd) {
          // Update the already-staged parking add's quantity
          setStagedAdds(prev => prev.map(add =>
            add.tempId === stagedParkingAdd.tempId
              ? { ...add, quantity: parkingQty }
              : add
          ))
        } else {
          // Stage a new add — use the linked misc record for FK compatibility
          const miscRecord = await api.misc.get(miscId)
          stageAdd({
            item_type: "misc",
            misc_id: miscId,
            quantity: parkingQty,
            unit_price: parkingPrice,
            base_cost: parkingRate.unit_price,
            markup_percent: parkingRate.markup_percent,
            miscellaneous: miscRecord, // For display
          })
        }
        return
      }

      // Non-edit mode: direct API calls (original behavior)
      if (existingParkingLine) {
        // Update existing line item quantity
        await api.quotes.updateLine(quoteId, existingParkingLine.id, {
          quantity: parkingQty
        })
      } else {
        // Add new line item
        const lineItem: QuoteLineItemCreate = {
          item_type: "misc",
          misc_id: miscId,
          quantity: parkingQty,
          unit_price: parkingPrice
        }
        await api.quotes.addLine(quoteId, lineItem)
      }

      fetchQuote()
      onUpdate?.()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to add parking")
    } finally {
      setAddingParking(false)
    }
  }

  // Handler for "Calculate & Add Travel Distance" button - opens dialog
  const handleOpenTravelDistanceDialog = async () => {
    await fetchTravelDistanceItems()
    setSelectedTravelDistanceId("")
    setTravelDistanceDialogOpen(true)
  }

  // Handler for confirming travel distance selection
  const handleConfirmTravelDistance = async () => {
    if (!quote || !selectedTravelDistanceId) return

    setAddingTravelDistance(true)
    try {
      const selectedRate = travelDistanceItems.find(
        item => item.id.toString() === selectedTravelDistanceId
      )

      if (!selectedRate) {
        alert("Please select a travel distance option")
        return
      }

      const miscId = selectedRate.linked_misc_id
      if (!miscId) {
        alert("Selected travel tier has no linked miscellaneous item. Please check Settings.")
        return
      }

      // Calculate days = total labor hours / 8, rounded up
      const totalLaborHours = calculateTotalLaborHours()
      const days = Math.ceil(totalLaborHours / 8)

      if (days === 0) {
        alert("No labor hours to calculate travel distance from. Add labor items first.")
        return
      }

      const travelPrice = selectedRate.unit_price * (1 + selectedRate.markup_percent / 100)

      // Check if this travel distance item already exists in quote
      const existingLine = quote.line_items.find(
        item => item.item_type === "misc" &&
                item.misc_id === miscId
      )

      // In edit mode, stage the changes instead of making direct API calls
      if (editorMode === "edit") {
        // Also check staged adds for already-staged travel item
        const stagedTravelAdd = stagedAdds.find(
          add => add.item_type === "misc" && add.misc_id === miscId
        )

        if (existingLine) {
          // Stage an edit to the existing line item's quantity
          stageEdit(existingLine, { quantity: days })
        } else if (stagedTravelAdd) {
          // Update the already-staged travel add's quantity
          setStagedAdds(prev => prev.map(add =>
            add.tempId === stagedTravelAdd.tempId
              ? { ...add, quantity: days }
              : add
          ))
        } else {
          // Stage a new add — use the linked misc record for FK compatibility
          const miscRecord = await api.misc.get(miscId)
          stageAdd({
            item_type: "misc",
            misc_id: miscId,
            quantity: days,
            unit_price: travelPrice,
            base_cost: selectedRate.unit_price,
            markup_percent: selectedRate.markup_percent,
            miscellaneous: miscRecord, // For display
          })
        }
        setTravelDistanceDialogOpen(false)
        return
      }

      // Non-edit mode: direct API calls (original behavior)
      if (existingLine) {
        // Update existing line item quantity
        await api.quotes.updateLine(quoteId, existingLine.id, {
          quantity: days
        })
      } else {
        // Add new line item
        const lineItem: QuoteLineItemCreate = {
          item_type: "misc",
          misc_id: miscId,
          quantity: days,
          unit_price: travelPrice
        }
        await api.quotes.addLine(quoteId, lineItem)
      }

      setTravelDistanceDialogOpen(false)
      fetchQuote()
      onUpdate?.()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to add travel distance")
    } finally {
      setAddingTravelDistance(false)
    }
  }

  const getTypeIcon = (type: LineItemType) => {
    switch (type) {
      case "labor":
        return <Wrench className="h-4 w-4" />
      case "part":
        return <Package className="h-4 w-4" />
      case "misc":
        return <FileText className="h-4 w-4" />
    }
  }

  const getTypeBadgeVariant = (type: LineItemType) => {
    switch (type) {
      case "labor":
        return "default"
      case "part":
        return "secondary"
      case "misc":
        return "outline"
    }
  }

  // Filter line items by type
  const partItems = quote?.line_items.filter(item => item.item_type === "part") || []
  const laborItems2 = quote?.line_items.filter(item => item.item_type === "labor") || []
  const miscItems2 = quote?.line_items.filter(item => item.item_type === "misc") || []

  // Stacked progress bar component for fulfillment visualization
  const StackedProgress = ({ items }: { items: QuoteLineItem[] }) => {
    const totals = calculateSectionTotals(items)
    const stagedTotals = calculateStagedSectionTotals(items)

    if (totals.qtyOrdered === 0) return null

    const fulfilledPercent = (totals.qtyFulfilled / totals.qtyOrdered) * 100
    const stagedPercent = (stagedTotals.stagedQty / totals.qtyOrdered) * 100

    return (
      <div className="h-2 w-32 bg-muted rounded-full overflow-hidden flex" title={`Fulfilled: ${totals.qtyFulfilled}, Staged: ${stagedTotals.stagedQty}, Remaining: ${totals.qtyPending - stagedTotals.stagedQty}`}>
        {/* Fulfilled portion - solid green */}
        <div
          className="h-full bg-green-600 dark:bg-green-500"
          style={{ width: `${fulfilledPercent}%` }}
        />
        {/* Staged portion - lighter green */}
        <div
          className="h-full bg-green-300 dark:bg-green-700"
          style={{ width: `${stagedPercent}%` }}
        />
      </div>
    )
  }

  const clearStagedFulfillment = (itemId: number) => {
    const newStaged = new Map(stagedFulfillments)
    newStaged.delete(itemId)
    setStagedFulfillments(newStaged)
    // Also clear stepper value and error
    const newStepperValues = new Map(stepperValues)
    newStepperValues.delete(itemId)
    setStepperValues(newStepperValues)
    const newStepperErrors = new Map(stepperErrors)
    newStepperErrors.delete(itemId)
    setStepperErrors(newStepperErrors)
  }

  // ===== Stepper Control Handlers =====

  // Get the stepper input value for display (prioritize stepperValues, then staged, then empty)
  const getStepperDisplayValue = (itemId: number): string => {
    // If there's a stepper value being edited, show that
    if (stepperValues.has(itemId)) {
      return stepperValues.get(itemId) || ""
    }
    // Otherwise show staged value if exists
    const staged = stagedFulfillments.get(itemId)
    return staged ? staged.toString() : ""
  }

  // Validate and apply stepper input
  const validateAndApplyStepperValue = (item: QuoteLineItem, value: string) => {
    const newErrors = new Map(stepperErrors)
    const newStaged = new Map(stagedFulfillments)
    const newStepperValues = new Map(stepperValues)

    // Clear any existing error first
    newErrors.delete(item.id)

    // Handle empty or zero - clear staging
    if (value === "" || value === "0") {
      newStaged.delete(item.id)
      newStepperValues.delete(item.id)
      setStagedFulfillments(newStaged)
      setStepperValues(newStepperValues)
      setStepperErrors(newErrors)
      return
    }

    const parsed = parseFloat(value)

    // Check for non-numeric or non-positive values
    if (isNaN(parsed) || parsed <= 0) {
      newErrors.set(item.id, "Must be a positive whole number")
      newStepperValues.set(item.id, value) // Keep the invalid value for display
      setStepperValues(newStepperValues)
      setStepperErrors(newErrors)
      return
    }

    // Check for decimal values (not integers)
    if (!Number.isInteger(parsed)) {
      newErrors.set(item.id, "Must be a positive whole number")
      newStepperValues.set(item.id, value) // Keep the invalid value for display
      setStepperValues(newStepperValues)
      setStepperErrors(newErrors)
      return
    }

    // Check if exceeds qty pending
    if (parsed > item.qty_pending) {
      newErrors.set(item.id, `Cannot exceed Qty Pending (${item.qty_pending})`)
      newStepperValues.set(item.id, value) // Keep the invalid value for display
      setStepperValues(newStepperValues)
      setStepperErrors(newErrors)
      return
    }

    // Valid value - apply staging
    newStaged.set(item.id, parsed)
    newStepperValues.delete(item.id) // Clear temp value since we're using staged
    setStagedFulfillments(newStaged)
    setStepperValues(newStepperValues)
    setStepperErrors(newErrors)
  }

  // Handle stepper input change (just update display value, validate on blur)
  const handleStepperInputChange = (item: QuoteLineItem, value: string) => {
    const newStepperValues = new Map(stepperValues)
    newStepperValues.set(item.id, value)
    setStepperValues(newStepperValues)
  }

  // Handle stepper input blur - validate and apply
  const handleStepperInputBlur = (item: QuoteLineItem) => {
    const value = stepperValues.get(item.id)
    if (value !== undefined) {
      validateAndApplyStepperValue(item, value)
    }
  }

  // Handle stepper input key down
  const handleStepperKeyDown = (e: React.KeyboardEvent, item: QuoteLineItem) => {
    if (e.key === "Enter") {
      e.preventDefault()
      const value = stepperValues.get(item.id) ?? getStepperDisplayValue(item.id)
      validateAndApplyStepperValue(item, value)
      ;(e.target as HTMLInputElement).blur()
    } else if (e.key === "Escape") {
      e.preventDefault()
      // Clear temp value and error, revert to staged value
      const newStepperValues = new Map(stepperValues)
      newStepperValues.delete(item.id)
      setStepperValues(newStepperValues)
      const newStepperErrors = new Map(stepperErrors)
      newStepperErrors.delete(item.id)
      setStepperErrors(newStepperErrors)
    }
  }

  // Increment stepper value
  const handleStepperIncrement = (item: QuoteLineItem) => {
    // Derive working value from in-progress stepperValues first, then fall back to stagedFulfillments
    let currentValue = 0
    if (stepperValues.has(item.id)) {
      const typedValue = stepperValues.get(item.id) || ""
      const parsed = parseFloat(typedValue)
      // Use parsed value if valid positive number, capped between 0 and qty_pending
      if (!isNaN(parsed) && parsed >= 0) {
        currentValue = Math.min(Math.max(parsed, 0), item.qty_pending)
      }
    } else {
      currentValue = stagedFulfillments.get(item.id) || 0
    }

    const newValue = Math.min(currentValue + 1, item.qty_pending)
    if (newValue > 0) {
      const newStaged = new Map(stagedFulfillments)
      newStaged.set(item.id, newValue)
      setStagedFulfillments(newStaged)
      // Clear any error
      const newErrors = new Map(stepperErrors)
      newErrors.delete(item.id)
      setStepperErrors(newErrors)
      // Clear temp value
      const newStepperValues = new Map(stepperValues)
      newStepperValues.delete(item.id)
      setStepperValues(newStepperValues)
    }
  }

  // Decrement stepper value
  const handleStepperDecrement = (item: QuoteLineItem) => {
    // Derive working value from in-progress stepperValues first, then fall back to stagedFulfillments
    let currentValue = 0
    if (stepperValues.has(item.id)) {
      const typedValue = stepperValues.get(item.id) || ""
      const parsed = parseFloat(typedValue)
      // Use parsed value if valid positive number, capped between 0 and qty_pending
      if (!isNaN(parsed) && parsed >= 0) {
        currentValue = Math.min(Math.max(parsed, 0), item.qty_pending)
      }
    } else {
      currentValue = stagedFulfillments.get(item.id) || 0
    }

    const newValue = currentValue - 1
    const newStaged = new Map(stagedFulfillments)
    if (newValue <= 0) {
      newStaged.delete(item.id)
    } else {
      newStaged.set(item.id, newValue)
    }
    setStagedFulfillments(newStaged)
    // Clear any error
    const newErrors = new Map(stepperErrors)
    newErrors.delete(item.id)
    setStepperErrors(newErrors)
    // Clear temp value
    const newStepperValues = new Map(stepperValues)
    newStepperValues.delete(item.id)
    setStepperValues(newStepperValues)
  }

  // Quick fulfill - set to max pending quantity
  const handleQuickFulfill = (item: QuoteLineItem) => {
    if (item.qty_pending <= 0) return
    const newStaged = new Map(stagedFulfillments)
    newStaged.set(item.id, Math.round(item.qty_pending))
    setStagedFulfillments(newStaged)
    // Clear any error
    const newErrors = new Map(stepperErrors)
    newErrors.delete(item.id)
    setStepperErrors(newErrors)
    // Clear temp value
    const newStepperValues = new Map(stepperValues)
    newStepperValues.delete(item.id)
    setStepperValues(newStepperValues)
  }

  // Get total staged items count
  const stagedCount = stagedFulfillments.size

  // Create invoice from staged fulfillments
  const handleCreateInvoice = async () => {
    if (stagedFulfillments.size === 0) return

    setIsCreatingInvoice(true)
    setConfirmDialogOpen(false) // Close confirmation dialog
    try {
      // Pre-submit staleness check: fetch latest quote to detect external changes
      if (initialQuoteVersion !== null) {
        const freshQuote = await api.quotes.get(quoteId)
        if (freshQuote.current_version !== initialQuoteVersion) {
          // Quote was modified externally - clear staging, update state, and warn user
          clearInvoicingState()
          setQuote(freshQuote)
          setClientPoNumber(freshQuote.client_po_number || "")
          setWorkDescription(freshQuote.work_description || "")
          setHardwareScheduleVersion(freshQuote.hardware_schedule_version || "")
          setInitialQuoteVersion(freshQuote.current_version)
          setQuoteChangedDialogOpen(true)
          setIsCreatingInvoice(false)
          return
        }
      }

      const fulfillments = Array.from(stagedFulfillments.entries()).map(([lineItemId, qty]) => ({
        line_item_id: lineItemId,
        quantity: qty
      }))

      const invoiceData: InvoiceCreate = {
        fulfillments,
        notes: undefined
      }

      await api.quotes.createInvoice(quoteId, invoiceData)

      // Show success toast
      toast({
        title: "Invoice created successfully",
        description: "The invoice has been created and line items have been fulfilled.",
      })

      // Clear staged fulfillments, stepper values/errors, exit invoicing mode, and refresh
      setStagedFulfillments(new Map())
      setStepperValues(new Map())
      setStepperErrors(new Map())
      setInitialQuoteVersion(null)
      setEditorMode("view")
      fetchQuote()
      onUpdate?.()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to create invoice")
    } finally {
      setIsCreatingInvoice(false)
    }
  }

  // Fulfill All handler - stages all items in section for fulfillment
  const handleFulfillAll = (itemType: LineItemType) => {
    if (!quote) return

    const itemsToFulfill = quote.line_items.filter(
      item => item.item_type === itemType && item.qty_pending > 0
    )

    if (itemsToFulfill.length === 0) return

    const newStagedFulfillments = new Map(stagedFulfillments)
    const newStepperValues = new Map(stepperValues)
    const newStepperErrors = new Map(stepperErrors)

    itemsToFulfill.forEach(item => {
      newStagedFulfillments.set(item.id, Math.round(item.qty_pending))
      // Clear stepper temp values and errors
      newStepperValues.delete(item.id)
      newStepperErrors.delete(item.id)
    })

    setStagedFulfillments(newStagedFulfillments)
    setStepperValues(newStepperValues)
    setStepperErrors(newStepperErrors)
  }

  // Clear All Staged handler - clears all staged items in section
  const handleClearAllStaged = (itemType: LineItemType) => {
    if (!quote) return

    const newStaged = new Map(stagedFulfillments)
    const newStepperValues = new Map(stepperValues)
    const newStepperErrors = new Map(stepperErrors)

    quote.line_items
      .filter(item => item.item_type === itemType)
      .forEach(item => {
        newStaged.delete(item.id)
        newStepperValues.delete(item.id)
        newStepperErrors.delete(item.id)
      })

    setStagedFulfillments(newStaged)
    setStepperValues(newStepperValues)
    setStepperErrors(newStepperErrors)
  }

  // Get button state for a section (fulfill, clear, or disabled)
  const getSectionButtonState = (items: QuoteLineItem[]): 'fulfill' | 'clear' | 'disabled' => {
    const pendingItems = items.filter(item => item.qty_pending > 0)
    if (pendingItems.length === 0) return 'disabled' // Nothing to stage

    const allFullyStaged = pendingItems.every(item => {
      const staged = stagedFulfillments.get(item.id)
      return staged === item.qty_pending
    })

    return allFullyStaged ? 'clear' : 'fulfill'
  }

  // Section Markup handlers
  const handleOpenSectionMarkup = (itemType: LineItemType) => {
    setSectionMarkupSection(itemType)
    // Pre-fill with current section markup value
    if (quote) {
      const current = itemType === "part" ? quote.parts_markup_percent
        : itemType === "labor" ? quote.labor_markup_percent
        : quote.misc_markup_percent
      setSectionMarkupValue(current != null ? current.toString() : "")
    }
    setSectionMarkupDialogOpen(true)
  }

  const handleApplySectionMarkup = async () => {
    if (!quote || !sectionMarkupSection) return

    if (hasStagedChanges) {
      alert("Please commit or discard your staged changes before setting section markup.")
      return
    }
    const markupValue = parseFloat(sectionMarkupValue) || 0
    if (markupValue < 0) {
      alert("Markup must be 0 or greater")
      return
    }

    setApplyingSectionMarkup(true)
    try {
      await api.quotes.toggleMarkupControl(quoteId, {
        enabled: true,
        parts_markup_percent: sectionMarkupSection === "part" ? markupValue : (quote.parts_markup_percent ?? 0),
        labor_markup_percent: sectionMarkupSection === "labor" ? markupValue : (quote.labor_markup_percent ?? 0),
        misc_markup_percent: sectionMarkupSection === "misc" ? markupValue : (quote.misc_markup_percent ?? 0),
      })
      setSectionMarkupDialogOpen(false)
      fetchQuote()
      onUpdate?.()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to apply section markup")
    } finally {
      setApplyingSectionMarkup(false)
    }
  }

  // Clone quote handler
  const handleCloneQuote = async () => {
    if (!quote) return

    setIsCloning(true)
    try {
      const clonedQuote = await api.quotes.clone(quote.id)
      onUpdate?.()
      if (onSelectQuote) {
        onSelectQuote(clonedQuote.id)
      } else {
        alert(`Quote cloned successfully! New quote: ${clonedQuote.quote_number}`)
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to clone quote")
    } finally {
      setIsCloning(false)
    }
  }

  const runPrintQuote = async (includeBreakdown: boolean) => {
    if (!quote) return
    setPrintDialogOpen(false)
    setIsPrinting(true)
    try {
      const [project, companySettings, { pdf }, { QuotePDF }] = await Promise.all([
        api.projects.get(quote.project_id) as Promise<Project>,
        api.companySettings.get(),
        import('@react-pdf/renderer'),
        import('@/components/pdf/QuotePDF'),
      ])
      const blob = await pdf(
        <QuotePDF
          quote={quote}
          project={project}
          companySettings={companySettings}
          includeBreakdown={includeBreakdown}
        />
      ).toBlob()
      const url = URL.createObjectURL(blob)
      window.open(url, '_blank')
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to generate PDF")
    } finally {
      setIsPrinting(false)
    }
  }

  if (loading) {
    return <div className="p-8 text-center text-muted-foreground">Loading...</div>
  }

  if (error || !quote) {
    return (
      <div className="p-8">
        <div className="p-4 bg-destructive/10 border border-destructive/20 rounded-md text-destructive">
          {error || "Quote not found"}
        </div>
      </div>
    )
  }

  // Render a section with table for a specific item type
  const renderLineItemSection = (
    title: string,
    items: QuoteLineItem[],
    type: LineItemType,
    icon: React.ReactNode,
    addButtonLabel: string,
    extraButtons?: React.ReactNode,
    useEffectivePricing?: boolean  // For Labour section PMS % support
  ) => {
    // Section-level markup default — pulled from the quote's section setting,
    // falling back to the most common per-row markup so we always have a sensible
    // value to compare overrides against.
    const sectionDefault: number = (() => {
      const fromQuote =
        type === "part" ? quote?.parts_markup_percent :
        type === "labor" ? quote?.labor_markup_percent :
        quote?.misc_markup_percent
      if (fromQuote != null) return fromQuote
      const nonPms = items.filter((i) => !i.is_pms)
      if (nonPms.length === 0) return 0
      const counts = new Map<number, number>()
      for (const it of nonPms) {
        const m = it.markup_percent ?? 0
        counts.set(m, (counts.get(m) ?? 0) + 1)
      }
      let mode = 0, best = -1
      for (const [m, c] of counts) {
        if (c > best) { best = c; mode = m }
      }
      return mode
    })()
    const hasMarkupOverrides = !quote?.markup_control_enabled && items.some(
      (i) => !i.is_pms && (i.markup_percent ?? 0) !== sectionDefault
    )
    // Show the per-row Markup % column whenever the user might need to act on it:
    // always in edit mode, otherwise only when at least one row diverges from the
    // section default. Hidden entirely when the global markup toggle is on.
    const showMarkupCol = !quote?.markup_control_enabled && (editorMode === "edit" || hasMarkupOverrides)
    return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex justify-between items-center">
          <div className="flex items-center gap-4">
            <CardTitle className="text-base flex items-center gap-2">
              {icon}
              {title}
            </CardTitle>
            {items.length > 0 && <StackedProgress items={items} />}
            {items.length > 0 && (
              <Badge variant="outline" className="text-xs font-normal gap-1">
                <Percent className="h-3 w-3" />
                Default markup: {sectionDefault}%
              </Badge>
            )}
          </div>
          <div className="flex gap-2">
            {/* Invoicing buttons - only visible in Invoicing mode */}
            {editorMode === "invoicing" && (
              <>
                {getSectionButtonState(items) === 'clear' ? (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handleClearAllStaged(type)}
                    className="text-green-600 dark:text-green-400 border-green-300 dark:border-green-700 hover:bg-green-50 dark:hover:bg-green-950"
                  >
                    <X className="h-4 w-4 mr-1" />
                    Clear All Staged
                  </Button>
                ) : (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handleFulfillAll(type)}
                    disabled={getSectionButtonState(items) === 'disabled'}
                  >
                    <ClipboardCheck className="h-4 w-4 mr-1" />
                    Fulfill All
                  </Button>
                )}
              </>
            )}
            {/* Edit buttons - only visible in Edit mode */}
            {editorMode === "edit" && (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => handleOpenSectionMarkup(type)}
                  disabled={items.length === 0 || hasBeenInvoiced}
                  title={hasBeenInvoiced ? "Quote is frozen" : "Set markup for this section"}
                >
                  <Percent className="h-4 w-4 mr-1" />
                  Set Markup
                </Button>
                {extraButtons}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => openAddDialog(type)}
                  disabled={hasBeenInvoiced}
                  className="gap-2"
                  title={hasBeenInvoiced ? "Quote is frozen" : `Add ${addButtonLabel}`}
                >
                  <Plus className="h-4 w-4" />
                  {addButtonLabel}
                </Button>
              </>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {items.length === 0 && stagedAdds.filter(add => add.item_type === type).length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-4">
            No {title.toLowerCase()} items yet.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Description</TableHead>
                <TableHead className="text-right">Qty Ordered</TableHead>
                <TableHead className="text-right">Qty Pending</TableHead>
                {/* Qty to Fulfill column - only in invoicing mode */}
                {editorMode === "invoicing" && (
                  <TableHead className="text-right">Qty to Fulfill</TableHead>
                )}
                {/* Edit mode: Qty Fulfilled and Fulfilled Price come before pricing columns */}
                {editorMode === "edit" && (
                  <>
                    <TableHead className="text-right">Qty Fulfilled</TableHead>
                    <TableHead className="text-right">Fulfilled Price</TableHead>
                  </>
                )}
                {/* Unit Cost column - always visible */}
                <TableHead className="text-right">Unit Cost</TableHead>
                {/* Markup % column — shown in edit mode, or when any row diverges from the section default */}
                {showMarkupCol && (
                  <TableHead className="text-right">Markup %</TableHead>
                )}
                <TableHead className="text-right">Unit Price</TableHead>
                {/* Non-edit mode: Qty Fulfilled and Fulfilled Price come after Unit Price */}
                {editorMode !== "edit" && (
                  <>
                    <TableHead className="text-right">Qty Fulfilled</TableHead>
                    <TableHead className="text-right">Fulfilled Price</TableHead>
                  </>
                )}
                <TableHead className="text-right">Total</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((item) => {
                const staged = stagedFulfillments.get(item.id)
                const isDeleted = stagedDeletes.has(item.id)
                const isEdited = stagedEdits.has(item.id)
                const editedItem = stagedEdits.get(item.id)
                return (
                  <TableRow
                    key={item.id}
                    className={`
                      ${staged ? "border-l-4 border-l-green-500 dark:border-l-green-400" : ""}
                      ${isEdited && !isDeleted ? "border-l-4 border-l-blue-500 dark:border-l-blue-400" : ""}
                      ${isDeleted ? "border-l-4 border-l-red-500 dark:border-l-red-400 bg-red-50/50 dark:bg-red-950/50 line-through opacity-60" : ""}
                      ${item.qty_pending === 0 && !isDeleted ? "opacity-50" : ""}
                    `}
                  >
                    {/* Description Column */}
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <span className="font-medium">{getLineItemDescription(item)}</span>
                        {/* PMS percentage indicator for labour items */}
                        {item.is_pms && item.pms_percent != null && (
                          <span className="text-xs text-muted-foreground">({item.pms_percent}%)</span>
                        )}
                        {isEdited && !isDeleted && (
                          <Badge variant="outline" className="text-xs bg-blue-100 dark:bg-blue-900 text-blue-700 dark:text-blue-300 border-blue-300">
                            Edited
                          </Badge>
                        )}
                        {isDeleted && (
                          <Badge variant="outline" className="text-xs bg-red-100 dark:bg-red-900 text-red-700 dark:text-red-300 border-red-300">
                            Deleted
                          </Badge>
                        )}
                        {item.item_type === "part" && item.part && (
                          <span className="text-muted-foreground ml-2">- {item.part.description}</span>
                        )}
                      </div>
                    </TableCell>

                    {/* Qty Ordered Column */}
                    <TableCell className="text-right">
                      {editedItem?.quantity !== undefined && editedItem.quantity !== item.quantity ? (
                        <span className="font-bold text-blue-600 dark:text-blue-400">{editedItem.quantity}</span>
                      ) : (
                        item.quantity
                      )}
                    </TableCell>

                    {/* Qty Pending Column - now read-only display */}
                    <TableCell className="text-right">
                      {item.qty_pending}
                    </TableCell>

                    {/* Qty to Fulfill Column - only in invoicing mode */}
                    {editorMode === "invoicing" && (
                      <TableCell className="text-right">
                        {item.qty_pending === 0 ? (
                          // Fully fulfilled - show badge
                          <Badge className="bg-green-100 dark:bg-green-900 text-green-700 dark:text-green-300 border-green-300 dark:border-green-700 hover:bg-green-100">
                            <CheckCircle2 className="h-3 w-3 mr-1" />
                            Fully Fulfilled
                          </Badge>
                        ) : (
                          // Stepper control
                          <div className="flex items-center justify-end gap-1">
                            <Button
                              variant="outline"
                              size="sm"
                              className="h-7 w-7 p-0"
                              onClick={() => handleStepperDecrement(item)}
                              disabled={!staged || staged <= 0}
                              title="Decrease quantity"
                            >
                              <Minus className="h-3 w-3" />
                            </Button>
                            <Input
                              type="text"
                              inputMode="numeric"
                              pattern="[0-9]*"
                              value={getStepperDisplayValue(item.id)}
                              onChange={(e) => handleStepperInputChange(item, e.target.value)}
                              onBlur={() => handleStepperInputBlur(item)}
                              onKeyDown={(e) => handleStepperKeyDown(e, item)}
                              placeholder="0"
                              title={stepperErrors.get(item.id) || undefined}
                              className={`w-16 h-7 text-center text-sm ${
                                stepperErrors.has(item.id)
                                  ? "border-red-500 bg-red-50 dark:bg-red-950 focus-visible:ring-red-500"
                                  : staged
                                  ? "border-green-500 bg-green-50 dark:bg-green-950 focus-visible:ring-green-500"
                                  : ""
                              }`}
                            />
                            <Button
                              variant="outline"
                              size="sm"
                              className="h-7 w-7 p-0"
                              onClick={() => handleStepperIncrement(item)}
                              disabled={staged === item.qty_pending}
                              title="Increase quantity"
                            >
                              <Plus className="h-3 w-3" />
                            </Button>
                          </div>
                        )}
                      </TableCell>
                    )}

                    {/* Edit mode: Qty Fulfilled and Fulfilled Price come before Unit Price */}
                    {editorMode === "edit" && (
                      <>
                        {/* Qty Fulfilled Column */}
                        <TableCell className="text-right">
                          <span className={item.qty_fulfilled > 0 ? "text-green-600 dark:text-green-400 font-medium" : "text-muted-foreground"}>
                            {item.qty_fulfilled}
                          </span>
                        </TableCell>

                        {/* Fulfilled Price Column */}
                        <TableCell className="text-right">
                          {item.qty_fulfilled > 0 ? (
                            <span className="text-green-600 dark:text-green-400 font-medium">
                              ${getFulfilledLineItemValue(item).toFixed(2)}
                            </span>
                          ) : (
                            <span className="text-muted-foreground">-</span>
                          )}
                        </TableCell>
                      </>
                    )}

                    {/* Unit Cost Column — always visible; editable in edit mode */}
                    <TableCell className="text-right">
                      {item.is_pms ? (
                        <span className="text-muted-foreground">-</span>
                      ) : editorMode === "edit" ? (
                        <Input
                          type="number"
                          step="0.01"
                          min="0"
                          className="w-24 h-7 text-right text-sm inline-block"
                          value={editedItem?.base_cost ?? getLineItemBaseCost(item)}
                          onChange={(e) => {
                            const val = e.target.value === "" ? 0 : parseFloat(e.target.value)
                            if (!isNaN(val)) stageEdit(item, { base_cost: val })
                          }}
                          disabled={isDeleted || hasBeenInvoiced}
                        />
                      ) : (
                        <span className="text-muted-foreground">${getLineItemBaseCost(item).toFixed(2)}</span>
                      )}
                    </TableCell>

                    {/* Markup % Column — editable in edit mode; in view/invoicing modes
                        shows em-dash for rows matching the section default and accent
                        colour for genuine overrides. */}
                    {showMarkupCol && (
                      <TableCell className="text-right">
                        {item.is_pms ? (
                          <span className="text-muted-foreground">—</span>
                        ) : editorMode === "edit" ? (
                          <Input
                            type="number"
                            step="0.01"
                            min="0"
                            className="w-20 h-7 text-right text-sm inline-block"
                            value={editedItem?.markup_percent ?? item.markup_percent ?? 0}
                            onChange={(e) => {
                              const val = e.target.value === "" ? 0 : parseFloat(e.target.value)
                              if (!isNaN(val)) stageEdit(item, { markup_percent: val })
                            }}
                            disabled={isDeleted || hasBeenInvoiced}
                          />
                        ) : (item.markup_percent ?? 0) === sectionDefault ? (
                          <span className="text-muted-foreground" title="Matches section default">—</span>
                        ) : (
                          <span className="font-medium text-amber-600 dark:text-amber-400" title="Override">
                            {item.markup_percent ?? 0}%
                          </span>
                        )}
                      </TableCell>
                    )}

                    {/* Unit Price Column — dynamically calculated from unit cost + markup */}
                    <TableCell className="text-right">
                      {(() => {
                        const baseCost = editedItem?.base_cost ?? getLineItemBaseCost(item)
                        const markup = editedItem?.markup_percent ?? item.markup_percent ?? 0
                        const price = item.is_pms
                          ? (useEffectivePricing ? getEffectiveUnitPrice(item) : getLineItemUnitPrice(item))
                          : baseCost * (1 + markup / 100)
                        const hasPricingChange = editedItem?.markup_percent !== undefined || editedItem?.base_cost !== undefined
                        return hasPricingChange
                          ? <span className="font-bold text-blue-600 dark:text-blue-400">${price.toFixed(2)}</span>
                          : `$${price.toFixed(2)}`
                      })()}
                    </TableCell>

                    {/* Non-edit mode: Qty Fulfilled and Fulfilled Price come after Unit Price */}
                    {editorMode !== "edit" && (
                      <>
                        {/* Qty Fulfilled Column */}
                        <TableCell className="text-right">
                          <span className={item.qty_fulfilled > 0 ? "text-green-600 dark:text-green-400 font-medium" : "text-muted-foreground"}>
                            {item.qty_fulfilled}
                          </span>
                        </TableCell>

                        {/* Fulfilled Price Column */}
                        <TableCell className="text-right">
                          {item.qty_fulfilled > 0 ? (
                            <span className="text-green-600 dark:text-green-400 font-medium">
                              ${getFulfilledLineItemValue(item).toFixed(2)}
                            </span>
                          ) : (
                            <span className="text-muted-foreground">-</span>
                          )}
                        </TableCell>
                      </>
                    )}

                    {/* Total Column — uses staged unit cost and markup for live preview */}
                    <TableCell className="text-right">
                      {(() => {
                        const baseCost = editedItem?.base_cost ?? getLineItemBaseCost(item)
                        const markup = editedItem?.markup_percent ?? item.markup_percent ?? 0
                        const unitPrice = item.is_pms
                          ? (useEffectivePricing ? getEffectiveUnitPrice(item) : getLineItemUnitPrice(item))
                          : baseCost * (1 + markup / 100)
                        const quantity = editedItem?.quantity ?? item.quantity
                        const total = unitPrice * quantity
                        return <span className="font-medium">${total.toFixed(2)}</span>
                      })()}
                    </TableCell>
                    <TableCell className="text-right space-x-1">
                      {/* Edit Mode: Edit, Undo edit, Undo delete, Delete buttons */}
                      {editorMode === "edit" && (
                        <>
                          {/* Edit button */}
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => openEditDialog(item)}
                            disabled={hasBeenInvoiced || isDeleted}
                            title={hasBeenInvoiced ? "Quote is frozen" : isDeleted ? "Item marked for deletion" : "Edit"}
                          >
                            <Pencil className="h-4 w-4" />
                          </Button>
                          {/* Undo staged edit */}
                          {isEdited && !isDeleted && (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => {
                                const newEdits = new Map(stagedEdits)
                                newEdits.delete(item.id)
                                setStagedEdits(newEdits)
                              }}
                              title="Undo edit"
                              className="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300"
                            >
                              <X className="h-4 w-4" />
                            </Button>
                          )}
                          {/* Undo delete */}
                          {isDeleted && (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => unstageDelete(item.id)}
                              title="Undo delete"
                              className="text-red-600 dark:text-red-400 hover:text-red-700 dark:hover:text-red-300"
                            >
                              <X className="h-4 w-4" />
                            </Button>
                          )}
                          {/* Delete button */}
                          {!isDeleted && (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => stageDelete(item.id)}
                              disabled={hasBeenInvoiced}
                              className="text-destructive hover:text-destructive hover:bg-destructive/10"
                              title={hasBeenInvoiced ? "Quote is frozen" : "Mark for deletion"}
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          )}
                        </>
                      )}
                      {/* Invoicing Mode: Fulfill This Item button and Clear staged button */}
                      {editorMode === "invoicing" && item.qty_pending > 0 && (
                        <>
                          {/* Fulfill This Item - quick action to stage full pending quantity */}
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleQuickFulfill(item)}
                            title="Fulfill all pending"
                            className="h-7 w-7 p-0 border-green-300 dark:border-green-700 text-green-600 dark:text-green-400 hover:bg-green-50 dark:hover:bg-green-950"
                          >
                            <Check className="h-4 w-4" />
                          </Button>
                          {/* Clear staged */}
                          {staged && (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => clearStagedFulfillment(item.id)}
                              title="Clear staged"
                              className="h-7 w-7 p-0 text-green-600 dark:text-green-400 hover:text-green-700 dark:hover:text-green-300"
                            >
                              <X className="h-4 w-4" />
                            </Button>
                          )}
                        </>
                      )}
                      {/* Invoicing Mode: Fully fulfilled items show no actions */}
                      {editorMode === "invoicing" && item.qty_pending === 0 && (
                        <span className="text-muted-foreground">-</span>
                      )}
                    </TableCell>
                  </TableRow>
                )
              })}
              {/* Staged Add Items - shown inline with green highlighting */}
              {stagedAdds.filter(add => add.item_type === type).map((add) => {
                const getAddDescription = () => {
                  if (add.description) return add.description
                  if (add.item_type === "labor" && add.labor) return add.labor.description
                  if (add.item_type === "part" && add.part) return add.part.part_number
                  if (add.item_type === "misc" && add.miscellaneous) return add.miscellaneous.description
                  return "New item"
                }
                const getAddBaseCost = () => {
                  if (add.base_cost !== undefined) return add.base_cost
                  if (add.item_type === "part" && add.part) return add.part.cost
                  if (add.item_type === "labor" && add.labor) return add.labor.hours * add.labor.rate
                  if (add.item_type === "misc" && add.miscellaneous) return add.miscellaneous.unit_price
                  return 0
                }
                const getAddMarkup = () => {
                  if (add.markup_percent !== undefined) return add.markup_percent
                  if (add.item_type === "part" && add.part) return add.part.markup_percent ?? 0
                  if (add.item_type === "labor" && add.labor) return add.labor.markup_percent
                  if (add.item_type === "misc" && add.miscellaneous) return add.miscellaneous.markup_percent
                  return 0
                }
                const addBaseCost = getAddBaseCost()
                const addMarkup = getAddMarkup()
                const unitPrice = addBaseCost * (1 + addMarkup / 100)
                const total = unitPrice * add.quantity
                return (
                  <TableRow
                    key={`staged-add-${add.tempId}`}
                    className="border-l-4 border-l-green-500 dark:border-l-green-400 bg-green-50/50 dark:bg-green-950/50"
                  >
                    {/* Description */}
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-green-700 dark:text-green-300">{getAddDescription()}</span>
                        <Badge variant="outline" className="text-xs bg-green-100 dark:bg-green-900 text-green-700 dark:text-green-300 border-green-300">
                          + New
                        </Badge>
                        {add.item_type === "part" && add.part && (
                          <span className="text-muted-foreground ml-2">- {add.part.description}</span>
                        )}
                      </div>
                    </TableCell>
                    {/* Qty Ordered */}
                    <TableCell className="text-right">
                      {editorMode === "edit" ? (
                        <Input
                          type="number"
                          step="1"
                          min="1"
                          className="w-20 h-7 text-right text-sm inline-block"
                          value={add.quantity}
                          onChange={(e) => {
                            const val = e.target.value === "" ? 1 : parseInt(e.target.value, 10)
                            if (!isNaN(val) && val >= 1) updateStagedAdd(add.tempId, { quantity: val })
                          }}
                        />
                      ) : (
                        <span className="text-green-700 dark:text-green-300 font-medium">{add.quantity}</span>
                      )}
                    </TableCell>
                    {/* Qty Pending */}
                    <TableCell className="text-right text-muted-foreground">-</TableCell>
                    {/* Qty to Fulfill - only in invoicing mode */}
                    {editorMode === "invoicing" && (
                      <TableCell className="text-right text-muted-foreground">-</TableCell>
                    )}
                    {/* Edit mode: Qty Fulfilled and Fulfilled Price come before Unit Price */}
                    {editorMode === "edit" && (
                      <>
                        {/* Qty Fulfilled */}
                        <TableCell className="text-right text-muted-foreground">-</TableCell>
                        {/* Fulfilled Price */}
                        <TableCell className="text-right text-muted-foreground">-</TableCell>
                      </>
                    )}
                    {/* Unit Cost — for staged adds; editable in edit mode unless PMS */}
                    <TableCell className="text-right">
                      {add.is_pms ? (
                        <span className="text-muted-foreground">-</span>
                      ) : editorMode === "edit" ? (
                        <Input
                          type="number"
                          step="0.01"
                          min="0"
                          className="w-24 h-7 text-right text-sm inline-block"
                          value={addBaseCost}
                          onChange={(e) => {
                            const val = e.target.value === "" ? 0 : parseFloat(e.target.value)
                            if (!isNaN(val)) updateStagedAdd(add.tempId, { base_cost: val })
                          }}
                        />
                      ) : (
                        <span className="text-muted-foreground">${addBaseCost.toFixed(2)}</span>
                      )}
                    </TableCell>
                    {/* Markup % — for staged adds; editable in edit mode unless PMS */}
                    {showMarkupCol && (
                      <TableCell className="text-right">
                        {add.is_pms ? (
                          <span className="text-muted-foreground">—</span>
                        ) : editorMode === "edit" ? (
                          <Input
                            type="number"
                            step="0.01"
                            min="0"
                            className="w-20 h-7 text-right text-sm inline-block"
                            value={addMarkup}
                            onChange={(e) => {
                              const val = e.target.value === "" ? 0 : parseFloat(e.target.value)
                              if (!isNaN(val)) updateStagedAdd(add.tempId, { markup_percent: val })
                            }}
                          />
                        ) : (
                          <span className="text-green-700 dark:text-green-300">{addMarkup}%</span>
                        )}
                      </TableCell>
                    )}
                    {/* Unit Price */}
                    <TableCell className="text-right text-green-700 dark:text-green-300 font-medium">${unitPrice.toFixed(2)}</TableCell>
                    {/* Non-edit mode: Qty Fulfilled and Fulfilled Price come after Unit Price */}
                    {editorMode !== "edit" && (
                      <>
                        {/* Qty Fulfilled */}
                        <TableCell className="text-right text-muted-foreground">-</TableCell>
                        {/* Fulfilled Price */}
                        <TableCell className="text-right text-muted-foreground">-</TableCell>
                      </>
                    )}
                    {/* Total */}
                    <TableCell className="text-right text-green-700 dark:text-green-300 font-bold">${total.toFixed(2)}</TableCell>
                    {/* Actions */}
                    <TableCell className="text-right">
                      {editorMode === "edit" && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => {
                            setStagedAdds(prev => prev.filter(a => a.tempId !== add.tempId))
                          }}
                          title="Remove staged add"
                          className="text-green-600 dark:text-green-400 hover:text-green-700 dark:hover:text-green-300"
                        >
                          <X className="h-4 w-4" />
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
            {(items.length > 0 || stagedAdds.filter(add => add.item_type === type).length > 0) && (
              <TableFooter>
                <TableRow className="bg-muted/50">
                  {/* Description */}
                  <TableCell className="font-semibold">Section Total</TableCell>
                  {/* Qty Ordered */}
                  <TableCell className="text-right font-semibold">{calculateSectionTotals(items, useEffectivePricing).qtyOrdered}</TableCell>
                  {/* Qty Pending */}
                  <TableCell className="text-right font-semibold">{calculateSectionTotals(items, useEffectivePricing).qtyPending}</TableCell>
                  {/* Qty to Fulfill - only in invoicing mode */}
                  {editorMode === "invoicing" && (
                    <TableCell className="text-right font-semibold text-green-700 dark:text-green-300">
                      {calculateStagedSectionTotals(items).stagedQty > 0 ? calculateStagedSectionTotals(items).stagedQty : ""}
                    </TableCell>
                  )}
                  {/* Edit mode: Qty Fulfilled and Fulfilled Price come before Unit Price */}
                  {editorMode === "edit" && (
                    <>
                      {/* Qty Fulfilled */}
                      <TableCell className="text-right font-semibold">{calculateSectionTotals(items, useEffectivePricing).qtyFulfilled}</TableCell>
                      {/* Fulfilled Price */}
                      <TableCell className="text-right font-semibold">
                        {calculateSectionTotals(items, useEffectivePricing).qtyFulfilled > 0 ? (
                          <span className="text-green-600 dark:text-green-400">
                            ${calculateSectionTotals(items, useEffectivePricing).fulfilledValue.toFixed(2)}
                          </span>
                        ) : (
                          <span className="text-muted-foreground">-</span>
                        )}
                      </TableCell>
                    </>
                  )}
                  {/* Unit Cost — empty in footer */}
                  <TableCell></TableCell>
                  {/* Markup % — empty in footer */}
                  {showMarkupCol && (
                    <TableCell></TableCell>
                  )}
                  {/* Unit Price */}
                  <TableCell></TableCell>
                  {/* Non-edit mode: Qty Fulfilled and Fulfilled Price come after Unit Price */}
                  {editorMode !== "edit" && (
                    <>
                      {/* Qty Fulfilled */}
                      <TableCell className="text-right font-semibold">{calculateSectionTotals(items, useEffectivePricing).qtyFulfilled}</TableCell>
                      {/* Fulfilled Price */}
                      <TableCell className="text-right font-semibold">
                        {calculateSectionTotals(items, useEffectivePricing).qtyFulfilled > 0 ? (
                          <span className="text-green-600 dark:text-green-400">
                            ${calculateSectionTotals(items, useEffectivePricing).fulfilledValue.toFixed(2)}
                          </span>
                        ) : (
                          <span className="text-muted-foreground">-</span>
                        )}
                      </TableCell>
                    </>
                  )}
                  {/* Total */}
                  <TableCell className="text-right font-bold">${calculateSectionTotals(items, useEffectivePricing).total.toFixed(2)}</TableCell>
                  {/* Actions */}
                  <TableCell></TableCell>
                </TableRow>
                {/* Staging summary row - only in invoicing mode with staged items */}
                {editorMode === "invoicing" && calculateStagedSectionTotals(items).itemCount > 0 && (
                  <TableRow className="bg-green-50/50 dark:bg-green-950/50 border-t-2 border-green-200 dark:border-green-800">
                    {/* Description */}
                    <TableCell className="font-semibold text-green-700 dark:text-green-300">
                      Staging for Invoice ({calculateStagedSectionTotals(items).itemCount} items)
                    </TableCell>
                    {/* Qty Ordered */}
                    <TableCell></TableCell>
                    {/* Qty Pending */}
                    <TableCell></TableCell>
                    {/* Qty to Fulfill */}
                    <TableCell className="text-right font-semibold text-green-700 dark:text-green-300">
                      {calculateStagedSectionTotals(items).stagedQty}
                    </TableCell>
                    {/* Unit Cost */}
                    <TableCell></TableCell>
                    {/* Markup % */}
                    {showMarkupCol && (
                      <TableCell></TableCell>
                    )}
                    {/* Unit Price */}
                    <TableCell></TableCell>
                    {/* Qty Fulfilled */}
                    <TableCell></TableCell>
                    {/* Fulfilled Price */}
                    <TableCell></TableCell>
                    {/* Total */}
                    <TableCell className="text-right font-bold text-green-700 dark:text-green-300">
                      ${calculateStagedSectionTotals(items).stagedTotal.toFixed(2)}
                    </TableCell>
                    {/* Actions */}
                    <TableCell></TableCell>
                  </TableRow>
                )}
              </TableFooter>
            )}
          </Table>
        )}
      </CardContent>
    </Card>
    )
  }

  return (
    <div className={`p-6 space-y-6 pb-24 rounded-lg transition-colors ${
      editorMode === "edit"
        ? "border-2 border-blue-500 dark:border-blue-400 bg-blue-50/30 dark:bg-blue-950/30"
        : editorMode === "invoicing"
        ? "border-2 border-green-500 dark:border-green-400 bg-green-50/30 dark:bg-green-950/30"
        : "border-2 border-transparent"
    }`}>
      {/* Frozen Banner */}
      {hasBeenInvoiced && (
        <Card className="border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950">
          <CardContent className="py-3">
            <div className="flex items-center gap-2 text-amber-700 dark:text-amber-300">
              <Lock className="h-4 w-4" />
              <span className="font-medium">Quote Frozen</span>
              <span className="text-sm">— This quote has been invoiced. You can create additional invoices but cannot modify line items.</span>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Header */}
      <div className="flex justify-between items-start">
        <div>
          <h2 className="text-xl font-semibold">{quote.quote_number}</h2>
          <p className="text-sm text-muted-foreground">
            Created: {formatDate(quote.created_at)}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {quote.legacy_imported && (
            <Badge
              variant="outline"
              className="bg-slate-500/10 text-slate-700 border-slate-500/30 dark:text-slate-300"
              title="This quote was brought in from the original UC Vision CSV migration."
            >
              Imported
            </Badge>
          )}
          <StatusBadge status={quote.status} />
        </div>
      </div>

      {/* Client PO Number */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <ClipboardCheck className="h-4 w-4" />
            Client PO Number
          </CardTitle>
        </CardHeader>
        <CardContent>
          {isEditingClientPo ? (
            <div className="flex items-center gap-2">
              <Input
                value={clientPoNumber}
                onChange={(e) => setClientPoNumber(e.target.value)}
                placeholder="Enter client PO number"
                className="max-w-xs"
              />
              <Button
                size="sm"
                onClick={handleSaveClientPoNumber}
                disabled={savingClientPo}
              >
                {savingClientPo ? "Saving..." : "Save"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  setClientPoNumber(quote.client_po_number || "")
                  setIsEditingClientPo(false)
                }}
              >
                Cancel
              </Button>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              {quote.client_po_number ? (
                <span className="font-medium">{quote.client_po_number}</span>
              ) : (
                <span className="text-muted-foreground" title={editorMode === "edit" ? "Click pencil to set" : undefined}>—</span>
              )}
              {/* Edit button only visible in Edit mode */}
              {editorMode === "edit" && (
                <Button size="sm" variant="ghost" onClick={() => setIsEditingClientPo(true)}>
                  <Pencil className="h-4 w-4" />
                </Button>
              )}
            </div>
          )}
          {!quote.client_po_number && (
            <p className="text-sm text-amber-600 mt-2">
              A Client PO Number is required before you can create an invoice.
            </p>
          )}
        </CardContent>
      </Card>

      {/* Cost Code */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <Hash className="h-4 w-4" />
            Cost Code
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="max-w-md">
            <SearchableSelect
              options={costCodes.map(cc => ({ value: cc.id.toString(), label: `${cc.code} - ${cc.description}` }))}
              value={quote.cost_code_id?.toString()}
              onChange={handleCostCodeChange}
              placeholder="Select cost code..."
              searchPlaceholder="Search cost codes..."
              disabled={editorMode !== "edit" || savingCostCode}
            />
            {savingCostCode && (
              <p className="text-xs text-muted-foreground mt-1">Saving...</p>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Work Description */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <FileText className="h-4 w-4" />
            Work Description
          </CardTitle>
        </CardHeader>
        <CardContent>
          {isEditingWorkDescription ? (
            <div className="space-y-2">
              <Textarea
                value={workDescription}
                onChange={(e) => setWorkDescription(e.target.value)}
                placeholder="Describe the work covered by this quote..."
                rows={4}
              />
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  onClick={handleSaveWorkDescription}
                  disabled={savingWorkDescription}
                >
                  {savingWorkDescription ? "Saving..." : "Save"}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    setWorkDescription(quote.work_description || "")
                    setIsEditingWorkDescription(false)
                  }}
                >
                  Cancel
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex items-start gap-2">
              {quote.work_description ? (
                <p className="whitespace-pre-wrap">{quote.work_description}</p>
              ) : (
                <span className="text-muted-foreground" title={editorMode === "edit" ? "Click pencil to set" : undefined}>—</span>
              )}
              {/* Edit button only visible in Edit mode */}
              {editorMode === "edit" && (
                <Button size="sm" variant="ghost" onClick={() => setIsEditingWorkDescription(true)}>
                  <Pencil className="h-4 w-4" />
                </Button>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Hardware Schedule Version */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <FileText className="h-4 w-4" />
            Hardware Schedule Version
          </CardTitle>
        </CardHeader>
        <CardContent>
          {isEditingHardwareScheduleVersion ? (
            <div className="space-y-2">
              <Input
                value={hardwareScheduleVersion}
                onChange={(e) => setHardwareScheduleVersion(e.target.value)}
                placeholder="e.g. Rev A"
              />
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  onClick={handleSaveHardwareScheduleVersion}
                  disabled={savingHardwareScheduleVersion}
                >
                  {savingHardwareScheduleVersion ? "Saving..." : "Save"}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    setHardwareScheduleVersion(quote.hardware_schedule_version || "")
                    setIsEditingHardwareScheduleVersion(false)
                  }}
                >
                  Cancel
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex items-start gap-2">
              {quote.hardware_schedule_version ? (
                <p className="whitespace-pre-wrap">{quote.hardware_schedule_version}</p>
              ) : (
                <span className="text-muted-foreground" title={editorMode === "edit" ? "Click pencil to set" : undefined}>—</span>
              )}
              {editorMode === "edit" && (
                <Button size="sm" variant="ghost" onClick={() => setIsEditingHardwareScheduleVersion(true)}>
                  <Pencil className="h-4 w-4" />
                </Button>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Markup Control */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex justify-between items-center">
            <CardTitle className="text-base flex items-center gap-2">
              <Percent className="h-4 w-4" />
              Markup Control
            </CardTitle>
            <div className="flex items-center gap-3">
              {/* Display current section markup badges */}
              {quote.markup_control_enabled && (quote.parts_markup_percent != null || quote.labor_markup_percent != null || quote.misc_markup_percent != null) && (
                editorMode === "edit" ? (
                  <button
                    onClick={handleOpenEditMarkup}
                    className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-blue-50 dark:bg-blue-950 text-blue-700 dark:text-blue-300 border border-blue-300 dark:border-blue-700 hover:bg-blue-100 dark:hover:bg-blue-900 transition-colors cursor-pointer"
                    title="Click to edit section markups"
                  >
                    Parts: {quote.parts_markup_percent ?? 0}% | Labour: {quote.labor_markup_percent ?? 0}% | Misc: {quote.misc_markup_percent ?? 0}%
                    <Pencil className="h-3 w-3" />
                  </button>
                ) : (
                  <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-blue-50 dark:bg-blue-950 text-blue-700 dark:text-blue-300 border border-blue-300 dark:border-blue-700">
                    Parts: {quote.parts_markup_percent ?? 0}% | Labour: {quote.labor_markup_percent ?? 0}% | Misc: {quote.misc_markup_percent ?? 0}%
                  </span>
                )
              )}
              {/* Toggle button only visible in Edit mode */}
              {editorMode === "edit" && (
                <Button
                  size="sm"
                  variant={quote.markup_control_enabled ? "default" : "outline"}
                  onClick={handleToggleMarkupControl}
                  disabled={togglingMarkupControl}
                >
                  {togglingMarkupControl ? "..." : (quote.markup_control_enabled ? "Enabled" : "Disabled")}
                </Button>
              )}
              {/* Read-only badge in View mode */}
              {editorMode !== "edit" && (
                <Badge variant={quote.markup_control_enabled ? "default" : "secondary"}>
                  {quote.markup_control_enabled ? "Enabled" : "Disabled"}
                </Badge>
              )}
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            {quote.markup_control_enabled
              ? "Section markups are applied to all non-PMS items."
              : "Enable to apply markup percentages per section (Parts, Labour, Misc) to all line items (excluding PMS items)."}
          </p>
        </CardContent>
      </Card>

      {/* Parts Section */}
      {renderLineItemSection("Parts", partItems, "part", <Package className="h-4 w-4" />, "Add Part")}

      {/* Labor Section */}
      {renderLineItemSection(
        "Labour",
        laborItems2,
        "labor",
        <Wrench className="h-4 w-4" />,
        "Add Labour",
        // Extra buttons for PMS
        <>
          <Button
            variant="outline"
            size="sm"
            onClick={() => openPmsDialog("dollar")}
            disabled={hasBeenInvoiced}
            className="gap-2"
          >
            <Plus className="h-4 w-4" />
            Add PMS $
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => openPmsDialog("percent")}
            disabled={hasBeenInvoiced}
            className="gap-2"
          >
            <Plus className="h-4 w-4" />
            Add PMS %
          </Button>
        </>,
        true  // useEffectivePricing for PMS % items
      )}

      {/* Misc Section */}
      {renderLineItemSection("Miscellaneous", miscItems2, "misc", <FileText className="h-4 w-4" />, "Add Misc", (
        <>
          <Button
            variant="outline"
            size="sm"
            onClick={handleCalculateAndAddParking}
            disabled={addingParking || hasBeenInvoiced}
            className="gap-2"
          >
            <Car className="h-4 w-4" />
            {addingParking ? "Adding..." : "Parking"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={handleOpenTravelDistanceDialog}
            disabled={hasBeenInvoiced}
            className="gap-2"
          >
            <MapPin className="h-4 w-4" />
            Travel
          </Button>
        </>
      ))}

      {/* Total Summary */}
      <Card>
        <CardContent className="pt-6">
          <div className="space-y-4">
            {/* Average Markup with formula tooltip */}
            <div className="flex justify-between items-center">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-muted-foreground">Average Markup:</span>
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger>
                      <Info className="h-4 w-4 text-muted-foreground cursor-help" />
                    </TooltipTrigger>
                    <TooltipContent className="max-w-xs">
                      <p className="text-xs">
                        Weighted Average = Σ(Markup% × Unit Cost × Qty) / Σ(Unit Cost × Qty)
                      </p>
                      <p className="text-xs mt-1 text-muted-foreground">
                        Parts: Unit Cost = Part Cost | Labor: Hours × Rate | Misc: Unit Price
                      </p>
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              </div>
              {hasStagedChanges ? (
                <span className="text-lg font-semibold">
                  {calculateAverageMarkup().toFixed(2)}%
                  <span className="text-muted-foreground mx-1">→</span>
                  <span className="text-blue-600 dark:text-blue-400">{calculateProjectedMarkup().toFixed(2)}%</span>
                </span>
              ) : (
                <span className="text-lg font-semibold">{calculateAverageMarkup().toFixed(2)}%</span>
              )}
            </div>

            {/* Advanced totals toggle (Total Margin etc. hidden by default) */}
            <div className="flex justify-end -mt-2">
              <Button
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-xs text-muted-foreground hover:text-foreground gap-1"
                onClick={() => setShowAdvancedTotals((v) => !v)}
              >
                {showAdvancedTotals ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                {showAdvancedTotals ? "Hide advanced totals" : "Show advanced totals"}
              </Button>
            </div>

            {/* Total Margin — advanced; redundant with Average Markup so off by default */}
            {showAdvancedTotals && (
              <div className="flex justify-between items-center">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-muted-foreground">Total Margin:</span>
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger>
                        <Info className="h-4 w-4 text-muted-foreground cursor-help" />
                      </TooltipTrigger>
                      <TooltipContent className="max-w-xs">
                        <p className="text-xs">
                          Margin % = (Selling Price - Mfg Cost) / Selling Price × 100
                        </p>
                        <p className="text-xs mt-1 text-muted-foreground">
                          Manufacturing Cost = Parts cost only (Labor/Misc = $0)
                        </p>
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                </div>
                {hasStagedChanges ? (
                  <span className="text-lg font-semibold">
                    {calculateTotalMargin().toFixed(2)}%
                    <span className="text-muted-foreground mx-1">→</span>
                    <span className="text-blue-600 dark:text-blue-400">{calculateProjectedMargin().toFixed(2)}%</span>
                  </span>
                ) : (
                  <span className="text-lg font-semibold">{calculateTotalMargin().toFixed(2)}%</span>
                )}
              </div>
            )}

            <Separator />

            {/* Quote Subtotal */}
            <div className="flex justify-between items-center">
              <span className="text-sm font-medium text-muted-foreground">Subtotal:</span>
              {hasStagedChanges ? (
                <span className="text-lg font-semibold">
                  ${calculateTotal().toFixed(2)}
                  <span className="text-muted-foreground mx-1">→</span>
                  <span className="text-blue-600 dark:text-blue-400">${calculateProjectedTotal().toFixed(2)}</span>
                </span>
              ) : (
                <span className="text-lg font-semibold">${calculateTotal().toFixed(2)}</span>
              )}
            </div>

            {/* HST */}
            {companySettings && companySettings.hst_rate > 0 && (
              <div className="flex justify-between items-center">
                <span className="text-sm font-medium text-muted-foreground">HST ({companySettings.hst_rate}%):</span>
                {hasStagedChanges ? (
                  <span className="text-lg font-semibold">
                    ${(calculateTotal() * companySettings.hst_rate / 100).toFixed(2)}
                    <span className="text-muted-foreground mx-1">→</span>
                    <span className="text-blue-600 dark:text-blue-400">${(calculateProjectedTotal() * companySettings.hst_rate / 100).toFixed(2)}</span>
                  </span>
                ) : (
                  <span className="text-lg font-semibold">${(calculateTotal() * companySettings.hst_rate / 100).toFixed(2)}</span>
                )}
              </div>
            )}

            {/* Quote Total (with HST) */}
            <div className="flex justify-between items-center">
              <span className="text-lg font-semibold">Total:</span>
              {hasStagedChanges ? (() => {
                const hstRate = companySettings?.hst_rate ?? 0
                const currentTotal = calculateTotal() * (1 + hstRate / 100)
                const projectedTotal = calculateProjectedTotal() * (1 + hstRate / 100)
                return (
                  <span className="text-2xl font-bold">
                    ${currentTotal.toFixed(2)}
                    <span className="text-muted-foreground mx-1 text-lg">→</span>
                    <span className="text-blue-600 dark:text-blue-400">${projectedTotal.toFixed(2)}</span>
                  </span>
                )
              })() : (
                <span className="text-2xl font-bold">
                  ${(calculateTotal() * (1 + (companySettings?.hst_rate ?? 0) / 100)).toFixed(2)}
                </span>
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Audit Trail */}
      <QuoteAuditTrail
        quoteId={quoteId}
        currentVersion={quote.current_version}
        onRevert={() => {
          fetchQuote()
          onUpdate?.()
        }}
      />

      {/* Unified Floating Button Group */}
      <div className="fixed bottom-6 right-6 z-50">
        <div className="flex flex-col items-end gap-2">
          {/* Secondary actions always available (Print / Clone) */}
          {editorMode === "view" && (
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPrintDialogOpen(true)}
                disabled={isPrinting}
                className="shadow-md gap-2 bg-background"
              >
                {isPrinting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Printer className="h-4 w-4" />}
                {isPrinting ? "Generating..." : "Print"}
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={handleCloneQuote}
                disabled={isCloning}
                className="shadow-md gap-2 bg-background"
              >
                <Copy className="h-4 w-4" />
                {isCloning ? "Cloning..." : "Clone"}
              </Button>
            </div>
          )}

          <div className="flex gap-2">
            {/* View Mode: Edit Quote and Create Invoice buttons */}
            {editorMode === "view" && !hasBeenInvoiced && (
              <>
                <Button
                  size="lg"
                  variant="outline"
                  onClick={enterEditMode}
                  className="shadow-lg gap-2"
                >
                  <Pencil className="h-5 w-5" />
                  Edit Quote
                </Button>
                <Button
                  size="lg"
                  onClick={enterInvoicingMode}
                  disabled={!hasAnyPendingQuantity}
                  className="shadow-lg gap-2"
                  title={!hasAnyPendingQuantity ? "No items have pending quantities to invoice" : undefined}
                >
                  <Receipt className="h-5 w-5" />
                  Create Invoice
                </Button>
              </>
            )}

            {/* View Mode (Frozen): Only Create Invoice button */}
            {editorMode === "view" && hasBeenInvoiced && (
              <Button
                size="lg"
                onClick={enterInvoicingMode}
                disabled={!hasAnyPendingQuantity}
                className="shadow-lg gap-2"
                title={!hasAnyPendingQuantity ? "All items have been fully invoiced" : undefined}
              >
                <Receipt className="h-5 w-5" />
                Create Invoice
              </Button>
            )}

            {/* Edit Mode: Discard and Commit buttons */}
            {editorMode === "edit" && (
              <>
                <Button
                  size="lg"
                  variant="outline"
                  onClick={() => exitEditMode()}
                  className="shadow-lg gap-2"
                >
                  <X className="h-5 w-5" />
                  Discard
                </Button>
                <Button
                  size="lg"
                  onClick={() => setCommitConfirmOpen(true)}
                  disabled={!hasStagedChanges || isCommitting}
                  className="shadow-lg gap-2 bg-blue-600 hover:bg-blue-700"
                >
                  <GitCommit className="h-5 w-5" />
                  {isCommitting ? "Committing..." : `Commit${hasStagedChanges ? ` (${stagedChangesCount})` : ""}`}
                </Button>
              </>
            )}

            {/* Invoicing Mode: Cancel and Confirm Invoice buttons */}
            {editorMode === "invoicing" && (
              <>
                <Button
                  size="lg"
                  variant="outline"
                  onClick={() => exitInvoicingMode(false)}
                  className="shadow-lg gap-2"
                >
                  <X className="h-5 w-5" />
                  Cancel
                </Button>
                <Button
                  size="lg"
                  onClick={() => exitInvoicingMode(true)}
                  disabled={stagedFulfillments.size === 0 || isCreatingInvoice || !quote.client_po_number}
                  className="shadow-lg gap-2 bg-green-600 hover:bg-green-700"
                >
                  <Receipt className="h-5 w-5" />
                  {isCreatingInvoice ? "Creating..." : `Confirm Invoice${stagedFulfillments.size > 0 ? ` (${stagedFulfillments.size})` : ""}`}
                </Button>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Add Line Item Dialog */}
      <Dialog
        open={addDialogOpen}
        onOpenChange={(open) => {
          setAddDialogOpen(open)
          if (!open) setAddDialogMode("select")
        }}
      >
        <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              {addDialogMode === "edit-inventory" ? (
                <>
                  <button
                    type="button"
                    onClick={() => setAddDialogMode("select")}
                    className="p-1 -ml-1 rounded hover:bg-muted"
                    title="Back to selection"
                    aria-label="Back to selection"
                  >
                    <ArrowLeft className="h-4 w-4" />
                  </button>
                  <Pencil className="h-5 w-5" />
                  Edit {addDialogType === "labor" ? "Labour" : addDialogType.charAt(0).toUpperCase() + addDialogType.slice(1)} Details
                </>
              ) : (
                <>
                  {getTypeIcon(addDialogType)}
                  Add {addDialogType === "labor" ? "Labour" : addDialogType.charAt(0).toUpperCase() + addDialogType.slice(1)}
                </>
              )}
            </DialogTitle>
            <DialogDescription>
              {addDialogMode === "edit-inventory"
                ? "Updates apply to the inventory master record. Future selections will use these values; previously committed line items are unaffected."
                : `Add a ${addDialogType === "labor" ? "labour" : addDialogType} line item to this quote.`}
            </DialogDescription>
          </DialogHeader>

          {addDialogMode === "select" && (
            <div className="space-y-4 pt-4">
              {addDialogType === "labor" && (
                <div className="space-y-2">
                  <Label>Labour Item</Label>
                  <SearchableSelect<Labor>
                    options={laborItems.map((labor): SearchableSelectOption => ({
                      value: labor.id.toString(),
                      label: labor.description,
                      description: `$${labor.rate}/hr x ${labor.hours}hrs`,
                    }))}
                    value={selectedLaborId}
                    onChange={setSelectedLaborId}
                    placeholder="Select labour"
                    searchPlaceholder="Search labour items..."
                    emptyMessage="No labour items found."
                    allowCreate={true}
                    createLabel="Create New Labour"
                    createDialogTitle="Create New Labour Item"
                    createForm={<LaborForm />}
                    onCreateSuccess={(newLabor) => {
                      setLaborItems([...laborItems, newLabor])
                      setSelectedLaborId(newLabor.id.toString())
                    }}
                  />
                </div>
              )}

              {addDialogType === "part" && (
                <div className="space-y-2">
                  <Label>Part</Label>
                  <SearchableSelect<Part>
                    options={parts.map((part): SearchableSelectOption => ({
                      value: part.id.toString(),
                      label: `${part.part_number} - ${part.description}`,
                      description: `$${(part.cost * (1 + (part.markup_percent ?? 0) / 100)).toFixed(2)}${part.labor_items && part.labor_items.length > 0 ? ` (${part.labor_items.length} linked labour)` : ''}`,
                    }))}
                    value={selectedPartId}
                    onChange={setSelectedPartId}
                    placeholder="Select part"
                    searchPlaceholder="Search parts..."
                    emptyMessage="No parts found."
                    allowCreate={true}
                    createLabel="Create New Part"
                    createDialogTitle="Create New Part"
                    createForm={<PartForm />}
                    onCreateSuccess={(newPart) => {
                      setParts([...parts, newPart])
                      setSelectedPartId(newPart.id.toString())
                    }}
                  />
                </div>
              )}

              {addDialogType === "misc" && (
                <div className="space-y-2">
                  <Label>Miscellaneous Item</Label>
                  <SearchableSelect<Miscellaneous>
                    options={miscItems
                      .filter(misc => !misc.is_system_item) // Exclude system items (Parking, Travel Distance)
                      .map((misc): SearchableSelectOption => ({
                        value: misc.id.toString(),
                        label: misc.description,
                        description: `$${(misc.unit_price * (1 + misc.markup_percent / 100)).toFixed(2)}`,
                      }))}
                    value={selectedMiscId}
                    onChange={setSelectedMiscId}
                    placeholder="Select misc item"
                    searchPlaceholder="Search misc items..."
                    emptyMessage="No miscellaneous items found."
                    allowCreate={true}
                    createLabel="Create New Misc Item"
                    createDialogTitle="Create New Miscellaneous Item"
                    createForm={<MiscForm />}
                    onCreateSuccess={(newMisc) => {
                      setMiscItems([...miscItems, newMisc])
                      setSelectedMiscId(newMisc.id.toString())
                    }}
                  />
                </div>
              )}

              {/* Inventory details panel — shown after an item is selected. */}
              {(selectedPart || selectedLabor || selectedMisc) && (
                <div className="rounded-lg border bg-muted/30 p-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <h4 className="text-sm font-semibold flex items-center gap-2">
                      <Info className="h-4 w-4" />
                      Inventory Details
                    </h4>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setAddDialogMode("edit-inventory")}
                      className="h-7 gap-2"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                      Edit details
                    </Button>
                  </div>

                  {selectedPart && (
                    <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
                      <div>
                        <dt className="text-muted-foreground">Part Number</dt>
                        <dd className="font-medium">{selectedPart.part_number}</dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Description</dt>
                        <dd className="font-medium">{selectedPart.description}</dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Base Cost</dt>
                        <dd className="font-medium">${selectedPart.cost.toFixed(2)}</dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Markup</dt>
                        <dd className="font-medium">{(selectedPart.markup_percent ?? 0).toFixed(2)}%</dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Unit Price</dt>
                        <dd className="font-medium">${(selectedPart.cost * (1 + (selectedPart.markup_percent ?? 0) / 100)).toFixed(2)}</dd>
                      </div>
                      {selectedPart.list_price != null && (
                        <div>
                          <dt className="text-muted-foreground">List Price</dt>
                          <dd className="font-medium">${selectedPart.list_price.toFixed(2)}</dd>
                        </div>
                      )}
                      {selectedPart.discount_percent != null && (
                        <div>
                          <dt className="text-muted-foreground">Discount</dt>
                          <dd className="font-medium">{selectedPart.discount_percent.toFixed(2)}%</dd>
                        </div>
                      )}
                      {selectedPart.labor_items && selectedPart.labor_items.length > 0 && (
                        <div className="col-span-2">
                          <dt className="text-muted-foreground">Linked Labour</dt>
                          <dd className="mt-1 space-y-0.5 text-xs font-medium">
                            {selectedPart.labor_items.map((l) => (
                              <div key={l.id}>• {l.description} ({l.hours}hr × ${l.rate}/hr)</div>
                            ))}
                          </dd>
                        </div>
                      )}
                    </dl>
                  )}

                  {selectedLabor && (
                    <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
                      <div className="col-span-2">
                        <dt className="text-muted-foreground">Description</dt>
                        <dd className="font-medium">{selectedLabor.description}</dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Rate</dt>
                        <dd className="font-medium">${selectedLabor.rate.toFixed(2)}/hr</dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Hours</dt>
                        <dd className="font-medium">{selectedLabor.hours}</dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Base Cost</dt>
                        <dd className="font-medium">${(selectedLabor.rate * selectedLabor.hours).toFixed(2)}</dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Markup</dt>
                        <dd className="font-medium">{selectedLabor.markup_percent.toFixed(2)}%</dd>
                      </div>
                      <div className="col-span-2">
                        <dt className="text-muted-foreground">Unit Price</dt>
                        <dd className="font-medium">${(selectedLabor.rate * selectedLabor.hours * (1 + selectedLabor.markup_percent / 100)).toFixed(2)}</dd>
                      </div>
                    </dl>
                  )}

                  {selectedMisc && (
                    <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
                      <div className="col-span-2">
                        <dt className="text-muted-foreground">Description</dt>
                        <dd className="font-medium">{selectedMisc.description}</dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Base Unit Price</dt>
                        <dd className="font-medium">${selectedMisc.unit_price.toFixed(2)}</dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Markup</dt>
                        <dd className="font-medium">{selectedMisc.markup_percent.toFixed(2)}%</dd>
                      </div>
                      <div className="col-span-2">
                        <dt className="text-muted-foreground">Unit Price</dt>
                        <dd className="font-medium">${(selectedMisc.unit_price * (1 + selectedMisc.markup_percent / 100)).toFixed(2)}</dd>
                      </div>
                    </dl>
                  )}
                </div>
              )}

              <div className="space-y-2">
                <Label>Quantity</Label>
                <Input
                  type="number"
                  step="1"
                  min="1"
                  value={quantity}
                  onChange={(e) => setQuantity(e.target.value)}
                />
              </div>

              <Button
                onClick={handleAddLineItem}
                className="w-full"
                disabled={
                  (addDialogType === "labor" && (!selectedLaborId || laborItems.length === 0)) ||
                  (addDialogType === "part" && (!selectedPartId || parts.length === 0)) ||
                  (addDialogType === "misc" && (!selectedMiscId || miscItems.length === 0))
                }
              >
                <Plus className="h-4 w-4 mr-2" />
                Add Line Item
              </Button>
            </div>
          )}

          {addDialogMode === "edit-inventory" && (
            <div className="pt-4">
              {addDialogType === "part" && selectedPart && (
                <PartForm
                  part={selectedPart}
                  onSuccess={handlePartUpdateSuccess}
                  onCancel={() => setAddDialogMode("select")}
                />
              )}
              {addDialogType === "labor" && selectedLabor && (
                <LaborForm
                  labor={selectedLabor}
                  onSuccess={handleLaborUpdateSuccess}
                  onCancel={() => setAddDialogMode("select")}
                />
              )}
              {addDialogType === "misc" && selectedMisc && (
                <MiscForm
                  misc={selectedMisc}
                  onSuccess={handleMiscUpdateSuccess}
                  onCancel={() => setAddDialogMode("select")}
                />
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Edit Line Item Dialog */}
      <Dialog open={editDialogOpen} onOpenChange={setEditDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit Line Item</DialogTitle>
            <DialogDescription>
              Update the quantity for this line item.
            </DialogDescription>
          </DialogHeader>

          {editingLineItem && (
            <div className="space-y-4 pt-4">
              <div className="bg-muted/50 p-3 rounded-md">
                <p className="text-sm font-medium">{getDescriptionLabel(editingLineItem)}</p>
                <p className="text-lg">{getLineItemDescription(editingLineItem)}</p>
              </div>

              <div className="space-y-2">
                <Label>Quantity</Label>
                <Input
                  type="number"
                  step="1"
                  min="1"
                  value={editQuantity}
                  onChange={(e) => setEditQuantity(e.target.value)}
                />
              </div>

              <div className="space-y-2">
                <Label>Unit Price</Label>
                <div className="px-3 py-2 bg-muted/50 rounded-md text-muted-foreground">
                  ${getLineItemUnitPrice(editingLineItem).toFixed(2)}
                </div>
              </div>

              <Button onClick={handleUpdateLineItem} className="w-full">
                Save Changes
              </Button>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Auto-Add Labor Confirmation Dialog */}
      <Dialog open={autoAddLaborDialogOpen} onOpenChange={setAutoAddLaborDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add Linked Labour Items?</DialogTitle>
            <DialogDescription>
              This part has {linkedLaborToAdd.length} linked labour item(s). Would you like to add them to the quote as well?
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3 py-4">
            <p className="text-sm font-medium">Part: {pendingPart?.part_number} - {pendingPart?.description}</p>
            <div className="bg-muted/50 p-3 rounded-md space-y-2">
              <p className="text-sm font-medium">Linked Labour Items:</p>
              {linkedLaborToAdd.map((labor) => (
                <div key={labor.id} className="flex justify-between text-sm">
                  <span>{labor.description}</span>
                  <span className="text-muted-foreground">
                    ${(labor.hours * labor.rate * (1 + labor.markup_percent / 100)).toFixed(2)}
                  </span>
                </div>
              ))}
            </div>
          </div>

          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={handleSkipAutoAddLabor}>
              Just Add Part
            </Button>
            <Button onClick={handleConfirmAutoAddLabor}>
              Add Part + Labour
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Print Options Dialog */}
      <Dialog open={printDialogOpen} onOpenChange={setPrintDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Printer className="h-4 w-4" />
              Print Quote
            </DialogTitle>
            <DialogDescription>
              Choose how line items appear in the printout.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 pt-2">
            <button
              type="button"
              onClick={() => runPrintQuote(true)}
              className="w-full text-left p-4 rounded-md border bg-background hover:bg-accent transition-colors"
            >
              <div className="font-medium">Include line-item breakdown</div>
              <div className="text-sm text-muted-foreground mt-1">
                Show each item's description, quantity, unit price, and per-item total, along with section subtotals.
              </div>
            </button>
            <button
              type="button"
              onClick={() => runPrintQuote(false)}
              className="w-full text-left p-4 rounded-md border bg-background hover:bg-accent transition-colors"
            >
              <div className="font-medium">Quantities only</div>
              <div className="text-sm text-muted-foreground mt-1">
                Show each item's description and quantity, but hide all unit prices, line totals, and section subtotals. Only the final Subtotal, HST, and Total are shown.
              </div>
            </button>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPrintDialogOpen(false)}>
              Cancel
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* PMS (Project Management Services) Dialog */}
      <Dialog open={pmsDialogOpen} onOpenChange={setPmsDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Wrench className="h-4 w-4" />
              {pmsType === "percent" ? "Add PMS %" : "Add PMS $"}
            </DialogTitle>
            <DialogDescription>
              {pmsType === "percent"
                ? "Enter a percentage of the non-PMS quote total. This will dynamically update as the quote changes."
                : "Enter the dollar amount for Project Management Services."}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 pt-4">
            {pmsType === "percent" && (
              <div className="bg-muted/50 p-3 rounded-md">
                <p className="text-sm text-muted-foreground">Current Non-PMS Total</p>
                <p className="text-lg font-semibold">${calculateNonPmsTotal().toFixed(2)}</p>
              </div>
            )}
            <div className="space-y-2">
              <Label>{pmsType === "percent" ? "Percentage (%)" : "Amount ($)"}</Label>
              <Input
                type="number"
                step={pmsType === "percent" ? "0.1" : "0.01"}
                min="0"
                value={pmsValue}
                onChange={(e) => setPmsValue(e.target.value)}
                placeholder={pmsType === "percent" ? "e.g., 10" : "e.g., 500.00"}
              />
              {pmsType === "percent" && pmsValue && !isNaN(parseFloat(pmsValue)) && (
                <p className="text-sm text-muted-foreground">
                  = ${(calculateNonPmsTotal() * parseFloat(pmsValue) / 100).toFixed(2)}
                </p>
              )}
            </div>
            <Button onClick={handleAddPmsItem} className="w-full">
              <Plus className="h-4 w-4 mr-2" />
              Add Project Management Services
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Markup Control Enable Dialog */}
      <Dialog open={markupControlDialogOpen} onOpenChange={setMarkupControlDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Percent className="h-4 w-4" />
              Enable Markup Control
            </DialogTitle>
            <DialogDescription>
              Set markup percentages per section. These will replace individual item markups (excluding PMS items).
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 pt-4">
            <div className="space-y-2">
              <Label>Parts Markup (%)</Label>
              <Input
                type="number"
                step="0.01"
                min="0"
                value={pendingPartsMarkup}
                onChange={(e) => setPendingPartsMarkup(e.target.value)}
                placeholder="e.g., 15.00"
              />
            </div>
            <div className="space-y-2">
              <Label>Labour Markup (%)</Label>
              <Input
                type="number"
                step="0.01"
                min="0"
                value={pendingLaborMarkup}
                onChange={(e) => setPendingLaborMarkup(e.target.value)}
                placeholder="e.g., 20.00"
              />
            </div>
            <div className="space-y-2">
              <Label>Miscellaneous Markup (%)</Label>
              <Input
                type="number"
                step="0.01"
                min="0"
                value={pendingMiscMarkup}
                onChange={(e) => setPendingMiscMarkup(e.target.value)}
                placeholder="e.g., 10.00"
              />
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setMarkupControlDialogOpen(false)}>
                Cancel
              </Button>
              <Button
                onClick={handleConfirmEnableMarkupControl}
                disabled={togglingMarkupControl || (!pendingPartsMarkup && !pendingLaborMarkup && !pendingMiscMarkup)}
              >
                {togglingMarkupControl ? "Applying..." : "Enable Markup Control"}
              </Button>
            </DialogFooter>
          </div>
        </DialogContent>
      </Dialog>

      {/* Edit Section Markup Dialog */}
      <Dialog open={editMarkupDialogOpen} onOpenChange={setEditMarkupDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Pencil className="h-4 w-4" />
              Edit Section Markups
            </DialogTitle>
            <DialogDescription>
              Update markup percentages per section. All line items (excluding PMS) will be recalculated.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 pt-4">
            <div className="space-y-2">
              <Label>Parts Markup (%)</Label>
              <Input
                type="number"
                step="0.01"
                min="0"
                value={editingPartsMarkup}
                onChange={(e) => setEditingPartsMarkup(e.target.value)}
                placeholder="e.g., 15.00"
              />
            </div>
            <div className="space-y-2">
              <Label>Labour Markup (%)</Label>
              <Input
                type="number"
                step="0.01"
                min="0"
                value={editingLaborMarkup}
                onChange={(e) => setEditingLaborMarkup(e.target.value)}
                placeholder="e.g., 20.00"
              />
            </div>
            <div className="space-y-2">
              <Label>Miscellaneous Markup (%)</Label>
              <Input
                type="number"
                step="0.01"
                min="0"
                value={editingMiscMarkup}
                onChange={(e) => setEditingMiscMarkup(e.target.value)}
                placeholder="e.g., 10.00"
              />
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setEditMarkupDialogOpen(false)}>
                Cancel
              </Button>
              <Button
                onClick={handleConfirmUpdateMarkup}
                disabled={updatingMarkupPercent || (!editingPartsMarkup && !editingLaborMarkup && !editingMiscMarkup)}
              >
                {updatingMarkupPercent ? "Updating..." : "Update Markups"}
              </Button>
            </DialogFooter>
          </div>
        </DialogContent>
      </Dialog>

      {/* Invoice Creation Confirmation Dialog */}
      <AlertDialog open={confirmDialogOpen} onOpenChange={setConfirmDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Create Invoice?</AlertDialogTitle>
            <AlertDialogDescription>
              You are about to create an invoice for {stagedFulfillments.size} item{stagedFulfillments.size !== 1 ? 's' : ''} totaling{' '}
              <span className="font-semibold text-green-600 dark:text-green-400">
                ${calculateStagedGrandTotal().stagedTotal.toFixed(2)}
              </span>.
              This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleCreateInvoice}>
              Create Invoice
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Invoice Preview Modal */}
      <Dialog open={previewModalOpen} onOpenChange={setPreviewModalOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <FileText className="h-5 w-5" />
              Invoice Preview
            </DialogTitle>
            <DialogDescription>
              Review the staged items before creating the invoice.
            </DialogDescription>
          </DialogHeader>

          {/* Comparison Breakdown */}
          <div className="grid grid-cols-4 gap-4 text-center">
            <div className="p-4 bg-muted/30 rounded-lg">
              <p className="text-xs text-muted-foreground">Quote Total</p>
              <p className="text-xl font-bold">${calculateTotal().toFixed(2)}</p>
            </div>
            <div className="p-4 bg-blue-50 dark:bg-blue-950 rounded-lg">
              <p className="text-xs text-blue-600 dark:text-blue-400">Already Invoiced</p>
              <p className="text-xl font-bold text-blue-600 dark:text-blue-400">
                ${calculateInvoicedTotals().invoicedTotal.toFixed(2)}
              </p>
            </div>
            <div className="p-4 bg-green-50 dark:bg-green-950 rounded-lg border-2 border-green-300 dark:border-green-700">
              <p className="text-xs text-green-600 dark:text-green-400">Staging Now</p>
              <p className="text-xl font-bold text-green-600 dark:text-green-400">
                ${calculateStagedGrandTotal().stagedTotal.toFixed(2)}
              </p>
            </div>
            <div className="p-4 bg-orange-50 dark:bg-orange-950 rounded-lg">
              <p className="text-xs text-orange-600 dark:text-orange-400">Remaining After</p>
              <p className="text-xl font-bold text-orange-600 dark:text-orange-400">
                ${(calculateTotal() - calculateInvoicedTotals().invoicedTotal - calculateStagedGrandTotal().stagedTotal).toFixed(2)}
              </p>
            </div>
          </div>

          <Separator />

          {/* Staged Items List */}
          <div className="max-h-64 overflow-y-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Item</TableHead>
                  <TableHead className="text-right">Unit Price</TableHead>
                  <TableHead className="text-right">Qty Staging</TableHead>
                  <TableHead className="text-right">Total</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {quote?.line_items
                  .filter(item => stagedFulfillments.has(item.id))
                  .map(item => (
                    <TableRow key={item.id}>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <Badge variant={getTypeBadgeVariant(item.item_type)}>
                            {item.item_type === "labor" ? "Labour" : item.item_type.charAt(0).toUpperCase() + item.item_type.slice(1)}
                          </Badge>
                          <span className="font-medium">{getLineItemDescription(item)}</span>
                        </div>
                      </TableCell>
                      <TableCell className="text-right">${getEffectiveUnitPrice(item).toFixed(2)}</TableCell>
                      <TableCell className="text-right font-medium text-green-600 dark:text-green-400">
                        {stagedFulfillments.get(item.id)}
                      </TableCell>
                      <TableCell className="text-right font-medium">
                        ${getStagedLineItemTotal(item).toFixed(2)}
                      </TableCell>
                    </TableRow>
                  ))}
              </TableBody>
            </Table>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setPreviewModalOpen(false)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Section Markup Dialog */}
      <Dialog open={sectionMarkupDialogOpen} onOpenChange={setSectionMarkupDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Percent className="h-4 w-4" />
              Set {sectionMarkupSection === "labor" ? "Labour" : sectionMarkupSection === "part" ? "Parts" : "Miscellaneous"} Markup
            </DialogTitle>
            <DialogDescription>
              Set the markup percentage for all {sectionMarkupSection === "labor" ? "labour" : sectionMarkupSection === "part" ? "parts" : "miscellaneous"} items.
              {!quote?.markup_control_enabled && " This will enable Markup Control for this quote."}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 pt-4">
            <div className="space-y-2">
              <Label>Markup (%)</Label>
              <Input
                type="number"
                step="0.01"
                min="0"
                value={sectionMarkupValue}
                onChange={(e) => setSectionMarkupValue(e.target.value)}
                placeholder="e.g., 15.00"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setSectionMarkupDialogOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleApplySectionMarkup}
              disabled={!sectionMarkupValue || applyingSectionMarkup}
            >
              {applyingSectionMarkup ? "Applying..." : "Apply Markup"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Travel Distance Selection Dialog */}
      <Dialog open={travelDistanceDialogOpen} onOpenChange={setTravelDistanceDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <MapPin className="h-4 w-4" />
              Select Travel Distance
            </DialogTitle>
            <DialogDescription>
              Choose a travel distance tier. The quantity will be calculated as days (labor hours / 8, rounded up).
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 pt-4">
            <div className="bg-muted/50 p-3 rounded-md">
              <p className="text-sm text-muted-foreground">Total Labor Hours (excl. PMS)</p>
              <p className="text-lg font-semibold">{calculateTotalLaborHours().toFixed(1)} hours</p>
              <p className="text-sm text-muted-foreground mt-1">
                = {Math.ceil(calculateTotalLaborHours() / 8) || 0} day(s)
              </p>
            </div>

            <div className="space-y-2">
              <Label>Travel Distance Tier</Label>
              <Select
                value={selectedTravelDistanceId}
                onValueChange={setSelectedTravelDistanceId}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select travel distance..." />
                </SelectTrigger>
                <SelectContent>
                  {travelDistanceItems.map(item => (
                    <SelectItem key={item.id} value={item.id.toString()}>
                      {item.description} - ${(item.unit_price * (1 + item.markup_percent / 100)).toFixed(2)}/day
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {selectedTravelDistanceId && (
              <div className="bg-blue-50 dark:bg-blue-950 p-3 rounded-md">
                <p className="text-sm font-medium">Preview:</p>
                <p className="text-sm">
                  {Math.ceil(calculateTotalLaborHours() / 8)} day(s) x $
                  {(travelDistanceItems.find(i => i.id.toString() === selectedTravelDistanceId)?.unit_price ?? 0) *
                   (1 + (travelDistanceItems.find(i => i.id.toString() === selectedTravelDistanceId)?.markup_percent ?? 0) / 100)
                  }
                  {" = $"}
                  {(Math.ceil(calculateTotalLaborHours() / 8) *
                    (travelDistanceItems.find(i => i.id.toString() === selectedTravelDistanceId)?.unit_price ?? 0) *
                    (1 + (travelDistanceItems.find(i => i.id.toString() === selectedTravelDistanceId)?.markup_percent ?? 0) / 100)
                  ).toFixed(2)}
                </p>
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setTravelDistanceDialogOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleConfirmTravelDistance}
              disabled={!selectedTravelDistanceId || addingTravelDistance}
            >
              {addingTravelDistance ? "Adding..." : "Add Travel Distance"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Discard Changes Confirmation Dialog */}
      <AlertDialog open={discardConfirmOpen} onOpenChange={setDiscardConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-amber-500" />
              Discard Changes?
            </AlertDialogTitle>
            <AlertDialogDescription>
              You have {stagedChangesCount} unsaved change{stagedChangesCount !== 1 ? "s" : ""}. Are you sure you want to discard all changes? This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep Editing</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => exitEditMode(true)}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Discard Changes
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Client PO Missing Dialog - prevents entering invoicing mode */}
      <AlertDialog open={clientPoMissingDialogOpen} onOpenChange={setClientPoMissingDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-destructive" />
              Client PO Number Required
            </AlertDialogTitle>
            <AlertDialogDescription>
              A Client PO Number is required before creating an invoice. Please add a Client PO Number in the quote details section first.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogAction onClick={() => setClientPoMissingDialogOpen(false)}>
              OK
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* No Pending Quantities Dialog - prevents entering invoicing mode when nothing to invoice */}
      <AlertDialog open={noPendingDialogOpen} onOpenChange={setNoPendingDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-amber-500" />
              No Items to Invoice
            </AlertDialogTitle>
            <AlertDialogDescription>
              There are no line items with pending quantities to invoice. All items have been fully fulfilled or the quote has no line items.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogAction onClick={() => setNoPendingDialogOpen(false)}>
              OK
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Quote Changed Dialog - Flow 7E: warns when quote changed externally during invoicing or editing */}
      <AlertDialog open={quoteChangedDialogOpen} onOpenChange={setQuoteChangedDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-amber-500" />
              Quote Data Changed
            </AlertDialogTitle>
            <AlertDialogDescription>
              {editorMode === "edit"
                ? "This quote has been modified since you started editing. Your staged changes have been cleared to ensure data accuracy. Please review the updated quote and re-apply your edits."
                : "This quote has been modified since you started staging items for invoicing. Your staged quantities have been cleared to ensure data accuracy. Please review the updated quote and re-stage your items."
              }
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogAction onClick={() => setQuoteChangedDialogOpen(false)}>
              OK
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Commit Changes Confirmation Dialog */}
      <AlertDialog open={commitConfirmOpen} onOpenChange={setCommitConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <GitCommit className="h-5 w-5 text-blue-500" />
              Commit Changes?
            </AlertDialogTitle>
            <AlertDialogDescription>
              You are about to commit {stagedChangesCount} change{stagedChangesCount !== 1 ? "s" : ""} to this quote:
              <ul className="mt-2 space-y-1 text-sm">
                {stagedAdds.length > 0 && (
                  <li className="text-green-600 dark:text-green-400">• {stagedAdds.length} item{stagedAdds.length !== 1 ? "s" : ""} added</li>
                )}
                {stagedEdits.size > 0 && (
                  <li className="text-blue-600 dark:text-blue-400">• {stagedEdits.size} item{stagedEdits.size !== 1 ? "s" : ""} modified</li>
                )}
                {stagedDeletes.size > 0 && (
                  <li className="text-red-600 dark:text-red-400">• {stagedDeletes.size} item{stagedDeletes.size !== 1 ? "s" : ""} deleted</li>
                )}
              </ul>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleCommitChanges}
              className="bg-blue-600 hover:bg-blue-700"
            >
              Commit Changes
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Edit Preview Dialog */}
      <Dialog open={editPreviewOpen} onOpenChange={setEditPreviewOpen}>
        <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Eye className="h-5 w-5" />
              Preview Changes
            </DialogTitle>
            <DialogDescription>
              Review your staged changes before committing.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            {stagedAdds.length > 0 && (
              <div>
                <h4 className="font-medium text-green-600 dark:text-green-400 mb-2">Added Items ({stagedAdds.length})</h4>
                <div className="space-y-2">
                  {stagedAdds.map(item => (
                    <div key={item.tempId} className="p-2 bg-green-50 dark:bg-green-950 rounded border border-green-200 dark:border-green-800">
                      <span className="font-medium">
                        {item.item_type === "part" && item.part?.part_number}
                        {item.item_type === "labor" && (item.labor?.description || item.description || "Labour")}
                        {item.item_type === "misc" && (item.miscellaneous?.description || item.description || "Miscellaneous")}
                      </span>
                      <span className="text-muted-foreground ml-2">× {item.quantity}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {stagedEdits.size > 0 && (
              <div>
                <h4 className="font-medium text-blue-600 dark:text-blue-400 mb-2">Modified Items ({stagedEdits.size})</h4>
                <div className="space-y-2">
                  {Array.from(stagedEdits.entries()).map(([id, edit]) => (
                    <div key={id} className="p-2 bg-blue-50 dark:bg-blue-950 rounded border border-blue-200 dark:border-blue-800">
                      <span className="font-medium">
                        {edit.originalItem.part?.part_number ||
                         edit.originalItem.labor?.description ||
                         edit.originalItem.miscellaneous?.description ||
                         edit.originalItem.description ||
                         "Item"}
                      </span>
                      <div className="text-sm text-muted-foreground mt-1">
                        {edit.quantity !== undefined && (
                          <div>Qty: {edit.originalItem.quantity} → {edit.quantity}</div>
                        )}
                        {edit.unit_price !== undefined && (
                          <div>Price: ${edit.originalItem.unit_price?.toFixed(2)} → ${edit.unit_price.toFixed(2)}</div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {stagedDeletes.size > 0 && (
              <div>
                <h4 className="font-medium text-red-600 dark:text-red-400 mb-2">Deleted Items ({stagedDeletes.size})</h4>
                <div className="space-y-2">
                  {Array.from(stagedDeletes).map(id => {
                    const item = quote?.line_items.find(li => li.id === id)
                    return item ? (
                      <div key={id} className="p-2 bg-red-50 dark:bg-red-950 rounded border border-red-200 dark:border-red-800 line-through opacity-60">
                        <span className="font-medium">
                          {item.part?.part_number ||
                           item.labor?.description ||
                           item.miscellaneous?.description ||
                           item.description ||
                           "Item"}
                        </span>
                        <span className="text-muted-foreground ml-2">× {item.quantity}</span>
                      </div>
                    ) : null
                  })}
                </div>
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditPreviewOpen(false)}>
              Close
            </Button>
            <Button onClick={() => { setEditPreviewOpen(false); setCommitConfirmOpen(true); }} className="bg-blue-600 hover:bg-blue-700">
              Commit Changes
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
