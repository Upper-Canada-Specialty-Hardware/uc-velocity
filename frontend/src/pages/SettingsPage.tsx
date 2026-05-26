import { useState, useEffect, useRef } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { api } from '@/api/client'
import type { CompanySettings, CompanySettingsUpdate, CostCode, CostCodeCreate, SystemRate, SystemRateCreate, SystemRateUpdate } from '@/types'
import { Loader2, Save, Pencil, Trash2, Plus, X, Check, Upload } from 'lucide-react'

// Inline editing row state
interface CostCodeEditState {
  code: string
  description: string
}

export function SettingsPage() {
  const [settings, setSettings] = useState<CompanySettings | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)

  // Form state
  const [name, setName] = useState('')
  const [address, setAddress] = useState('')
  const [phone, setPhone] = useState('')
  const [fax, setFax] = useState('')
  const [gstNumber, setGstNumber] = useState('')
  const [hstRate, setHstRate] = useState('')

  // Cost codes state
  const [costCodes, setCostCodes] = useState<CostCode[]>([])
  const [costCodesLoading, setCostCodesLoading] = useState(true)
  const [editingCostCodeId, setEditingCostCodeId] = useState<number | null>(null)
  const [editingCostCode, setEditingCostCode] = useState<CostCodeEditState | null>(null)
  const [addingCostCode, setAddingCostCode] = useState(false)
  const [newCostCode, setNewCostCode] = useState<CostCodeEditState>({ code: '', description: '' })
  const [costCodeSaving, setCostCodeSaving] = useState(false)
  const [costCodeError, setCostCodeError] = useState<string | null>(null)
  const [deleteConfirm, setDeleteConfirm] = useState<CostCode | null>(null)

  // Parking rate state
  const [parkingRate, setParkingRate] = useState<SystemRate | null>(null)
  const [editingParking, setEditingParking] = useState(false)
  const [parkingForm, setParkingForm] = useState({ description: '', unit_price: '', markup_percent: '' })
  const [parkingSaving, setParkingSaving] = useState(false)

  // Travel distance state
  const [travelTiers, setTravelTiers] = useState<SystemRate[]>([])
  const [travelLoading, setTravelLoading] = useState(true)
  const [editingTravelId, setEditingTravelId] = useState<number | null>(null)
  const [editingTravel, setEditingTravel] = useState<{ description: string; unit_price: string; markup_percent: string; sort_order: string } | null>(null)
  const [addingTravel, setAddingTravel] = useState(false)
  const [newTravel, setNewTravel] = useState({ description: '', unit_price: '', markup_percent: '', sort_order: '' })
  const [travelSaving, setTravelSaving] = useState(false)
  const [travelError, setTravelError] = useState<string | null>(null)
  const [deleteTravelConfirm, setDeleteTravelConfirm] = useState<SystemRate | null>(null)

  // PMS default state
  const [pmsDefault, setPmsDefault] = useState('')

  // Company logo state (base64 data URL)
  const [logoDataUrl, setLogoDataUrl] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    fetchSettings()
    fetchCostCodes()
    fetchParkingRate()
    fetchTravelTiers()
  }, [])

  const fetchSettings = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await api.companySettings.get()
      setSettings(data)
      setName(data.name || '')
      setAddress(data.address || '')
      setPhone(data.phone || '')
      setFax(data.fax || '')
      setGstNumber(data.gst_number || '')
      setHstRate(String(data.hst_rate ?? 13.0))
      setPmsDefault(data.default_pms_percent != null ? String(data.default_pms_percent) : '')
      setLogoDataUrl(data.logo_data_url ?? null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load settings')
    } finally {
      setLoading(false)
    }
  }

  const handleLogoChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return

    const MAX_BYTES = 2 * 1024 * 1024
    if (file.size > MAX_BYTES) {
      setError('Logo image must be 2MB or smaller.')
      e.target.value = ''
      return
    }

    const reader = new FileReader()
    reader.onload = () => {
      if (typeof reader.result === 'string') {
        setLogoDataUrl(reader.result)
        setError(null)
      }
    }
    reader.onerror = () => {
      setError('Failed to read logo image.')
    }
    reader.readAsDataURL(file)
    e.target.value = ''
  }

  const handleRemoveLogo = () => {
    setLogoDataUrl(null)
  }

  const fetchCostCodes = async () => {
    setCostCodesLoading(true)
    try {
      const data = await api.costCodes.getAll()
      setCostCodes(data)
    } catch (err) {
      setCostCodeError(err instanceof Error ? err.message : 'Failed to load cost codes')
    } finally {
      setCostCodesLoading(false)
    }
  }

  // Parking rate handlers
  const fetchParkingRate = async () => {
    try {
      const data = await api.systemRates.getParking()
      setParkingRate(data)
    } catch (_) {
      // Parking rate may not exist yet
    }
  }

  const startEditParking = () => {
    if (!parkingRate) return
    setParkingForm({
      description: parkingRate.description,
      unit_price: String(parkingRate.unit_price),
      markup_percent: String(parkingRate.markup_percent),
    })
    setEditingParking(true)
  }

  const saveParking = async () => {
    const unit_price = parseFloat(parkingForm.unit_price)
    const markup_percent = parseFloat(parkingForm.markup_percent)
    if (isNaN(unit_price) || unit_price < 0) { setError('Parking base cost must be a non-negative number.'); return }
    if (isNaN(markup_percent) || markup_percent < 0) { setError('Parking markup must be a non-negative number.'); return }

    setParkingSaving(true)
    setError(null)
    try {
      const updated = await api.systemRates.updateParking({
        description: parkingForm.description.trim(),
        unit_price,
        markup_percent,
      })
      setParkingRate(updated)
      setEditingParking(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update parking rate')
    } finally {
      setParkingSaving(false)
    }
  }

  // Travel distance handlers
  const fetchTravelTiers = async () => {
    setTravelLoading(true)
    try {
      const data = await api.systemRates.getTravelDistance()
      setTravelTiers(data)
    } catch (err) {
      setTravelError(err instanceof Error ? err.message : 'Failed to load travel tiers')
    } finally {
      setTravelLoading(false)
    }
  }

  const startEditTravel = (tier: SystemRate) => {
    setEditingTravelId(tier.id)
    setEditingTravel({
      description: tier.description,
      unit_price: String(tier.unit_price),
      markup_percent: String(tier.markup_percent),
      sort_order: String(tier.sort_order),
    })
    setTravelError(null)
  }

  const cancelEditTravel = () => {
    setEditingTravelId(null)
    setEditingTravel(null)
  }

  const saveEditTravel = async () => {
    if (!editingTravel || editingTravelId === null) return
    const unit_price = parseFloat(editingTravel.unit_price)
    const markup_percent = parseFloat(editingTravel.markup_percent)
    const sort_order = parseInt(editingTravel.sort_order)
    if (!editingTravel.description.trim()) { setTravelError('Description is required.'); return }
    if (isNaN(unit_price) || unit_price < 0) { setTravelError('Base cost must be a non-negative number.'); return }
    if (isNaN(markup_percent) || markup_percent < 0) { setTravelError('Markup must be a non-negative number.'); return }

    setTravelSaving(true)
    setTravelError(null)
    try {
      await api.systemRates.updateTravelDistance(editingTravelId, {
        description: editingTravel.description.trim(),
        unit_price,
        markup_percent,
        sort_order: isNaN(sort_order) ? undefined : sort_order,
      })
      setEditingTravelId(null)
      setEditingTravel(null)
      fetchTravelTiers()
    } catch (err) {
      setTravelError(err instanceof Error ? err.message : 'Failed to update travel tier')
    } finally {
      setTravelSaving(false)
    }
  }

  const startAddTravel = () => {
    const nextSort = travelTiers.length > 0 ? Math.max(...travelTiers.map(t => t.sort_order)) + 1 : 1
    setAddingTravel(true)
    setNewTravel({ description: '', unit_price: '', markup_percent: '', sort_order: String(nextSort) })
    setTravelError(null)
  }

  const cancelAddTravel = () => {
    setAddingTravel(false)
    setNewTravel({ description: '', unit_price: '', markup_percent: '', sort_order: '' })
  }

  const saveNewTravel = async () => {
    const unit_price = parseFloat(newTravel.unit_price)
    const markup_percent = parseFloat(newTravel.markup_percent || '0')
    const sort_order = parseInt(newTravel.sort_order || '0')
    if (!newTravel.description.trim()) { setTravelError('Description is required.'); return }
    if (isNaN(unit_price) || unit_price < 0) { setTravelError('Base cost must be a non-negative number.'); return }

    setTravelSaving(true)
    setTravelError(null)
    try {
      await api.systemRates.createTravelDistance({
        description: newTravel.description.trim(),
        unit_price,
        markup_percent: isNaN(markup_percent) ? 0 : markup_percent,
        sort_order: isNaN(sort_order) ? 0 : sort_order,
      })
      setAddingTravel(false)
      setNewTravel({ description: '', unit_price: '', markup_percent: '', sort_order: '' })
      fetchTravelTiers()
    } catch (err) {
      setTravelError(err instanceof Error ? err.message : 'Failed to create travel tier')
    } finally {
      setTravelSaving(false)
    }
  }

  const deleteTravel = async (tier: SystemRate) => {
    setTravelError(null)
    try {
      await api.systemRates.deleteTravelDistance(tier.id)
      setDeleteTravelConfirm(null)
      fetchTravelTiers()
    } catch (err) {
      setDeleteTravelConfirm(null)
      setTravelError(err instanceof Error ? err.message : 'Failed to delete travel tier')
    }
  }

  const handleSave = async () => {
    setError(null)
    setSuccessMessage(null)

    const hstValue = parseFloat(hstRate)
    if (isNaN(hstValue) || hstValue < 0 || hstValue > 100) {
      setError('HST Rate must be a number between 0 and 100.')
      return
    }

    const pmsValue = pmsDefault.trim() === '' ? null : parseFloat(pmsDefault)
    if (pmsValue !== null && (isNaN(pmsValue) || pmsValue < 0 || pmsValue > 100)) {
      setError('Default PMS Percentage must be a number between 0 and 100.')
      return
    }

    setSaving(true)
    try {
      const update: CompanySettingsUpdate = {
        name,
        address,
        phone,
        fax,
        gst_number: gstNumber,
        hst_rate: hstValue,
        default_pms_percent: pmsValue,
        logo_data_url: logoDataUrl,
      }
      const data = await api.companySettings.update(update)
      setSettings(data)
      setSuccessMessage('Settings saved successfully.')
      setTimeout(() => setSuccessMessage(null), 3000)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save settings')
    } finally {
      setSaving(false)
    }
  }

  // Cost code CRUD handlers
  const startEditCostCode = (cc: CostCode) => {
    setEditingCostCodeId(cc.id)
    setEditingCostCode({
      code: cc.code,
      description: cc.description,
    })
    setCostCodeError(null)
  }

  const cancelEditCostCode = () => {
    setEditingCostCodeId(null)
    setEditingCostCode(null)
  }

  const saveEditCostCode = async () => {
    if (!editingCostCode || editingCostCodeId === null) return
    if (!editingCostCode.code.trim() || !editingCostCode.description.trim()) {
      setCostCodeError('Code and Description are required.')
      return
    }

    setCostCodeSaving(true)
    setCostCodeError(null)
    try {
      await api.costCodes.update(editingCostCodeId, {
        code: editingCostCode.code.trim(),
        description: editingCostCode.description.trim(),
      })
      setEditingCostCodeId(null)
      setEditingCostCode(null)
      fetchCostCodes()
    } catch (err) {
      setCostCodeError(err instanceof Error ? err.message : 'Failed to update cost code')
    } finally {
      setCostCodeSaving(false)
    }
  }

  const startAddCostCode = () => {
    setAddingCostCode(true)
    setNewCostCode({ code: '', description: '' })
    setCostCodeError(null)
  }

  const cancelAddCostCode = () => {
    setAddingCostCode(false)
    setNewCostCode({ code: '', description: '' })
  }

  const saveNewCostCode = async () => {
    if (!newCostCode.code.trim() || !newCostCode.description.trim()) {
      setCostCodeError('Code and Description are required.')
      return
    }

    setCostCodeSaving(true)
    setCostCodeError(null)
    try {
      const data: CostCodeCreate = {
        code: newCostCode.code.trim(),
        description: newCostCode.description.trim(),
      }
      await api.costCodes.create(data)
      setAddingCostCode(false)
      setNewCostCode({ code: '', description: '' })
      fetchCostCodes()
    } catch (err) {
      setCostCodeError(err instanceof Error ? err.message : 'Failed to create cost code')
    } finally {
      setCostCodeSaving(false)
    }
  }

  const deleteCostCode = async (cc: CostCode) => {
    setCostCodeError(null)
    try {
      await api.costCodes.delete(cc.id)
      setDeleteConfirm(null)
      fetchCostCodes()
    } catch (err) {
      setDeleteConfirm(null)
      setCostCodeError(err instanceof Error ? err.message : 'Failed to delete cost code')
    }
  }

  if (loading) {
    return (
      <div className="p-8 text-center text-muted-foreground">
        <Loader2 className="h-6 w-6 animate-spin mx-auto mb-2" />
        Loading settings...
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Settings</h1>
        <p className="text-muted-foreground">Manage company information and business variables</p>
      </div>

      {error && (
        <div className="p-3 bg-destructive/10 border border-destructive/20 rounded-md text-destructive text-sm">
          {error}
        </div>
      )}

      {successMessage && (
        <div className="p-3 bg-green-50 dark:bg-green-950 border border-green-200 dark:border-green-800 rounded-md text-green-700 dark:text-green-300 text-sm">
          {successMessage}
        </div>
      )}

      {/* Company Information */}
      <Card>
        <CardHeader>
          <CardTitle>Company Information</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="company-name">Company Name</Label>
              <Input
                id="company-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="gst-number">GST Number</Label>
              <Input
                id="gst-number"
                value={gstNumber}
                onChange={(e) => setGstNumber(e.target.value)}
              />
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="address">Address</Label>
            <Input
              id="address"
              value={address}
              onChange={(e) => setAddress(e.target.value)}
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="phone">Phone</Label>
              <Input
                id="phone"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="fax">Fax</Label>
              <Input
                id="fax"
                value={fax}
                onChange={(e) => setFax(e.target.value)}
              />
            </div>
          </div>

          <div className="space-y-2">
            <Label>Company Logo</Label>
            <div className="flex items-start gap-4">
              <div className="flex h-20 w-20 shrink-0 items-center justify-center rounded-md border bg-muted/30 overflow-hidden">
                {logoDataUrl ? (
                  <img src={logoDataUrl} alt="Company logo" className="max-h-full max-w-full object-contain" />
                ) : (
                  <span className="text-xs text-muted-foreground">No logo</span>
                )}
              </div>
              <div className="space-y-2">
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  onChange={handleLogoChange}
                  className="hidden"
                />
                <div className="flex gap-2">
                  <Button type="button" variant="outline" size="sm" onClick={() => fileInputRef.current?.click()} className="gap-1">
                    <Upload className="h-3.5 w-3.5" />
                    {logoDataUrl ? 'Replace logo' : 'Upload logo'}
                  </Button>
                  {logoDataUrl && (
                    <Button type="button" variant="ghost" size="sm" onClick={handleRemoveLogo}>
                      Remove
                    </Button>
                  )}
                </div>
                <p className="text-xs text-muted-foreground">
                  PNG, JPEG, or WebP. Max 2MB. Appears on quote, invoice, and PO PDFs. Click "Save Settings" below to apply.
                </p>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Business Variables */}
      <Card>
        <CardHeader>
          <CardTitle>Business Variables</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="max-w-xs space-y-2">
              <Label htmlFor="hst-rate">HST Rate (Ontario Harmonized Sales Tax)</Label>
              <div className="relative">
                <Input
                  id="hst-rate"
                  type="number"
                  min="0"
                  max="100"
                  step="0.01"
                  value={hstRate}
                  onChange={(e) => setHstRate(e.target.value)}
                  className="pr-8"
                />
                <span className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground text-sm">%</span>
              </div>
              <p className="text-xs text-muted-foreground">
                Applied to quotes, invoices, and purchase orders. Ontario HST is 13%.
              </p>
            </div>
            <div className="max-w-xs space-y-2">
              <Label htmlFor="pms-default">Default PMS Percentage</Label>
              <div className="relative">
                <Input
                  id="pms-default"
                  type="number"
                  min="0"
                  max="100"
                  step="0.1"
                  value={pmsDefault}
                  onChange={(e) => setPmsDefault(e.target.value)}
                  className="pr-8"
                  placeholder="e.g. 10"
                />
                <span className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground text-sm">%</span>
              </div>
              <p className="text-xs text-muted-foreground">
                Pre-filled when adding PMS % items to quotes. Leave empty for no default.
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Parking Rate */}
      <Card>
        <CardHeader>
          <CardTitle>Parking Rate</CardTitle>
        </CardHeader>
        <CardContent>
          {parkingRate ? (
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Description</TableHead>
                    <TableHead className="w-[130px]">Base Cost ($)</TableHead>
                    <TableHead className="w-[130px]">Markup (%)</TableHead>
                    <TableHead className="w-[130px]">Total ($)</TableHead>
                    <TableHead className="w-[80px] text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  <TableRow>
                    {editingParking ? (
                      <>
                        <TableCell>
                          <Input
                            value={parkingForm.description}
                            onChange={(e) => setParkingForm({ ...parkingForm, description: e.target.value })}
                            className="h-8"
                          />
                        </TableCell>
                        <TableCell>
                          <Input
                            type="number"
                            min="0"
                            step="0.01"
                            value={parkingForm.unit_price}
                            onChange={(e) => setParkingForm({ ...parkingForm, unit_price: e.target.value })}
                            className="h-8"
                          />
                        </TableCell>
                        <TableCell>
                          <Input
                            type="number"
                            min="0"
                            step="0.01"
                            value={parkingForm.markup_percent}
                            onChange={(e) => setParkingForm({ ...parkingForm, markup_percent: e.target.value })}
                            className="h-8"
                          />
                        </TableCell>
                        <TableCell className="text-sm font-medium">
                          ${(parseFloat(parkingForm.unit_price || '0') * (1 + parseFloat(parkingForm.markup_percent || '0') / 100)).toFixed(2)}
                        </TableCell>
                        <TableCell className="text-right">
                          <div className="flex justify-end gap-1">
                            <Button size="icon" variant="ghost" className="h-7 w-7" onClick={saveParking} disabled={parkingSaving}>
                              {parkingSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4 text-green-600" />}
                            </Button>
                            <Button size="icon" variant="ghost" className="h-7 w-7" onClick={() => setEditingParking(false)}>
                              <X className="h-4 w-4 text-muted-foreground" />
                            </Button>
                          </div>
                        </TableCell>
                      </>
                    ) : (
                      <>
                        <TableCell className="font-medium">{parkingRate.description}</TableCell>
                        <TableCell>${parkingRate.unit_price.toFixed(2)}</TableCell>
                        <TableCell>{parkingRate.markup_percent.toFixed(1)}%</TableCell>
                        <TableCell className="font-medium">
                          ${(parkingRate.unit_price * (1 + parkingRate.markup_percent / 100)).toFixed(2)}
                        </TableCell>
                        <TableCell className="text-right">
                          <Button size="icon" variant="ghost" className="h-7 w-7" onClick={startEditParking}>
                            <Pencil className="h-3.5 w-3.5" />
                          </Button>
                        </TableCell>
                      </>
                    )}
                  </TableRow>
                </TableBody>
              </Table>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">Parking rate not configured. It will be created on next deployment.</p>
          )}
        </CardContent>
      </Card>

      {/* Travel Distance Tiers */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>Travel Distance Tiers</CardTitle>
          <Button
            size="sm"
            onClick={startAddTravel}
            disabled={addingTravel || editingTravelId !== null}
            className="gap-1"
          >
            <Plus className="h-4 w-4" />
            Add New
          </Button>
        </CardHeader>
        <CardContent>
          {travelError && (
            <div className="p-3 mb-4 bg-destructive/10 border border-destructive/20 rounded-md text-destructive text-sm">
              {travelError}
            </div>
          )}

          {travelLoading ? (
            <div className="p-4 text-center text-muted-foreground">
              <Loader2 className="h-5 w-5 animate-spin mx-auto mb-2" />
              Loading travel tiers...
            </div>
          ) : (
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Description</TableHead>
                    <TableHead className="w-[120px]">Base Cost ($)</TableHead>
                    <TableHead className="w-[110px]">Markup (%)</TableHead>
                    <TableHead className="w-[120px]">Total ($)</TableHead>
                    <TableHead className="w-[80px]">Order</TableHead>
                    <TableHead className="w-[100px] text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {travelTiers.map((tier) => (
                    <TableRow key={tier.id}>
                      {editingTravelId === tier.id && editingTravel ? (
                        <>
                          <TableCell>
                            <Input
                              value={editingTravel.description}
                              onChange={(e) => setEditingTravel({ ...editingTravel, description: e.target.value })}
                              className="h-8"
                            />
                          </TableCell>
                          <TableCell>
                            <Input
                              type="number" min="0" step="0.01"
                              value={editingTravel.unit_price}
                              onChange={(e) => setEditingTravel({ ...editingTravel, unit_price: e.target.value })}
                              className="h-8"
                            />
                          </TableCell>
                          <TableCell>
                            <Input
                              type="number" min="0" step="0.01"
                              value={editingTravel.markup_percent}
                              onChange={(e) => setEditingTravel({ ...editingTravel, markup_percent: e.target.value })}
                              className="h-8"
                            />
                          </TableCell>
                          <TableCell className="text-sm font-medium">
                            ${(parseFloat(editingTravel.unit_price || '0') * (1 + parseFloat(editingTravel.markup_percent || '0') / 100)).toFixed(2)}
                          </TableCell>
                          <TableCell>
                            <Input
                              type="number" min="0"
                              value={editingTravel.sort_order}
                              onChange={(e) => setEditingTravel({ ...editingTravel, sort_order: e.target.value })}
                              className="h-8 w-16"
                            />
                          </TableCell>
                          <TableCell className="text-right">
                            <div className="flex justify-end gap-1">
                              <Button size="icon" variant="ghost" className="h-7 w-7" onClick={saveEditTravel} disabled={travelSaving}>
                                {travelSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4 text-green-600" />}
                              </Button>
                              <Button size="icon" variant="ghost" className="h-7 w-7" onClick={cancelEditTravel}>
                                <X className="h-4 w-4 text-muted-foreground" />
                              </Button>
                            </div>
                          </TableCell>
                        </>
                      ) : (
                        <>
                          <TableCell className="font-medium">{tier.description}</TableCell>
                          <TableCell>${tier.unit_price.toFixed(2)}</TableCell>
                          <TableCell>{tier.markup_percent.toFixed(1)}%</TableCell>
                          <TableCell className="font-medium">
                            ${(tier.unit_price * (1 + tier.markup_percent / 100)).toFixed(2)}
                          </TableCell>
                          <TableCell className="text-sm text-muted-foreground">{tier.sort_order}</TableCell>
                          <TableCell className="text-right">
                            <div className="flex justify-end gap-1">
                              <Button
                                size="icon" variant="ghost" className="h-7 w-7"
                                onClick={() => startEditTravel(tier)}
                                disabled={addingTravel || editingTravelId !== null}
                              >
                                <Pencil className="h-3.5 w-3.5" />
                              </Button>
                              <Button
                                size="icon" variant="ghost"
                                className="h-7 w-7 text-destructive hover:text-destructive"
                                onClick={() => setDeleteTravelConfirm(tier)}
                                disabled={addingTravel || editingTravelId !== null}
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </Button>
                            </div>
                          </TableCell>
                        </>
                      )}
                    </TableRow>
                  ))}

                  {/* Add new row */}
                  {addingTravel && (
                    <TableRow>
                      <TableCell>
                        <Input
                          value={newTravel.description}
                          onChange={(e) => setNewTravel({ ...newTravel, description: e.target.value })}
                          className="h-8"
                          placeholder='e.g. "Travel Distance (300km) (1 Day)"'
                          autoFocus
                        />
                      </TableCell>
                      <TableCell>
                        <Input
                          type="number" min="0" step="0.01"
                          value={newTravel.unit_price}
                          onChange={(e) => setNewTravel({ ...newTravel, unit_price: e.target.value })}
                          className="h-8"
                          placeholder="0.00"
                        />
                      </TableCell>
                      <TableCell>
                        <Input
                          type="number" min="0" step="0.01"
                          value={newTravel.markup_percent}
                          onChange={(e) => setNewTravel({ ...newTravel, markup_percent: e.target.value })}
                          className="h-8"
                          placeholder="0.0"
                        />
                      </TableCell>
                      <TableCell className="text-sm font-medium">
                        ${(parseFloat(newTravel.unit_price || '0') * (1 + parseFloat(newTravel.markup_percent || '0') / 100)).toFixed(2)}
                      </TableCell>
                      <TableCell>
                        <Input
                          type="number" min="0"
                          value={newTravel.sort_order}
                          onChange={(e) => setNewTravel({ ...newTravel, sort_order: e.target.value })}
                          className="h-8 w-16"
                        />
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-1">
                          <Button size="icon" variant="ghost" className="h-7 w-7" onClick={saveNewTravel} disabled={travelSaving}>
                            {travelSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4 text-green-600" />}
                          </Button>
                          <Button size="icon" variant="ghost" className="h-7 w-7" onClick={cancelAddTravel}>
                            <X className="h-4 w-4 text-muted-foreground" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  )}

                  {travelTiers.length === 0 && !addingTravel && (
                    <TableRow>
                      <TableCell colSpan={6} className="text-center text-muted-foreground py-8">
                        No travel distance tiers found. Click "Add New" to create one.
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <Separator />

      <div className="flex justify-end">
        <Button onClick={handleSave} disabled={saving} className="gap-2">
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
          Save Settings
        </Button>
      </div>

      {/* Cost Codes */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>Cost Codes</CardTitle>
          <Button
            size="sm"
            onClick={startAddCostCode}
            disabled={addingCostCode || editingCostCodeId !== null}
            className="gap-1"
          >
            <Plus className="h-4 w-4" />
            Add New
          </Button>
        </CardHeader>
        <CardContent>
          {costCodeError && (
            <div className="p-3 mb-4 bg-destructive/10 border border-destructive/20 rounded-md text-destructive text-sm">
              {costCodeError}
            </div>
          )}

          {costCodesLoading ? (
            <div className="p-4 text-center text-muted-foreground">
              <Loader2 className="h-5 w-5 animate-spin mx-auto mb-2" />
              Loading cost codes...
            </div>
          ) : (
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[120px]">Code</TableHead>
                    <TableHead>Description</TableHead>
                    <TableHead className="w-[100px] text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {costCodes.map((cc) => (
                    <TableRow key={cc.id}>
                      {editingCostCodeId === cc.id && editingCostCode ? (
                        <>
                          <TableCell>
                            <Input
                              value={editingCostCode.code}
                              onChange={(e) => setEditingCostCode({ ...editingCostCode, code: e.target.value })}
                              className="h-8"
                            />
                          </TableCell>
                          <TableCell>
                            <Input
                              value={editingCostCode.description}
                              onChange={(e) => setEditingCostCode({ ...editingCostCode, description: e.target.value })}
                              className="h-8"
                            />
                          </TableCell>
                          <TableCell className="text-right">
                            <div className="flex justify-end gap-1">
                              <Button size="icon" variant="ghost" className="h-7 w-7" onClick={saveEditCostCode} disabled={costCodeSaving}>
                                {costCodeSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4 text-green-600" />}
                              </Button>
                              <Button size="icon" variant="ghost" className="h-7 w-7" onClick={cancelEditCostCode}>
                                <X className="h-4 w-4 text-muted-foreground" />
                              </Button>
                            </div>
                          </TableCell>
                        </>
                      ) : (
                        <>
                          <TableCell className="font-mono text-sm">{cc.code}</TableCell>
                          <TableCell>{cc.description}</TableCell>
                          <TableCell className="text-right">
                            <div className="flex justify-end gap-1">
                              <Button
                                size="icon"
                                variant="ghost"
                                className="h-7 w-7"
                                onClick={() => startEditCostCode(cc)}
                                disabled={addingCostCode || editingCostCodeId !== null}
                              >
                                <Pencil className="h-3.5 w-3.5" />
                              </Button>
                              <Button
                                size="icon"
                                variant="ghost"
                                className="h-7 w-7 text-destructive hover:text-destructive"
                                onClick={() => setDeleteConfirm(cc)}
                                disabled={addingCostCode || editingCostCodeId !== null}
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </Button>
                            </div>
                          </TableCell>
                        </>
                      )}
                    </TableRow>
                  ))}

                  {/* Add new row */}
                  {addingCostCode && (
                    <TableRow>
                      <TableCell>
                        <Input
                          value={newCostCode.code}
                          onChange={(e) => setNewCostCode({ ...newCostCode, code: e.target.value })}
                          className="h-8"
                          placeholder="e.g. 999-100"
                          autoFocus
                        />
                      </TableCell>
                      <TableCell>
                        <Input
                          value={newCostCode.description}
                          onChange={(e) => setNewCostCode({ ...newCostCode, description: e.target.value })}
                          className="h-8"
                          placeholder="Description"
                        />
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-1">
                          <Button size="icon" variant="ghost" className="h-7 w-7" onClick={saveNewCostCode} disabled={costCodeSaving}>
                            {costCodeSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4 text-green-600" />}
                          </Button>
                          <Button size="icon" variant="ghost" className="h-7 w-7" onClick={cancelAddCostCode}>
                            <X className="h-4 w-4 text-muted-foreground" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  )}

                  {costCodes.length === 0 && !addingCostCode && (
                    <TableRow>
                      <TableCell colSpan={3} className="text-center text-muted-foreground py-8">
                        No cost codes found. Click "Add New" to create one.
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Delete cost code confirmation dialog */}
      <AlertDialog open={!!deleteConfirm} onOpenChange={(open) => !open && setDeleteConfirm(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Cost Code</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete cost code <strong>{deleteConfirm?.code}</strong> ({deleteConfirm?.description})?
              This cannot be undone. If this cost code is used by any quotes or purchase orders, deletion will be blocked.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={() => deleteConfirm && deleteCostCode(deleteConfirm)}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Delete travel tier confirmation dialog */}
      <AlertDialog open={!!deleteTravelConfirm} onOpenChange={(open) => !open && setDeleteTravelConfirm(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Travel Distance Tier</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to remove <strong>{deleteTravelConfirm?.description}</strong>?
              Existing quotes using this tier will not be affected.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={() => deleteTravelConfirm && deleteTravel(deleteTravelConfirm)}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
