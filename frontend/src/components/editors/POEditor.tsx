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
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Calendar as CalendarWidget } from "@/components/ui/calendar"
import type { SearchableSelectOption } from "@/components/ui/searchable-select"
import { api } from "@/api/client"
import type {
  PurchaseOrder, POLineItem, POLineItemCreate, POLineItemType, POStatus, Part, CostCode,
  POEditorMode, StagedPOEdit, StagedPOAdd,
  StagedPOLineItemChange, POCommitEditsRequest, POReceivingCreate, POReceivingLineItemCreate,
  CompanySettings, Project
} from "@/types"
import {
  Plus, Minus, Trash2, Package, FileText, Building, Pencil, Copy,
  X, GitCommit, Eye, AlertTriangle, Check, Calendar, Loader2, Hash, Printer
} from "lucide-react"
import { StatusBadge } from "@/components/ui/status-badge"
import { formatDate } from "@/lib/format"
import { PartForm } from "@/components/forms/PartForm"
import { POAuditTrail } from "./POAuditTrail"

interface POEditorProps {
  poId: number
  onUpdate?: () => void
  onSelectPO?: (poId: number) => void
  onDirtyStateChange?: (isDirty: boolean) => void
}

export function POEditor({ poId, onUpdate, onSelectPO, onDirtyStateChange }: POEditorProps) {
  // ===== Core State =====
  const [po, setPO] = useState<PurchaseOrder | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // ===== Dialog States =====
  const [addDialogOpen, setAddDialogOpen] = useState(false)
  const [addDialogType, setAddDialogType] = useState<POLineItemType>("part")

  // Edit dialog states
  const [editDialogOpen, setEditDialogOpen] = useState(false)
  const [editingLineItem, setEditingLineItem] = useState<POLineItem | null>(null)

  // ===== Resource States =====
  const [parts, setParts] = useState<Part[]>([])

  // ===== Add Form Fields =====
  const [selectedPartId, setSelectedPartId] = useState<string>("")
  const [miscDescription, setMiscDescription] = useState("")
  const [quantity, setQuantity] = useState("1")
  const [unitPrice, setUnitPrice] = useState("")

  // ===== Edit Form Fields =====
  const [editQuantity, setEditQuantity] = useState("1")
  const [editUnitPrice, setEditUnitPrice] = useState("")
  const [editDescription, setEditDescription] = useState("")

  // ===== Editor Mode State (Commit-based workflow) =====
  const [editorMode, setEditorMode] = useState<POEditorMode>("view")
  const [stagedEdits, setStagedEdits] = useState<Map<number, StagedPOEdit>>(new Map())
  const [stagedAdds, setStagedAdds] = useState<StagedPOAdd[]>([])
  const [stagedDeletes, setStagedDeletes] = useState<Set<number>>(new Set())
  const [nextTempId, setNextTempId] = useState(-1) // Negative IDs for staged adds
  const [isCommitting, setIsCommitting] = useState(false)
  const [discardConfirmOpen, setDiscardConfirmOpen] = useState(false)
  const [editPreviewOpen, setEditPreviewOpen] = useState(false)
  const [commitConfirmOpen, setCommitConfirmOpen] = useState(false)

  // ===== Version Tracking =====
  const [editModeStartVersion, setEditModeStartVersion] = useState<number | null>(null)
  const [poChangedDialogOpen, setPOChangedDialogOpen] = useState(false)

  // ===== Metadata Editing States =====
  const [workDescription, setWorkDescription] = useState("")
  const [isEditingWorkDescription, setIsEditingWorkDescription] = useState(false)
  const [savingWorkDescription, setSavingWorkDescription] = useState(false)

  const [vendorPoNumber, setVendorPoNumber] = useState("")
  const [isEditingVendorPo, setIsEditingVendorPo] = useState(false)
  const [savingVendorPo, setSavingVendorPo] = useState(false)

  const [expectedDeliveryDate, setExpectedDeliveryDate] = useState("")
  const [isEditingDeliveryDate, setIsEditingDeliveryDate] = useState(false)
  const [savingDeliveryDate, setSavingDeliveryDate] = useState(false)

  // ===== Company Settings (for HST rate) =====
  const [companySettings, setCompanySettings] = useState<CompanySettings | null>(null)

  // ===== Clone State =====
  const [isCloning, setIsCloning] = useState(false)
  const [cloneConfirmOpen, setCloneConfirmOpen] = useState(false)

  // Print PDF state
  const [isPrinting, setIsPrinting] = useState(false)

  // ===== Cost Codes =====
  const [costCodes, setCostCodes] = useState<CostCode[]>([])
  const [savingCostCode, setSavingCostCode] = useState(false)

  // ===== Receiving Mode State =====
  const [stagedReceivings, setStagedReceivings] = useState<Map<number, {
    qty_received: number;
    actual_unit_price?: number;
  }>>(new Map())
  const [receivedDate, setReceivedDate] = useState<string>(new Date().toISOString().split('T')[0])
  const [receivingDialogOpen, setReceivingDialogOpen] = useState(false)
  const [isSubmittingReceiving, setIsSubmittingReceiving] = useState(false)

  // ===== Computed Values =====
  const hasBeenReceived = po?.line_items.some(item => item.qty_received > 0) ?? false
  const hasStagedChanges = stagedEdits.size > 0 || stagedAdds.length > 0 || stagedDeletes.size > 0
  const stagedChangesCount = stagedEdits.size + stagedAdds.length + stagedDeletes.size
  const hasAnyUnsavedChanges = editorMode === "edit" && hasStagedChanges
  const canEdit = editorMode === "edit" && po?.status === "Draft"
  const hasPendingItems = po?.line_items.some(item => item.qty_pending > 0) ?? false
  const canReceive = (po?.status === "Sent" || po?.status === "Received") && hasPendingItems
  const stagedReceivingsCount = Array.from(stagedReceivings.values()).filter(r => r.qty_received > 0).length

  // ===== Data Fetching =====

  const fetchPO = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await api.purchaseOrders.get(poId)

      // Detect external changes during edit mode
      if (editorMode === "edit" && editModeStartVersion !== null && data.current_version !== editModeStartVersion) {
        clearEditModeState()
        setPOChangedDialogOpen(true)
        setEditModeStartVersion(data.current_version)
      }

      setPO(data)
      setWorkDescription(data.work_description || "")
      setVendorPoNumber(data.vendor_po_number || "")
      setExpectedDeliveryDate(data.expected_delivery_date || "")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch purchase order")
    } finally {
      setLoading(false)
    }
  }

  const fetchParts = async () => {
    try {
      const partsData = await api.parts.getAll()
      setParts(partsData)
    } catch (err) {
      console.error("Failed to fetch parts", err)
    }
  }

  useEffect(() => {
    fetchPO()
    fetchParts()
    api.companySettings.get().then(setCompanySettings).catch(() => {})
    api.costCodes.getAll().then(setCostCodes).catch(() => {})
  }, [poId])

  // ===== Navigation Guard =====
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

  // ===== Notify parent of dirty state =====
  useEffect(() => {
    onDirtyStateChange?.(hasAnyUnsavedChanges)
  }, [hasAnyUnsavedChanges])

  // ===== Edit Mode Handlers =====

  const enterEditMode = () => {
    if (po?.status !== "Draft") return
    if (hasBeenReceived) return // Cannot edit POs that have been received
    setEditModeStartVersion(po?.current_version ?? null)
    setEditorMode("edit")
  }

  const exitEditMode = (confirmDiscard = false) => {
    if (hasStagedChanges && !confirmDiscard) {
      setDiscardConfirmOpen(true)
      return
    }
    clearEditModeState()
    setEditorMode("view")
    setDiscardConfirmOpen(false)
  }

  const clearEditModeState = () => {
    setStagedEdits(new Map())
    setStagedAdds([])
    setStagedDeletes(new Set())
    setEditModeStartVersion(null)
  }

  const stageEdit = (item: POLineItem, changes: Partial<Omit<StagedPOEdit, "originalItem">>) => {
    const newStagedEdits = new Map(stagedEdits)
    const existing = newStagedEdits.get(item.id)

    if (existing) {
      newStagedEdits.set(item.id, { ...existing, ...changes })
    } else {
      newStagedEdits.set(item.id, { originalItem: item, ...changes })
    }

    // Check if all values are back to original - if so, remove the staged edit
    const staged = newStagedEdits.get(item.id)!
    const isUnchanged =
      (staged.quantity === undefined || staged.quantity === item.quantity) &&
      (staged.unit_price === undefined || staged.unit_price === (item.unit_price ?? 0)) &&
      (staged.description === undefined || staged.description === (item.description ?? ""))

    if (isUnchanged) {
      newStagedEdits.delete(item.id)
    }

    setStagedEdits(newStagedEdits)
  }

  const stageAdd = (newItem: Omit<StagedPOAdd, "tempId">) => {
    const tempId = nextTempId
    setNextTempId(prev => prev - 1)
    setStagedAdds(prev => [...prev, { ...newItem, tempId }])
  }

  const unstageAdd = (tempId: number) => {
    setStagedAdds(prev => prev.filter(item => item.tempId !== tempId))
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
      // Pre-submit staleness check
      if (editModeStartVersion !== null) {
        const freshPO = await api.purchaseOrders.get(poId)
        if (freshPO.current_version !== editModeStartVersion) {
          clearEditModeState()
          setPO(freshPO)
          setWorkDescription(freshPO.work_description || "")
          setVendorPoNumber(freshPO.vendor_po_number || "")
          setExpectedDeliveryDate(freshPO.expected_delivery_date || "")
          setEditModeStartVersion(freshPO.current_version)
          setPOChangedDialogOpen(true)
          setIsCommitting(false)
          return
        }
      }

      const changes: StagedPOLineItemChange[] = []

      // Add staged adds
      for (const add of stagedAdds) {
        changes.push({
          action: "add",
          item_type: add.item_type,
          part_id: add.part_id,
          description: add.description,
          quantity: add.quantity,
          unit_price: add.unit_price,
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
        })
      }

      // Add staged deletes
      for (const lineItemId of stagedDeletes) {
        changes.push({
          action: "delete",
          line_item_id: lineItemId,
        })
      }

      const request: POCommitEditsRequest = { changes }
      await api.purchaseOrders.commitEdits(poId, request)

      // Clear staged changes and exit edit mode
      setStagedEdits(new Map())
      setStagedAdds([])
      setStagedDeletes(new Set())
      setEditorMode("view")

      // Refresh PO data
      fetchPO()
      onUpdate?.()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to commit changes")
    } finally {
      setIsCommitting(false)
    }
  }

  // ===== Display Value Helpers (considering staged edits) =====

  const getDisplayQuantity = (item: POLineItem): number => {
    const staged = stagedEdits.get(item.id)
    return staged?.quantity ?? item.quantity
  }

  const getDisplayUnitPrice = (item: POLineItem): number => {
    const staged = stagedEdits.get(item.id)
    return staged?.unit_price ?? item.unit_price ?? 0
  }

  const getDisplayDescription = (item: POLineItem): string => {
    const staged = stagedEdits.get(item.id)
    if (staged?.description !== undefined) return staged.description
    return item.description ?? ""
  }

  // ===== Line Item CRUD Handlers =====

  const openAddDialog = (type: POLineItemType) => {
    setAddDialogType(type)
    setSelectedPartId("")
    setMiscDescription("")
    setQuantity("1")
    setUnitPrice("")
    setAddDialogOpen(true)
  }

  const openEditDialog = (item: POLineItem) => {
    setEditingLineItem(item)
    // Use staged values if available, otherwise original
    const staged = stagedEdits.get(item.id)
    setEditQuantity((staged?.quantity ?? item.quantity).toString())
    setEditUnitPrice((staged?.unit_price ?? item.unit_price ?? 0).toString())
    setEditDescription(staged?.description ?? item.description ?? "")
    setEditDialogOpen(true)
  }

  const handleAddLineItem = async () => {
    const qty = Math.max(1, parseFloat(quantity) || 0)

    if (addDialogType === "part") {
      if (!selectedPartId) return
      const part = parts.find((p) => p.id === parseInt(selectedPartId))
      if (!part) return

      // For POs we use base cost (no markup)
      const partUnitPrice = part.cost

      if (editorMode === "edit") {
        stageAdd({
          item_type: "part",
          part_id: part.id,
          quantity: qty,
          unit_price: partUnitPrice,
          part: part,
        })
        setAddDialogOpen(false)
        return
      }

      // Direct API call in view mode (fallback)
      try {
        await api.purchaseOrders.addLine(poId, {
          item_type: "part",
          part_id: part.id,
          quantity: qty,
          unit_price: partUnitPrice,
        })
        setAddDialogOpen(false)
        fetchPO()
        onUpdate?.()
      } catch (err) {
        alert(err instanceof Error ? err.message : "Failed to add line item")
      }
    } else if (addDialogType === "misc") {
      if (!miscDescription) return
      const miscUnitPrice = parseFloat(unitPrice) || 0

      if (editorMode === "edit") {
        stageAdd({
          item_type: "misc",
          description: miscDescription,
          quantity: qty,
          unit_price: miscUnitPrice,
        })
        setAddDialogOpen(false)
        return
      }

      try {
        await api.purchaseOrders.addLine(poId, {
          item_type: "misc",
          description: miscDescription,
          quantity: qty,
          unit_price: miscUnitPrice,
        })
        setAddDialogOpen(false)
        fetchPO()
        onUpdate?.()
      } catch (err) {
        alert(err instanceof Error ? err.message : "Failed to add line item")
      }
    }
  }

  const handleEditLineItem = () => {
    if (!editingLineItem) return

    const newQuantity = Math.max(1, parseFloat(editQuantity) || 0)
    const newUnitPrice = parseFloat(editUnitPrice) || 0
    const newDescription = editDescription.trim()

    if (editorMode === "edit") {
      const changes: Partial<Omit<StagedPOEdit, "originalItem">> = {}

      if (newQuantity !== editingLineItem.quantity) {
        changes.quantity = newQuantity
      }
      if (newUnitPrice !== (editingLineItem.unit_price ?? 0)) {
        changes.unit_price = newUnitPrice
      }
      if (editingLineItem.item_type === "misc" && newDescription !== (editingLineItem.description ?? "")) {
        changes.description = newDescription
      }

      if (Object.keys(changes).length > 0) {
        stageEdit(editingLineItem, changes)
      }

      setEditDialogOpen(false)
      setEditingLineItem(null)
      return
    }

    setEditDialogOpen(false)
    setEditingLineItem(null)
  }

  const handleDeleteLine = (lineId: number) => {
    if (editorMode === "edit") {
      if (stagedDeletes.has(lineId)) {
        unstageDelete(lineId)
      } else {
        stageDelete(lineId)
      }
      return
    }
  }

  // ===== Metadata Handlers (save immediately, no staging) =====

  const handleSaveWorkDescription = async () => {
    setSavingWorkDescription(true)
    try {
      await api.purchaseOrders.update(poId, { work_description: workDescription.trim() || null })
      setIsEditingWorkDescription(false)
      fetchPO()
      onUpdate?.()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to update work description")
    } finally {
      setSavingWorkDescription(false)
    }
  }

  const handleSaveVendorPoNumber = async () => {
    setSavingVendorPo(true)
    try {
      await api.purchaseOrders.update(poId, { vendor_po_number: vendorPoNumber.trim() || null })
      setIsEditingVendorPo(false)
      fetchPO()
      onUpdate?.()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to update vendor PO number")
    } finally {
      setSavingVendorPo(false)
    }
  }

  const handleSaveExpectedDeliveryDate = async () => {
    setSavingDeliveryDate(true)
    try {
      await api.purchaseOrders.update(poId, { expected_delivery_date: expectedDeliveryDate ? `${expectedDeliveryDate}T00:00:00` : null })
      setIsEditingDeliveryDate(false)
      fetchPO()
      onUpdate?.()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to update expected delivery date")
    } finally {
      setSavingDeliveryDate(false)
    }
  }

  const handleCostCodeChange = async (value: string) => {
    const costCodeId = parseInt(value)
    if (isNaN(costCodeId)) return
    setSavingCostCode(true)
    try {
      await api.purchaseOrders.update(poId, { cost_code_id: costCodeId })
      fetchPO()
      onUpdate?.()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to update cost code")
    } finally {
      setSavingCostCode(false)
    }
  }

  // ===== Status & Clone Handlers =====

  // Allowed status transitions: Draft->Sent, Sent->Received/Closed, Received->Closed
  const allowedTransitions: Record<POStatus, POStatus[]> = {
    Draft: ["Sent"],
    Sent: ["Received", "Closed"],
    Received: ["Closed"],
    Closed: [],
  }

  const getNextStatuses = (): POStatus[] => {
    if (!po) return []
    return allowedTransitions[po.status] ?? []
  }

  const handleStatusChange = async (newStatus: POStatus) => {
    if (!po) return
    if (editorMode === "edit" || hasStagedChanges) return
    const allowed = allowedTransitions[po.status] ?? []
    if (!allowed.includes(newStatus)) return
    try {
      await api.purchaseOrders.update(poId, { status: newStatus })
      fetchPO()
      onUpdate?.()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to update status")
    }
  }

  const handleClonePO = async () => {
    setIsCloning(true)
    setCloneConfirmOpen(false)
    try {
      const clonedPO = await api.purchaseOrders.clone(poId)
      onUpdate?.()
      if (onSelectPO) {
        onSelectPO(clonedPO.id)
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to clone purchase order")
    } finally {
      setIsCloning(false)
    }
  }

  // ===== Print PO Handler =====

  const handlePrintPO = async () => {
    if (!po) return
    setIsPrinting(true)
    try {
      const [project, companySettings, { pdf }, { PurchaseOrderPDF }] = await Promise.all([
        api.projects.get(po.project_id) as Promise<Project>,
        api.companySettings.get(),
        import('@react-pdf/renderer'),
        import('@/components/pdf/PurchaseOrderPDF'),
      ])
      const blob = await pdf(
        <PurchaseOrderPDF po={po} project={project} companySettings={companySettings} />
      ).toBlob()
      const url = URL.createObjectURL(blob)
      window.open(url, '_blank')
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to generate PDF")
    } finally {
      setIsPrinting(false)
    }
  }

  // ===== Receiving Mode Handlers =====

  const enterReceivingMode = () => {
    if (!canReceive) return
    setStagedReceivings(new Map())
    setReceivedDate(new Date().toISOString().split('T')[0])
    setReceivingDialogOpen(true)
    setEditorMode("receiving")
  }

  const exitReceivingMode = () => {
    setStagedReceivings(new Map())
    setReceivedDate(new Date().toISOString().split('T')[0])
    setReceivingDialogOpen(false)
    setEditorMode("view")
  }

  const updateStagedReceiving = (lineItemId: number, qty: number, actualPrice?: number) => {
    const newMap = new Map(stagedReceivings)
    if (qty <= 0 && actualPrice === undefined) {
      newMap.delete(lineItemId)
    } else {
      const existing = newMap.get(lineItemId)
      newMap.set(lineItemId, {
        qty_received: Math.max(0, qty),
        actual_unit_price: actualPrice ?? existing?.actual_unit_price,
      })
    }
    setStagedReceivings(newMap)
  }

  /** Submit staged receiving quantities and actual prices to the backend. */
  const handleSubmitReceiving = async () => {
    if (!po) return

    // Build the line items for the receiving
    const receivingLineItems: POReceivingLineItemCreate[] = []
    for (const [lineItemId, staged] of stagedReceivings) {
      if (staged.qty_received > 0) {
        receivingLineItems.push({
          po_line_item_id: lineItemId,
          qty_received: staged.qty_received,
          actual_unit_price: staged.actual_unit_price,
        })
      }
    }

    if (receivingLineItems.length === 0) return

    setIsSubmittingReceiving(true)
    try {
      const payload: POReceivingCreate = {
        received_date: `${receivedDate}T00:00:00`,
        line_items: receivingLineItems,
      }
      await api.purchaseOrders.createReceiving(poId, payload)

      // Exit receiving mode and refresh
      exitReceivingMode()
      await fetchPO()
      onUpdate?.()

      // Check if all items are now received - auto-transition to "Received"
      const freshPO = await api.purchaseOrders.get(poId)
      const allReceived = freshPO.line_items.every(item => item.qty_pending === 0)
      if (allReceived && freshPO.status === "Sent") {
        await api.purchaseOrders.update(poId, { status: "Received" })
        fetchPO()
        onUpdate?.()
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to submit receiving")
    } finally {
      setIsSubmittingReceiving(false)
    }
  }

  // ===== Calculation Functions =====

  const getLineItemDescription = (item: POLineItem): string => {
    if (item.item_type === "part" && item.part) {
      return `${item.part.part_number} - ${item.part.description}`
    }
    return getDisplayDescription(item) || item.description || "Miscellaneous"
  }

  const getLineItemTotal = (item: POLineItem): number => {
    return getDisplayUnitPrice(item) * getDisplayQuantity(item)
  }

  const getLineItemVariance = (item: POLineItem): number | null => {
    if (item.qty_received > 0 && item.actual_unit_price != null) {
      return (item.actual_unit_price - getDisplayUnitPrice(item)) * item.qty_received
    }
    return null
  }

  const calculateTotalOrdered = (): number => {
    if (!po) return 0
    let total = po.line_items
      .filter(item => !stagedDeletes.has(item.id))
      .reduce((sum, item) => sum + getLineItemTotal(item), 0)
    // Add staged adds
    total += stagedAdds.reduce((sum, item) => sum + (item.unit_price ?? 0) * item.quantity, 0)
    return total
  }

  const calculateTotalActual = (): number => {
    if (!po) return 0
    let total = po.line_items
      .filter(item => !stagedDeletes.has(item.id))
      .reduce((sum, item) => {
        const receivedCost = item.qty_received * (item.actual_unit_price ?? getDisplayUnitPrice(item))
        const pendingCost = getDisplayPending(item) * getDisplayUnitPrice(item)
        return sum + receivedCost + pendingCost
      }, 0)
    // Include staged adds (all pending, none received)
    total += stagedAdds.reduce((sum, item) => sum + (item.unit_price ?? 0) * item.quantity, 0)
    return total
  }

  const calculateTotalVariance = (): number => {
    return calculateTotalActual() - calculateTotalOrdered()
  }

  const calculateVariancePercentage = (): number => {
    const ordered = calculateTotalOrdered()
    if (ordered === 0) return 0
    return (calculateTotalVariance() / ordered) * 100
  }

  const getSectionItems = (type: POLineItemType): POLineItem[] => {
    if (!po) return []
    return po.line_items.filter(item => item.item_type === type)
  }

  const getSectionStagedAdds = (type: POLineItemType): StagedPOAdd[] => {
    return stagedAdds.filter(item => item.item_type === type)
  }

  // Get the display pending value: for items with no receipts, use staged quantity if available
  const getDisplayPending = (item: POLineItem): number => {
    if (editorMode === "edit" && item.qty_received === 0) {
      // For Draft POs with no receipts, pending = staged quantity (since pending = ordered - received)
      return getDisplayQuantity(item)
    }
    return item.qty_pending
  }

  const getSectionProgress = (items: POLineItem[], sectionAdds: StagedPOAdd[] = []): { received: number; pending: number; total: number } => {
    const activeItems = items.filter(item => !stagedDeletes.has(item.id))
    let total = activeItems.reduce((sum, item) => sum + getDisplayQuantity(item), 0)
    const received = activeItems.reduce((sum, item) => sum + item.qty_received, 0)
    let pending = activeItems.reduce((sum, item) => sum + getDisplayPending(item), 0)
    // Include staged adds: all quantity is pending (none received yet)
    const addsTotal = sectionAdds.reduce((sum, item) => sum + item.quantity, 0)
    total += addsTotal
    pending += addsTotal
    return { received, pending, total }
  }

  // ===== Utility Functions =====

  const getTypeIcon = (type: POLineItemType) => {
    switch (type) {
      case "part":
        return <Package className="h-4 w-4" />
      case "misc":
        return <FileText className="h-4 w-4" />
    }
  }

  const getTypeBadgeVariant = (type: POLineItemType) => {
    switch (type) {
      case "part":
        return "secondary"
      case "misc":
        return "outline"
    }
  }

  const getStatusBadgeColor = (status: POStatus): string => {
    switch (status) {
      case "Draft":
        return "bg-yellow-100 dark:bg-yellow-900 text-yellow-800 dark:text-yellow-200 border-yellow-300 dark:border-yellow-700"
      case "Sent":
        return "bg-blue-100 dark:bg-blue-900 text-blue-800 dark:text-blue-200 border-blue-300 dark:border-blue-700"
      case "Received":
        return "bg-green-100 dark:bg-green-900 text-green-800 dark:text-green-200 border-green-300 dark:border-green-700"
      case "Closed":
        return "bg-gray-100 dark:bg-gray-800 text-gray-800 dark:text-gray-200 border-gray-300 dark:border-gray-700"
    }
  }

  const formatCurrency = (value: number): string => {
    return `$${value.toFixed(2)}`
  }

  const formatVariance = (variance: number): string => {
    if (variance === 0) return "$0.00"
    const sign = variance > 0 ? "+" : ""
    return `${sign}$${variance.toFixed(2)}`
  }

  const getVarianceColor = (variance: number): string => {
    if (variance < 0) return "text-green-600 dark:text-green-400"
    if (variance > 0) return "text-red-600 dark:text-red-400"
    return "text-muted-foreground"
  }

  // ===== Stacked Progress Component =====

  const StackedProgress = ({ items, sectionAdds = [] }: { items: POLineItem[]; sectionAdds?: StagedPOAdd[] }) => {
    const { received, total } = getSectionProgress(items, sectionAdds)

    if (total === 0) return null

    const receivedPercent = (received / total) * 100
    const pendingPercent = ((total - received) / total) * 100

    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <div className="h-2 w-32 bg-muted rounded-full overflow-hidden flex">
              {/* Received portion - solid green */}
              <div
                className="h-full bg-green-600 dark:bg-green-500"
                style={{ width: `${receivedPercent}%` }}
              />
              {/* Pending portion - gray */}
              <div
                className="h-full bg-gray-300 dark:bg-gray-600"
                style={{ width: `${pendingPercent}%` }}
              />
            </div>
          </TooltipTrigger>
          <TooltipContent>
            <p>{received} of {total} received</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    )
  }

  // ===== Section Rendering =====

  const renderLineItemSection = (
    title: string,
    items: POLineItem[],
    type: POLineItemType,
    icon: React.ReactNode,
    addButtonLabel: string
  ) => {
    const sectionAdds = getSectionStagedAdds(type)
    const hasItems = items.length > 0 || sectionAdds.length > 0

    return (
      <Card>
        <CardHeader className="pb-3">
          <div className="flex justify-between items-center">
            <div className="flex items-center gap-4">
              <CardTitle className="text-base flex items-center gap-2">
                {icon}
                {title}
              </CardTitle>
              {(items.length > 0 || sectionAdds.length > 0) && <StackedProgress items={items} sectionAdds={sectionAdds} />}
            </div>
            <div className="flex gap-2">
              {editorMode === "edit" && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => openAddDialog(type)}
                  className="gap-2"
                >
                  <Plus className="h-4 w-4" />
                  {addButtonLabel}
                </Button>
              )}
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {!hasItems ? (
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
                  <TableHead className="text-right">Qty Received</TableHead>
                  <TableHead className="text-right">Unit Price</TableHead>
                  <TableHead className="text-right">Actual Price</TableHead>
                  <TableHead className="text-right">Variance</TableHead>
                  <TableHead className="text-right">Total</TableHead>
                  {editorMode === "edit" && (
                    <TableHead className="text-right">Actions</TableHead>
                  )}
                </TableRow>
              </TableHeader>
              <TableBody>
                {/* Existing items */}
                {items.map((item) => {
                  const isDeleted = stagedDeletes.has(item.id)
                  const isEdited = stagedEdits.has(item.id)
                  const editedItem = stagedEdits.get(item.id)
                  const variance = getLineItemVariance(item)

                  return (
                    <TableRow
                      key={item.id}
                      className={`
                        ${isEdited && !isDeleted ? "border-l-4 border-l-blue-500 dark:border-l-blue-400" : ""}
                        ${isDeleted ? "border-l-4 border-l-red-500 dark:border-l-red-400 bg-red-50/50 dark:bg-red-950/50 line-through opacity-60" : ""}
                      `}
                    >
                      {/* Description */}
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <span className="font-medium">{getLineItemDescription(item)}</span>
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
                        </div>
                      </TableCell>

                      {/* Qty Ordered */}
                      <TableCell className="text-right">
                        {editedItem?.quantity !== undefined && editedItem.quantity !== item.quantity ? (
                          <span className="font-bold text-blue-600 dark:text-blue-400">{editedItem.quantity}</span>
                        ) : (
                          item.quantity
                        )}
                      </TableCell>

                      {/* Qty Pending */}
                      <TableCell className="text-right">
                        {(() => {
                          const displayPending = getDisplayPending(item)
                          return displayPending !== item.qty_pending ? (
                            <span className="font-bold text-blue-600 dark:text-blue-400">{displayPending}</span>
                          ) : (
                            item.qty_pending
                          )
                        })()}
                      </TableCell>

                      {/* Qty Received */}
                      <TableCell className="text-right">
                        {item.qty_received}
                      </TableCell>

                      {/* Unit Price */}
                      <TableCell className="text-right">
                        {editedItem?.unit_price !== undefined && editedItem.unit_price !== (item.unit_price ?? 0) ? (
                          <span className="font-bold text-blue-600 dark:text-blue-400">
                            {formatCurrency(editedItem.unit_price)}
                          </span>
                        ) : (
                          formatCurrency(item.unit_price ?? 0)
                        )}
                      </TableCell>

                      {/* Actual Price */}
                      <TableCell className="text-right">
                        {item.actual_unit_price != null ? formatCurrency(item.actual_unit_price) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </TableCell>

                      {/* Variance */}
                      <TableCell className="text-right">
                        {variance !== null ? (
                          <span className={getVarianceColor(variance)}>
                            {formatVariance(variance)}
                          </span>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </TableCell>

                      {/* Total */}
                      <TableCell className="text-right font-medium">
                        {formatCurrency(getLineItemTotal(item))}
                      </TableCell>

                      {/* Actions */}
                      {editorMode === "edit" && (
                        <TableCell className="text-right">
                          <div className="flex items-center justify-end gap-1">
                            {isDeleted ? (
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => unstageDelete(item.id)}
                                className="text-blue-600 hover:text-blue-700 hover:bg-blue-50"
                                title="Undo delete"
                              >
                                Undo
                              </Button>
                            ) : (
                              <>
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  onClick={() => openEditDialog(item)}
                                  title="Edit line item"
                                >
                                  <Pencil className="h-4 w-4" />
                                </Button>
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  onClick={() => handleDeleteLine(item.id)}
                                  className="text-destructive hover:text-destructive hover:bg-destructive/10"
                                  title="Delete line item"
                                >
                                  <Trash2 className="h-4 w-4" />
                                </Button>
                              </>
                            )}
                          </div>
                        </TableCell>
                      )}
                    </TableRow>
                  )
                })}

                {/* Staged add items */}
                {sectionAdds.map((item) => (
                  <TableRow
                    key={`staged-${item.tempId}`}
                    className="border-l-4 border-l-green-500 dark:border-l-green-400 bg-green-50/30 dark:bg-green-950/30"
                  >
                    {/* Description */}
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <span className="font-medium">
                          {item.item_type === "part" && item.part
                            ? `${item.part.part_number} - ${item.part.description}`
                            : item.description || "Miscellaneous"
                          }
                        </span>
                        <Badge variant="outline" className="text-xs bg-green-100 dark:bg-green-900 text-green-700 dark:text-green-300 border-green-300">
                          New
                        </Badge>
                      </div>
                    </TableCell>

                    {/* Qty Ordered */}
                    <TableCell className="text-right font-bold text-green-600 dark:text-green-400">
                      {item.quantity}
                    </TableCell>

                    {/* Qty Pending */}
                    <TableCell className="text-right font-bold text-green-600 dark:text-green-400">{item.quantity}</TableCell>

                    {/* Qty Received */}
                    <TableCell className="text-right text-muted-foreground">0</TableCell>

                    {/* Unit Price */}
                    <TableCell className="text-right font-bold text-green-600 dark:text-green-400">
                      {formatCurrency(item.unit_price ?? 0)}
                    </TableCell>

                    {/* Actual Price */}
                    <TableCell className="text-right text-muted-foreground">—</TableCell>

                    {/* Variance */}
                    <TableCell className="text-right text-muted-foreground">—</TableCell>

                    {/* Total */}
                    <TableCell className="text-right font-medium text-green-600 dark:text-green-400">
                      {formatCurrency((item.unit_price ?? 0) * item.quantity)}
                    </TableCell>

                    {/* Actions */}
                    {editorMode === "edit" && (
                      <TableCell className="text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => unstageAdd(item.tempId)}
                          className="text-destructive hover:text-destructive hover:bg-destructive/10"
                          title="Remove staged item"
                        >
                          <X className="h-4 w-4" />
                        </Button>
                      </TableCell>
                    )}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    )
  }

  // ===== Loading & Error States =====

  if (loading) {
    return <div className="p-8 text-center text-muted-foreground">Loading...</div>
  }

  if (error || !po) {
    return (
      <div className="p-8">
        <div className="p-4 bg-destructive/10 border border-destructive/20 rounded-md text-destructive">
          {error || "Purchase order not found"}
        </div>
      </div>
    )
  }

  // ===== Gather section data =====
  const partItems = getSectionItems("part")
  const miscItems = getSectionItems("misc")

  // ===== Main Render =====
  return (
    <div className={`p-6 space-y-6 pb-24 rounded-lg transition-colors ${
      editorMode === "edit"
        ? "border-2 border-blue-500 dark:border-blue-400 bg-blue-50/30 dark:bg-blue-950/30"
        : editorMode === "receiving"
        ? "border-2 border-green-500 dark:border-green-400 bg-green-50/30 dark:bg-green-950/30"
        : "border-2 border-transparent"
    }`}>

      {/* Received Banner */}
      {hasBeenReceived && (
        <Card className="border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950">
          <CardContent className="py-3">
            <div className="flex items-center gap-2 text-amber-700 dark:text-amber-300">
              <Check className="h-4 w-4" />
              <span className="font-medium">Partially Received</span>
              <span className="text-sm">— Some items on this PO have been received. Line item edits are restricted.</span>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Header */}
      <div className="flex justify-between items-start">
        <div>
          <h2 className="text-xl font-semibold">{po.po_number}</h2>
          <div className="flex items-center gap-2 mt-1 text-sm text-muted-foreground">
            <Building className="h-4 w-4" />
            <span>Vendor: {po.vendor.name}</span>
          </div>
          <p className="text-sm text-muted-foreground">
            Created: {formatDate(po.created_at)}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {getNextStatuses().length > 0 && editorMode === "view" && !hasStagedChanges ? (
            <Select value={po.status} onValueChange={(v) => handleStatusChange(v as POStatus)}>
              <SelectTrigger className={`w-32 ${getStatusBadgeColor(po.status)}`}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={po.status}>{po.status}</SelectItem>
                {getNextStatuses().map(status => (
                  <SelectItem key={status} value={status}>{status}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <StatusBadge status={po.status} className="px-3 py-1" />
          )}
          {po.legacy_imported && (
            <Badge
              variant="outline"
              className="bg-slate-500/10 text-slate-700 border-slate-500/30 dark:text-slate-300"
              title="This PO was brought in from the original UC Vision CSV migration."
            >
              Imported
            </Badge>
          )}
        </div>
      </div>

      {/* Metadata Section: Order Details */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Order Details</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            {/* Work Description */}
            <div className="space-y-1">
              <Label className="text-sm text-muted-foreground">Work Description</Label>
              {isEditingWorkDescription ? (
                <div className="flex flex-col gap-2">
                  <Textarea
                    value={workDescription}
                    onChange={(e) => setWorkDescription(e.target.value)}
                    placeholder="Enter work description"
                    rows={3}
                  />
                  <div className="flex gap-2">
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
                        setWorkDescription(po.work_description || "")
                        setIsEditingWorkDescription(false)
                      }}
                    >
                      Cancel
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="flex items-center gap-2">
                  {po.work_description ? (
                    <span className="text-sm">{po.work_description}</span>
                  ) : (
                    <span className="text-sm text-muted-foreground" title="Click pencil to set">—</span>
                  )}
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 w-6 p-0"
                    onClick={() => setIsEditingWorkDescription(true)}
                  >
                    <Pencil className="h-3 w-3" />
                  </Button>
                </div>
              )}
            </div>

            {/* Vendor PO Number */}
            <div className="space-y-1">
              <Label className="text-sm text-muted-foreground">Vendor PO Number</Label>
              {isEditingVendorPo ? (
                <div className="flex items-center gap-2">
                  <Input
                    value={vendorPoNumber}
                    onChange={(e) => setVendorPoNumber(e.target.value)}
                    placeholder="Enter vendor PO number"
                    className="max-w-xs"
                  />
                  <Button
                    size="sm"
                    onClick={handleSaveVendorPoNumber}
                    disabled={savingVendorPo}
                  >
                    {savingVendorPo ? "Saving..." : "Save"}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      setVendorPoNumber(po.vendor_po_number || "")
                      setIsEditingVendorPo(false)
                    }}
                  >
                    Cancel
                  </Button>
                </div>
              ) : (
                <div className="flex items-center gap-2">
                  {po.vendor_po_number ? (
                    <span className="text-sm font-medium">{po.vendor_po_number}</span>
                  ) : (
                    <span className="text-sm text-muted-foreground" title="Click pencil to set">—</span>
                  )}
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 w-6 p-0"
                    onClick={() => setIsEditingVendorPo(true)}
                  >
                    <Pencil className="h-3 w-3" />
                  </Button>
                </div>
              )}
            </div>

            {/* Expected Delivery Date */}
            <div className="space-y-1">
              <Label className="text-sm text-muted-foreground flex items-center gap-1">
                <Calendar className="h-3 w-3" />
                Expected Delivery Date
              </Label>
              {isEditingDeliveryDate ? (
                <div className="flex items-center gap-2">
                  <Input
                    type="date"
                    value={expectedDeliveryDate}
                    onChange={(e) => setExpectedDeliveryDate(e.target.value)}
                    className="max-w-xs"
                  />
                  <Popover>
                    <PopoverTrigger asChild>
                      <Button variant="outline" size="sm" className="h-9 w-9 p-0">
                        <Calendar className="h-4 w-4" />
                      </Button>
                    </PopoverTrigger>
                    <PopoverContent className="w-auto p-0" align="start">
                      <CalendarWidget
                        mode="single"
                        selected={expectedDeliveryDate ? new Date(expectedDeliveryDate + "T00:00:00") : undefined}
                        onSelect={(date) => {
                          if (date) {
                            const yyyy = date.getFullYear()
                            const mm = String(date.getMonth() + 1).padStart(2, "0")
                            const dd = String(date.getDate()).padStart(2, "0")
                            setExpectedDeliveryDate(`${yyyy}-${mm}-${dd}`)
                          }
                        }}
                      />
                    </PopoverContent>
                  </Popover>
                  <Button
                    size="sm"
                    onClick={handleSaveExpectedDeliveryDate}
                    disabled={savingDeliveryDate}
                  >
                    {savingDeliveryDate ? "Saving..." : "Save"}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      setExpectedDeliveryDate(po.expected_delivery_date || "")
                      setIsEditingDeliveryDate(false)
                    }}
                  >
                    Cancel
                  </Button>
                </div>
              ) : (
                <div className="flex items-center gap-2">
                  {po.expected_delivery_date ? (
                    <span className="text-sm font-medium">
                      {formatDate(po.expected_delivery_date)}
                    </span>
                  ) : (
                    <span className="text-sm text-muted-foreground" title="Click pencil to set">—</span>
                  )}
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 w-6 p-0"
                    onClick={() => setIsEditingDeliveryDate(true)}
                  >
                    <Pencil className="h-3 w-3" />
                  </Button>
                </div>
              )}
            </div>

            {/* Cost Code */}
            <div className="space-y-1">
              <Label className="text-sm text-muted-foreground flex items-center gap-1">
                <Hash className="h-3 w-3" />
                Cost Code
              </Label>
              <SearchableSelect
                options={costCodes.map(cc => ({ value: cc.id.toString(), label: `${cc.code} - ${cc.description}` }))}
                value={po.cost_code_id?.toString()}
                onChange={handleCostCodeChange}
                placeholder="Select cost code..."
                searchPlaceholder="Search cost codes..."
                disabled={savingCostCode}
              />
              {savingCostCode && (
                <p className="text-xs text-muted-foreground">Saving...</p>
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Parts Section */}
      {renderLineItemSection("Parts", partItems, "part", <Package className="h-4 w-4" />, "Add Part")}

      {/* Misc Section */}
      {renderLineItemSection("Miscellaneous", miscItems, "misc", <FileText className="h-4 w-4" />, "Add Misc")}

      {/* Summary Section */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Cost Summary</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            <div className="flex justify-between">
              <span className="text-muted-foreground">Total Ordered:</span>
              <span className="font-medium">{formatCurrency(calculateTotalOrdered())}</span>
            </div>
            {po.line_items.some(item => item.qty_received > 0) && (
              <>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Total Actual:</span>
                  <span className="font-medium">{formatCurrency(calculateTotalActual())}</span>
                </div>
                <Separator />
                <div className="flex justify-between items-center">
                  <span className="font-semibold">Total Variance:</span>
                  <span className={`font-bold ${getVarianceColor(calculateTotalVariance())}`}>
                    {formatVariance(calculateTotalVariance())}
                    <span className="text-sm ml-2">({calculateVariancePercentage().toFixed(1)}%)</span>
                  </span>
                </div>
              </>
            )}
            {!po.line_items.some(item => item.qty_received > 0) && (
              <>
                <Separator />
                {companySettings && companySettings.hst_rate > 0 && (
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">HST ({companySettings.hst_rate}%):</span>
                    <span className="font-medium">{formatCurrency(calculateTotalOrdered() * companySettings.hst_rate / 100)}</span>
                  </div>
                )}
                <div className="flex justify-between items-center">
                  <span className="font-semibold text-lg">Total:</span>
                  <span className="text-2xl font-bold">
                    {formatCurrency(calculateTotalOrdered() * (1 + (companySettings?.hst_rate ?? 0) / 100))}
                  </span>
                </div>
              </>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Audit Trail */}
      {editorMode !== "edit" && (
        <POAuditTrail
          purchaseOrderId={poId}
          currentVersion={po.current_version}
          onRevert={() => {
            fetchPO()
            onUpdate?.()
          }}
        />
      )}

      {/* ===== Floating Action Buttons ===== */}
      <div className="fixed bottom-6 right-6 z-50">
        <div className="flex flex-col items-end gap-2">
          {/* Secondary actions (Print / Clone) always reachable in view mode */}
          {editorMode === "view" && (
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={handlePrintPO}
                disabled={isPrinting}
                className="shadow-md gap-2 bg-background"
              >
                {isPrinting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Printer className="h-4 w-4" />}
                {isPrinting ? "Generating..." : "Print"}
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setCloneConfirmOpen(true)}
                disabled={isCloning}
                className="shadow-md gap-2 bg-background"
              >
                <Copy className="h-4 w-4" />
                {isCloning ? "Cloning..." : "Clone"}
              </Button>
            </div>
          )}
          <div className="flex gap-2">
            {/* View Mode: Enter Edit Mode button (only for Draft POs that haven't been received) */}
            {editorMode === "view" && po.status === "Draft" && !hasBeenReceived && (
              <Button
                size="lg"
                variant="outline"
                onClick={enterEditMode}
                className="shadow-lg gap-2"
              >
                <Pencil className="h-5 w-5" />
                Edit PO
              </Button>
            )}

            {/* View Mode: Receive Items button (for Sent/Received POs with pending items) */}
            {editorMode === "view" && canReceive && (
              <Button
                size="lg"
                onClick={enterReceivingMode}
                className="shadow-lg gap-2 bg-green-600 hover:bg-green-700 text-white"
              >
                <Package className="h-5 w-5" />
                Receive Items
              </Button>
            )}

            {/* Receiving Mode: Cancel and Submit buttons */}
            {editorMode === "receiving" && (
              <>
                <Button
                  size="lg"
                  variant="outline"
                  onClick={exitReceivingMode}
                  className="shadow-lg gap-2"
                >
                  <X className="h-5 w-5" />
                  Cancel
                </Button>
              </>
            )}

            {/* Edit Mode: Discard, Preview, and Commit buttons */}
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
                  variant="secondary"
                  onClick={() => setEditPreviewOpen(true)}
                  disabled={!hasStagedChanges}
                  className="shadow-lg gap-2"
                >
                  <Eye className="h-5 w-5" />
                  Preview
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
          </div>
        </div>
      </div>

      {/* ===== Dialogs ===== */}

      {/* Add Line Item Dialog */}
      <Dialog open={addDialogOpen} onOpenChange={setAddDialogOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              {getTypeIcon(addDialogType)}
              Add {addDialogType.charAt(0).toUpperCase() + addDialogType.slice(1)}
            </DialogTitle>
            <DialogDescription>
              Add a {addDialogType} line item to this purchase order.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 pt-4">
            {addDialogType === "part" && (
              <div className="space-y-2">
                <Label>Part</Label>
                <SearchableSelect<Part>
                  options={parts.map((part): SearchableSelectOption => ({
                    value: part.id.toString(),
                    label: `${part.part_number} - ${part.description}`,
                    description: `$${(part.cost ?? 0).toFixed(2)}`,
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
              <>
                <div className="space-y-2">
                  <Label>Description</Label>
                  <Input
                    value={miscDescription}
                    onChange={(e) => setMiscDescription(e.target.value)}
                    placeholder="e.g., Shipping fee"
                  />
                </div>
                <div className="space-y-2">
                  <Label>Unit Price</Label>
                  <Input
                    type="number"
                    step="0.01"
                    value={unitPrice}
                    onChange={(e) => setUnitPrice(e.target.value)}
                    placeholder="0.00"
                  />
                </div>
              </>
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
                (addDialogType === "part" && (!selectedPartId || parts.length === 0)) ||
                (addDialogType === "misc" && !miscDescription)
              }
            >
              <Plus className="h-4 w-4 mr-2" />
              Add Line Item
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Edit Line Item Dialog */}
      <Dialog open={editDialogOpen} onOpenChange={setEditDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Pencil className="h-4 w-4" />
              Edit Line Item
            </DialogTitle>
            <DialogDescription>
              Modify the line item details. Changes are staged until you commit.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 pt-4">
            {editingLineItem && (
              <>
                {/* Show item description (read-only for parts) */}
                <div className="p-3 bg-muted/50 rounded-md">
                  <span className="text-sm font-medium">
                    {editingLineItem.item_type === "part" && editingLineItem.part
                      ? `${editingLineItem.part.part_number} - ${editingLineItem.part.description}`
                      : editingLineItem.description || "Miscellaneous"
                    }
                  </span>
                </div>

                {/* Description (editable for misc items) */}
                {editingLineItem.item_type === "misc" && (
                  <div className="space-y-2">
                    <Label>Description</Label>
                    <Input
                      value={editDescription}
                      onChange={(e) => setEditDescription(e.target.value)}
                      placeholder="Enter description"
                    />
                  </div>
                )}

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
                  <Input
                    type="number"
                    step="0.01"
                    value={editUnitPrice}
                    onChange={(e) => setEditUnitPrice(e.target.value)}
                    placeholder="0.00"
                  />
                </div>
              </>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditDialogOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleEditLineItem}>
              Save Changes
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

      {/* Commit Changes Confirmation Dialog */}
      <AlertDialog open={commitConfirmOpen} onOpenChange={setCommitConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <GitCommit className="h-5 w-5 text-blue-500" />
              Commit Changes?
            </AlertDialogTitle>
            <AlertDialogDescription>
              You are about to commit {stagedChangesCount} change{stagedChangesCount !== 1 ? "s" : ""} to this purchase order:
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
                        {item.item_type === "part" && item.part
                          ? `${item.part.part_number} - ${item.part.description}`
                          : item.description || "Miscellaneous"
                        }
                      </span>
                      <span className="text-muted-foreground ml-2">× {item.quantity}</span>
                      <span className="text-muted-foreground ml-2">@ {formatCurrency(item.unit_price ?? 0)}</span>
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
                        {edit.originalItem.part?.part_number
                          ? `${edit.originalItem.part.part_number} - ${edit.originalItem.part.description}`
                          : edit.originalItem.description || "Item"
                        }
                      </span>
                      <div className="text-sm text-muted-foreground mt-1">
                        {edit.quantity !== undefined && (
                          <div>Qty: {edit.originalItem.quantity} → {edit.quantity}</div>
                        )}
                        {edit.unit_price !== undefined && (
                          <div>Price: {formatCurrency(edit.originalItem.unit_price ?? 0)} → {formatCurrency(edit.unit_price)}</div>
                        )}
                        {edit.description !== undefined && (
                          <div>Description: "{edit.originalItem.description}" → "{edit.description}"</div>
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
                    const item = po?.line_items.find(li => li.id === id)
                    return item ? (
                      <div key={id} className="p-2 bg-red-50 dark:bg-red-950 rounded border border-red-200 dark:border-red-800 line-through opacity-60">
                        <span className="font-medium">
                          {item.part?.part_number
                            ? `${item.part.part_number} - ${item.part.description}`
                            : item.description || "Item"
                          }
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

      {/* PO Changed Warning Dialog */}
      <AlertDialog open={poChangedDialogOpen} onOpenChange={setPOChangedDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-amber-500" />
              Purchase Order Data Changed
            </AlertDialogTitle>
            <AlertDialogDescription>
              This purchase order has been modified since you started editing. Your staged changes have been cleared to ensure data accuracy. Please review the updated PO and re-apply your edits.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogAction onClick={() => setPOChangedDialogOpen(false)}>
              OK
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Clone Confirmation Dialog */}
      <AlertDialog open={cloneConfirmOpen} onOpenChange={setCloneConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Clone Purchase Order?</AlertDialogTitle>
            <AlertDialogDescription>
              This will create a new purchase order with the same vendor, line items, and metadata as {po.po_number}. The new PO will be in Draft status with no receiving history.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleClonePO}>
              Confirm Clone
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Receiving Dialog */}
      <Dialog open={receivingDialogOpen} onOpenChange={(open) => {
        if (!open) exitReceivingMode()
      }}>
        <DialogContent className="max-w-3xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Package className="h-5 w-5 text-green-600" />
              Receive Items
            </DialogTitle>
            <DialogDescription>
              Record vendor delivery and actual prices for {po.po_number}.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 pt-2">
            {/* Received Date */}
            <div className="space-y-2">
              <Label className="flex items-center gap-1">
                <Calendar className="h-3 w-3" />
                Received Date
              </Label>
              <Input
                type="date"
                value={receivedDate}
                onChange={(e) => setReceivedDate(e.target.value)}
                className="max-w-xs"
              />
            </div>

            <Separator />

            {/* Items Table */}
            {(() => {
              const pendingItems = po.line_items.filter(item => item.qty_pending > 0)
              if (pendingItems.length === 0) {
                return (
                  <div className="text-center py-8 text-muted-foreground">
                    <Check className="h-8 w-8 mx-auto mb-2 text-green-600" />
                    <p>All items have been received.</p>
                  </div>
                )
              }

              return (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Description</TableHead>
                      <TableHead className="text-center">Ordered</TableHead>
                      <TableHead className="text-center">Pending</TableHead>
                      <TableHead className="text-center">Receive Qty</TableHead>
                      <TableHead className="text-right">Unit Price</TableHead>
                      <TableHead className="text-right">Actual Price</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {pendingItems.map((item) => {
                      const staged = stagedReceivings.get(item.id)
                      const qtyReceiving = staged?.qty_received ?? 0
                      const isReceiving = qtyReceiving > 0

                      return (
                        <TableRow
                          key={item.id}
                          className={isReceiving ? "bg-green-50/50 dark:bg-green-950/30" : ""}
                        >
                          <TableCell>
                            <span className="font-medium">{getLineItemDescription(item)}</span>
                          </TableCell>
                          <TableCell className="text-center">
                            <Badge variant="outline">{item.quantity}</Badge>
                          </TableCell>
                          <TableCell className="text-center">
                            <Badge variant="secondary">{item.qty_pending}</Badge>
                          </TableCell>
                          <TableCell>
                            <div className="flex items-center justify-center gap-1">
                              <Button
                                variant="outline"
                                size="sm"
                                className="h-8 w-8 p-0"
                                disabled={qtyReceiving <= 0 || isSubmittingReceiving}
                                onClick={() => updateStagedReceiving(item.id, qtyReceiving - 1, staged?.actual_unit_price)}
                              >
                                <Minus className="h-3 w-3" />
                              </Button>
                              <Input
                                type="number"
                                min={0}
                                max={item.qty_pending}
                                value={qtyReceiving}
                                onChange={(e) => {
                                  const val = parseInt(e.target.value) || 0
                                  const clamped = Math.min(Math.max(0, val), item.qty_pending)
                                  updateStagedReceiving(item.id, clamped, staged?.actual_unit_price)
                                }}
                                disabled={isSubmittingReceiving}
                                className="h-8 w-16 text-center"
                              />
                              <Button
                                variant="outline"
                                size="sm"
                                className="h-8 w-8 p-0"
                                disabled={qtyReceiving >= item.qty_pending || isSubmittingReceiving}
                                onClick={() => updateStagedReceiving(item.id, qtyReceiving + 1, staged?.actual_unit_price)}
                              >
                                <Plus className="h-3 w-3" />
                              </Button>
                            </div>
                          </TableCell>
                          <TableCell className="text-right">
                            {formatCurrency(item.unit_price ?? 0)}
                          </TableCell>
                          <TableCell className="text-right">
                            <Input
                              type="number"
                              step="0.01"
                              placeholder={`${(item.unit_price ?? 0).toFixed(2)}`}
                              value={staged?.actual_unit_price ?? ""}
                              onChange={(e) => {
                                const val = e.target.value ? parseFloat(e.target.value) : undefined
                                updateStagedReceiving(item.id, qtyReceiving, val)
                              }}
                              disabled={isSubmittingReceiving}
                              className="h-8 w-28 text-right ml-auto"
                            />
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              )
            })()}
          </div>

          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={exitReceivingMode} disabled={isSubmittingReceiving}>
              Cancel
            </Button>
            <Button
              onClick={handleSubmitReceiving}
              disabled={stagedReceivingsCount === 0 || isSubmittingReceiving || !receivedDate}
              className="bg-green-600 hover:bg-green-700 text-white gap-2"
            >
              {isSubmittingReceiving ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Submitting...
                </>
              ) : (
                <>
                  <Check className="h-4 w-4" />
                  Confirm Receipt ({stagedReceivingsCount} item{stagedReceivingsCount !== 1 ? "s" : ""})
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
