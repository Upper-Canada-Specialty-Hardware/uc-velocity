import { useState, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { SearchableMultiSelect } from "@/components/ui/searchable-multi-select"
import type { SearchableMultiSelectOption } from "@/components/ui/searchable-multi-select"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { api } from "@/api/client"
import type { Part, PartCreate, Labor, Profile } from "@/types"
import { LaborForm } from "./LaborForm"

interface PartFormProps {
  part?: Part // If provided, we're editing; otherwise creating
  onSuccess?: (item?: Part) => void
  onCancel?: () => void
}

export function PartForm({ part, onSuccess, onCancel }: PartFormProps) {
  const [laborItems, setLaborItems] = useState<Labor[]>([])
  const [vendors, setVendors] = useState<Profile[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isEditing = !!part

  // Form state - use strings for number fields to allow empty while editing
  const [partNumber, setPartNumber] = useState("")
  const [description, setDescription] = useState("")
  const [cost, setCost] = useState("")
  // New parts default to 50% markup; edit mode loads the part's real value below.
  const [markupPercent, setMarkupPercent] = useState(isEditing ? "" : "50")
  const [selectedLaborIds, setSelectedLaborIds] = useState<string[]>([])

  // New pricing flow fields
  const [vendorId, setVendorId] = useState<string>("")
  const [listPrice, setListPrice] = useState("")
  const [discountPercent, setDiscountPercent] = useState("")

  // Fetch available labor items and vendors on mount
  useEffect(() => {
    const fetchData = async () => {
      try {
        const [laborData, vendorData] = await Promise.all([
          api.labor.getAll(),
          api.profiles.getAll("vendor"),
        ])
        setLaborItems(laborData)
        setVendors(vendorData)
      } catch {
        setError("Failed to load form data")
      }
    }
    fetchData()
  }, [])

  // Populate form when editing
  useEffect(() => {
    if (part) {
      setPartNumber(part.part_number)
      setDescription(part.description)
      setCost(part.cost.toString())
      setMarkupPercent(part.markup_percent?.toString() || "0")
      if (part.labor_items) {
        setSelectedLaborIds(part.labor_items.map(l => l.id.toString()))
      }
      // New fields
      setVendorId(part.vendor_id?.toString() || "")
      setListPrice(part.list_price?.toString() || "")
      setDiscountPercent(part.discount_percent?.toString() || "")
    }
  }, [part])

  // Get selected vendor's default discount for display
  const selectedVendor = vendors.find(v => v.id.toString() === vendorId)
  const vendorDefaultDiscount = selectedVendor?.default_discount_percent ?? 0

  // Determine if cost should be auto-calculated
  const hasListPrice = listPrice !== "" && parseFloat(listPrice) > 0
  const hasVendor = vendorId !== ""
  const costIsAutoCalculated = hasListPrice && hasVendor

  // Auto-calculate cost when list_price + vendor are set
  useEffect(() => {
    if (costIsAutoCalculated) {
      const lp = parseFloat(listPrice) || 0
      const dp = discountPercent !== "" ? parseFloat(discountPercent) : vendorDefaultDiscount
      const effectiveDiscount = dp || 0
      const calculatedCost = lp * (1 - effectiveDiscount / 100)
      setCost(calculatedCost.toFixed(2))
    }
  }, [listPrice, discountPercent, vendorId, vendorDefaultDiscount, costIsAutoCalculated])

  // Convert labor items to multi-select options
  const laborOptions: SearchableMultiSelectOption[] = laborItems.map((labor) => ({
    value: labor.id.toString(),
    label: labor.description,
    description: `${labor.hours}hrs @ $${labor.rate.toFixed(2)}/hr`,
  }))

  // Calculate total linked labor cost
  const linkedLaborCost = selectedLaborIds.reduce((total, id) => {
    const labor = laborItems.find((l) => l.id === parseInt(id, 10))
    if (labor) {
      return total + (labor.hours * labor.rate * (1 + labor.markup_percent / 100))
    }
    return total
  }, 0)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError(null)

    const partData: PartCreate = {
      part_number: partNumber,
      description,
      cost: parseFloat(cost) || 0,
      markup_percent: parseFloat(markupPercent) || 0,
      linked_labor_ids: selectedLaborIds.map((id) => parseInt(id, 10)),
      vendor_id: vendorId ? parseInt(vendorId, 10) : undefined,
      list_price: listPrice ? parseFloat(listPrice) : undefined,
      discount_percent: discountPercent ? parseFloat(discountPercent) : undefined,
    }

    try {
      let result: Part | undefined
      if (isEditing && part) {
        result = await api.parts.update(part.id, partData)
      } else {
        result = await api.parts.create(partData)
      }
      onSuccess?.(result)
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to ${isEditing ? 'update' : 'create'} part`)
    } finally {
      setLoading(false)
    }
  }

  const partCost = parseFloat(cost) || 0
  const markupNum = parseFloat(markupPercent) || 0
  const partPriceWithMarkup = partCost * (1 + markupNum / 100)

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {error && (
        <div className="text-sm text-destructive bg-destructive/10 p-3 rounded-md border border-destructive/20">
          {error}
        </div>
      )}

      <div className="space-y-2">
        <Label htmlFor="partNumber">Part Number</Label>
        <Input
          id="partNumber"
          value={partNumber}
          onChange={(e) => setPartNumber(e.target.value)}
          placeholder="e.g., HVAC-001"
          required
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="description">Description</Label>
        <Input
          id="description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="e.g., AC Unit 2-Ton"
          required
        />
      </div>

      {/* Vendor Selection */}
      <div className="space-y-2">
        <Label htmlFor="vendor">Vendor</Label>
        <Select value={vendorId} onValueChange={(v) => setVendorId(v === "none" ? "" : v)}>
          <SelectTrigger>
            <SelectValue placeholder="Select vendor (optional)" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="none">No vendor</SelectItem>
            {vendors.map(v => (
              <SelectItem key={v.id} value={v.id.toString()}>
                {v.name}
                {v.default_discount_percent ? ` (${v.default_discount_percent}% disc.)` : ''}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Vendor List Price (visible when vendor selected) */}
      {hasVendor && (
        <div className="space-y-2">
          <Label htmlFor="listPrice">Vendor List Price ($)</Label>
          <Input
            id="listPrice"
            type="number"
            step="0.01"
            min="0"
            value={listPrice}
            onChange={(e) => setListPrice(e.target.value)}
            placeholder="0.00"
          />
        </div>
      )}

      {/* Discount % (visible when vendor selected) */}
      {hasVendor && (
        <div className="space-y-2">
          <Label htmlFor="discountPercent">
            Discount (%)
            {vendorDefaultDiscount > 0 && discountPercent === "" && (
              <span className="text-muted-foreground font-normal ml-1">
                — using vendor default: {vendorDefaultDiscount}%
              </span>
            )}
          </Label>
          <Input
            id="discountPercent"
            type="number"
            step="0.01"
            min="0"
            max="100"
            value={discountPercent}
            onChange={(e) => setDiscountPercent(e.target.value)}
            placeholder={vendorDefaultDiscount > 0 ? `${vendorDefaultDiscount} (vendor default)` : "0"}
          />
          <p className="text-xs text-muted-foreground">
            Per-part override. Leave blank to use vendor default.
          </p>
        </div>
      )}

      {/* Cost field — read-only when auto-calculated */}
      <div className="space-y-2">
        <Label htmlFor="cost">
          {costIsAutoCalculated ? "Calculated Cost ($)" : "Manufacturer Cost ($)"}
        </Label>
        <Input
          id="cost"
          type="number"
          step="0.01"
          min="0"
          value={cost}
          onChange={(e) => setCost(e.target.value)}
          placeholder="0.00"
          required
          readOnly={costIsAutoCalculated}
          className={costIsAutoCalculated ? "bg-muted" : ""}
        />
        {costIsAutoCalculated && (
          <p className="text-xs text-muted-foreground">
            Auto-calculated: List Price x (1 - Discount%)
          </p>
        )}
      </div>

      <div className="space-y-2">
        <Label htmlFor="markup">Markup (%)</Label>
        <Input
          id="markup"
          type="number"
          step="0.01"
          min="0"
          value={markupPercent}
          onChange={(e) => setMarkupPercent(e.target.value)}
          placeholder="0"
        />
      </div>

      <div className="space-y-2">
        <Label>Linked Labour</Label>
        <SearchableMultiSelect<Labor>
          options={laborOptions}
          selected={selectedLaborIds}
          onChange={setSelectedLaborIds}
          placeholder="Select labour tasks for this part..."
          searchPlaceholder="Search labour items..."
          emptyMessage="No labour items found."
          allowCreate={true}
          createLabel="Create New Labour"
          createDialogTitle="Create New Labour Item"
          createForm={<LaborForm />}
          onCreateSuccess={(newLabor) => {
            setLaborItems([...laborItems, newLabor])
            setSelectedLaborIds([...selectedLaborIds, newLabor.id.toString()])
          }}
        />
        <p className="text-sm text-muted-foreground">
          Select labour tasks that are typically performed with this part. When adding this part to a quote, the system will prompt to auto-add these labour items.
        </p>
      </div>

      {/* Cost Summary */}
      <div className="bg-muted/50 p-4 rounded-md space-y-2">
        <h4 className="font-medium text-sm">Pricing Summary</h4>
        <div className="text-sm space-y-1">
          {costIsAutoCalculated && (
            <>
              <div className="flex justify-between">
                <span>Vendor List Price:</span>
                <span>${(parseFloat(listPrice) || 0).toFixed(2)}</span>
              </div>
              <div className="flex justify-between">
                <span>
                  Discount ({discountPercent !== "" ? discountPercent : vendorDefaultDiscount}%):
                </span>
                <span>
                  -${((parseFloat(listPrice) || 0) * ((discountPercent !== "" ? parseFloat(discountPercent) : vendorDefaultDiscount) || 0) / 100).toFixed(2)}
                </span>
              </div>
            </>
          )}
          <div className="flex justify-between">
            <span>Cost:</span>
            <span>${partCost.toFixed(2)}</span>
          </div>
          {markupNum > 0 && (
            <div className="flex justify-between font-medium border-t pt-1">
              <span>Sell Price ({markupNum}% markup):</span>
              <span>${partPriceWithMarkup.toFixed(2)}</span>
            </div>
          )}
          {selectedLaborIds.length > 0 && (
            <>
              <div className="flex justify-between">
                <span>Linked Labour Cost:</span>
                <span>${linkedLaborCost.toFixed(2)}</span>
              </div>
              <div className="flex justify-between font-medium border-t pt-1">
                <span>Total (with labour):</span>
                <span>${(partPriceWithMarkup + linkedLaborCost).toFixed(2)}</span>
              </div>
            </>
          )}
        </div>
      </div>

      <div className="flex justify-end gap-2 pt-4">
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={loading || !partNumber || !description || !cost}>
          {loading ? (isEditing ? "Saving..." : "Creating...") : (isEditing ? "Save Changes" : "Create Part")}
        </Button>
      </div>
    </form>
  )
}
