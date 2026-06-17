import { useState, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { api } from "@/api/client"
import type { Miscellaneous, MiscellaneousCreate } from "@/types"

interface MiscFormProps {
  misc?: Miscellaneous // If provided, we're editing; otherwise creating
  onSuccess?: (item?: Miscellaneous) => void
  onCancel?: () => void
}

export function MiscForm({ misc, onSuccess, onCancel }: MiscFormProps) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isEditing = !!misc

  // Form state - use strings for number fields to allow empty while editing
  const [description, setDescription] = useState("")
  const [unitPrice, setUnitPrice] = useState("")
  // New misc items default to 50% markup; edit mode loads the item's real value below.
  const [markupPercent, setMarkupPercent] = useState(isEditing ? "" : "50")

  // Populate form when editing
  useEffect(() => {
    if (misc) {
      setDescription(misc.description)
      setUnitPrice(misc.unit_price.toString())
      setMarkupPercent(misc.markup_percent.toString())
    }
  }, [misc])

  // Calculate misc cost (use parsed values or 0 for display)
  const unitPriceNum = parseFloat(unitPrice) || 0
  const markupNum = parseFloat(markupPercent) || 0
  const miscCost = unitPriceNum * (1 + markupNum / 100)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError(null)

    const miscData: MiscellaneousCreate = {
      description,
      unit_price: parseFloat(unitPrice) || 0,
      markup_percent: parseFloat(markupPercent) || 0,
    }

    try {
      let result: Miscellaneous | undefined
      if (isEditing && misc) {
        result = await api.misc.update(misc.id, miscData)
      } else {
        result = await api.misc.create(miscData)
      }
      onSuccess?.(result)
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to ${isEditing ? 'update' : 'create'} miscellaneous item`)
    } finally {
      setLoading(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {error && (
        <div className="text-sm text-destructive bg-destructive/10 p-3 rounded-md border border-destructive/20">
          {error}
        </div>
      )}

      <div className="space-y-2">
        <Label htmlFor="description">Misc Description</Label>
        <Input
          id="description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="e.g., Equipment Rental"
          required
        />
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="unitPrice">Unit Price ($)</Label>
          <Input
            id="unitPrice"
            type="number"
            step="0.01"
            min="0"
            value={unitPrice}
            onChange={(e) => setUnitPrice(e.target.value)}
            placeholder="0.00"
            required
          />
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
      </div>

      {/* Cost Summary */}
      <div className="bg-muted/50 p-4 rounded-md space-y-2">
        <h4 className="font-medium text-sm">Cost Summary</h4>
        <div className="text-sm space-y-1">
          <div className="flex justify-between font-medium">
            <span>Misc Cost:</span>
            <span>${miscCost.toFixed(2)}</span>
          </div>
        </div>
      </div>

      <div className="flex justify-end gap-2 pt-4">
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={loading || !description || !unitPrice}>
          {loading ? (isEditing ? "Saving..." : "Creating...") : (isEditing ? "Save Changes" : "Create Misc")}
        </Button>
      </div>
    </form>
  )
}
