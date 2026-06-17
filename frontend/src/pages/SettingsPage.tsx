import { useState, useEffect, useRef } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
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
import type { CompanySettings, CompanySettingsUpdate, CostCode, SystemRate } from '@/types'
import { Loader2, Save, Trash2, Plus, Upload, Undo2, AlertTriangle } from 'lucide-react'

// Working-row shapes. Numeric fields are kept as strings while editing so inputs stay
// controlled; they're parsed and validated at save time. A negative id marks a row that
// doesn't exist on the server yet (a staged add).
interface TravelRow {
  id: number
  description: string
  unit_price: string
  markup_percent: string
  sort_order: string
}

interface CostCodeRow {
  id: number
  code: string
  description: string
}

interface ParkingForm {
  description: string
  unit_price: string
  markup_percent: string
}

interface SettingsPageProps {
  // Lets the App shell guard sidebar navigation when there are unsaved edits.
  onDirtyChange?: (dirty: boolean) => void
}

// One canonical object for the whole editable form. Serialized to snapshot the loaded
// baseline and, on every render, to detect whether anything has changed since.
interface WorkingState {
  company: {
    name: string
    address: string
    phone: string
    fax: string
    gstNumber: string
    hstRate: string
    pmsDefault: string
    logoDataUrl: string | null
  }
  parking: ParkingForm | null
  travel: TravelRow[]
  costCodes: CostCodeRow[]
}

const serialize = (w: WorkingState): string =>
  JSON.stringify({
    company: w.company,
    parking: w.parking,
    travel: [...w.travel].sort((a, b) => a.id - b.id),
    costCodes: [...w.costCodes].sort((a, b) => a.id - b.id),
  })

const travelFromServer = (t: SystemRate): TravelRow => ({
  id: t.id,
  description: t.description,
  unit_price: String(t.unit_price),
  markup_percent: String(t.markup_percent),
  sort_order: String(t.sort_order),
})

const costCodeFromServer = (c: CostCode): CostCodeRow => ({
  id: c.id,
  code: c.code,
  description: c.description,
})

const parkingFromServer = (p: SystemRate | null): ParkingForm | null =>
  p
    ? { description: p.description, unit_price: String(p.unit_price), markup_percent: String(p.markup_percent) }
    : null

export function SettingsPage({ onDirtyChange }: SettingsPageProps) {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)
  const [discardConfirmOpen, setDiscardConfirmOpen] = useState(false)

  // Company info + business variables (kept as discrete fields for simple input binding).
  const [name, setName] = useState('')
  const [address, setAddress] = useState('')
  const [phone, setPhone] = useState('')
  const [fax, setFax] = useState('')
  const [gstNumber, setGstNumber] = useState('')
  const [hstRate, setHstRate] = useState('')
  const [pmsDefault, setPmsDefault] = useState('')
  const [logoDataUrl, setLogoDataUrl] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  // Editable tables.
  const [parkingForm, setParkingForm] = useState<ParkingForm | null>(null)
  const [travelRows, setTravelRows] = useState<TravelRow[]>([])
  const [costCodeRows, setCostCodeRows] = useState<CostCodeRow[]>([])

  // Server-truth snapshots — used to diff on save and to revert on discard.
  const [originalCompany, setOriginalCompany] = useState<CompanySettings | null>(null)
  const [originalParking, setOriginalParking] = useState<SystemRate | null>(null)
  const [originalTravel, setOriginalTravel] = useState<SystemRate[]>([])
  const [originalCostCodes, setOriginalCostCodes] = useState<CostCode[]>([])

  // Canonical snapshot of the form as last loaded/saved; dirty = current !== baseline.
  const [baseline, setBaseline] = useState<string | null>(null)

  // Monotonic negative ids for staged-add rows.
  const nextTempId = useRef(-1)

  // Push a full set of server data into the working form and reset baseline + snapshots.
  const applyServerData = (
    company: CompanySettings,
    parking: SystemRate | null,
    travel: SystemRate[],
    costCodes: CostCode[]
  ) => {
    const companyForm = {
      name: company.name || '',
      address: company.address || '',
      phone: company.phone || '',
      fax: company.fax || '',
      gstNumber: company.gst_number || '',
      hstRate: String(company.hst_rate ?? 13.0),
      pmsDefault: company.default_pms_percent != null ? String(company.default_pms_percent) : '',
      logoDataUrl: company.logo_data_url ?? null,
    }
    const parkingF = parkingFromServer(parking)
    const travelF = travel.map(travelFromServer)
    const costCodesF = costCodes.map(costCodeFromServer)

    setName(companyForm.name)
    setAddress(companyForm.address)
    setPhone(companyForm.phone)
    setFax(companyForm.fax)
    setGstNumber(companyForm.gstNumber)
    setHstRate(companyForm.hstRate)
    setPmsDefault(companyForm.pmsDefault)
    setLogoDataUrl(companyForm.logoDataUrl)
    setParkingForm(parkingF)
    setTravelRows(travelF)
    setCostCodeRows(costCodesF)

    setOriginalCompany(company)
    setOriginalParking(parking)
    setOriginalTravel(travel)
    setOriginalCostCodes(costCodes)

    setBaseline(serialize({ company: companyForm, parking: parkingF, travel: travelF, costCodes: costCodesF }))
  }

  // silent=true skips the full-page loading state (used for the post-save re-sync so the
  // page doesn't blink back to a spinner — the Save button shows its own spinner).
  const loadAll = async (silent = false) => {
    if (!silent) setLoading(true)
    if (!silent) setError(null)
    try {
      const [company, travel, costCodes] = await Promise.all([
        api.companySettings.get(),
        api.systemRates.getTravelDistance(),
        api.costCodes.getAll(),
      ])
      // Parking is a singleton that may not exist yet; tolerate its absence.
      let parking: SystemRate | null = null
      try {
        parking = await api.systemRates.getParking()
      } catch {
        parking = null
      }
      applyServerData(company, parking, travel, costCodes)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load settings')
    } finally {
      if (!silent) setLoading(false)
    }
  }

  useEffect(() => {
    loadAll()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ----- Dirty detection -----
  const currentWorking: WorkingState = {
    company: { name, address, phone, fax, gstNumber, hstRate, pmsDefault, logoDataUrl },
    parking: parkingForm,
    travel: travelRows,
    costCodes: costCodeRows,
  }
  const isDirty = baseline !== null && serialize(currentWorking) !== baseline

  // Report dirty state up so the shell can guard sidebar navigation; clear on unmount.
  useEffect(() => {
    onDirtyChange?.(isDirty)
  }, [isDirty, onDirtyChange])
  useEffect(() => {
    return () => onDirtyChange?.(false)
  }, [onDirtyChange])

  // Warn on browser-level navigation (tab close / refresh) while there are unsaved edits.
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (isDirty) {
        e.preventDefault()
        e.returnValue = 'You have unsaved changes that will be lost. Are you sure you want to leave?'
        return e.returnValue
      }
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [isDirty])

  // ----- Logo handlers -----
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
    reader.onerror = () => setError('Failed to read logo image.')
    reader.readAsDataURL(file)
    e.target.value = ''
  }

  // ----- Row add / update / remove -----
  const addTravelRow = () => {
    const nextSort =
      travelRows.length > 0 ? Math.max(...travelRows.map((t) => parseInt(t.sort_order) || 0)) + 1 : 1
    setTravelRows([
      ...travelRows,
      { id: nextTempId.current--, description: '', unit_price: '', markup_percent: '', sort_order: String(nextSort) },
    ])
  }
  const updateTravelRow = (id: number, patch: Partial<TravelRow>) =>
    setTravelRows((rows) => rows.map((r) => (r.id === id ? { ...r, ...patch } : r)))
  const removeTravelRow = (id: number) => setTravelRows((rows) => rows.filter((r) => r.id !== id))

  const addCostCodeRow = () =>
    setCostCodeRows([...costCodeRows, { id: nextTempId.current--, code: '', description: '' }])
  const updateCostCodeRow = (id: number, patch: Partial<CostCodeRow>) =>
    setCostCodeRows((rows) => rows.map((r) => (r.id === id ? { ...r, ...patch } : r)))
  const removeCostCodeRow = (id: number) => setCostCodeRows((rows) => rows.filter((r) => r.id !== id))

  // ----- Discard: revert the whole form to the last-saved snapshot -----
  const handleDiscard = () => {
    if (!originalCompany) return
    applyServerData(originalCompany, originalParking, originalTravel, originalCostCodes)
    setDiscardConfirmOpen(false)
    setError(null)
  }

  // ----- Save: validate -> diff against snapshots -> fire all writes -> re-sync truth -----
  const handleSave = async () => {
    setError(null)
    setSuccessMessage(null)

    // Company / business variables
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

    // Parking
    if (parkingForm) {
      const pu = parseFloat(parkingForm.unit_price)
      const pm = parseFloat(parkingForm.markup_percent)
      if (isNaN(pu) || pu < 0) { setError('Parking base cost must be a non-negative number.'); return }
      if (isNaN(pm) || pm < 0) { setError('Parking markup must be a non-negative number.'); return }
    }

    // Travel — drop fully-blank new rows, then validate the rest
    const travelToSave = travelRows.filter(
      (r) => !(r.id < 0 && !r.description.trim() && !r.unit_price.trim() && !r.markup_percent.trim())
    )
    for (const r of travelToSave) {
      if (!r.description.trim()) { setError('Every travel tier needs a description.'); return }
      const u = parseFloat(r.unit_price)
      if (isNaN(u) || u < 0) { setError(`Travel tier "${r.description}": base cost must be a non-negative number.`); return }
      const m = r.markup_percent.trim() === '' ? 0 : parseFloat(r.markup_percent)
      if (isNaN(m) || m < 0) { setError(`Travel tier "${r.description}": markup must be a non-negative number.`); return }
    }

    // Cost codes — drop fully-blank new rows, then validate the rest
    const costCodesToSave = costCodeRows.filter((r) => !(r.id < 0 && !r.code.trim() && !r.description.trim()))
    for (const r of costCodesToSave) {
      if (!r.code.trim() || !r.description.trim()) {
        setError('Every cost code needs both a code and a description.')
        return
      }
    }

    setSaving(true)

    // Each write is wrapped so a single failure doesn't abort the others; we collect
    // labelled error strings and report them together.
    const run = (label: string, p: Promise<unknown>): Promise<string | null> =>
      p.then(() => null).catch((e) => `${label}: ${e instanceof Error ? e.message : 'failed'}`)
    const ops: Promise<string | null>[] = []

    // Company settings — one PUT if anything in the two cards changed
    const companyChanged =
      !originalCompany ||
      name !== (originalCompany.name || '') ||
      address !== (originalCompany.address || '') ||
      phone !== (originalCompany.phone || '') ||
      fax !== (originalCompany.fax || '') ||
      gstNumber !== (originalCompany.gst_number || '') ||
      hstValue !== originalCompany.hst_rate ||
      pmsValue !== (originalCompany.default_pms_percent ?? null) ||
      (logoDataUrl ?? null) !== (originalCompany.logo_data_url ?? null)
    if (companyChanged) {
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
      ops.push(run('Company settings', api.companySettings.update(update)))
    }

    // Parking — one PUT if changed
    if (parkingForm && originalParking) {
      const changed =
        parkingForm.description !== originalParking.description ||
        parseFloat(parkingForm.unit_price) !== originalParking.unit_price ||
        parseFloat(parkingForm.markup_percent) !== originalParking.markup_percent
      if (changed) {
        ops.push(
          run('Parking rate', api.systemRates.updateParking({
            description: parkingForm.description.trim(),
            unit_price: parseFloat(parkingForm.unit_price),
            markup_percent: parseFloat(parkingForm.markup_percent),
          }))
        )
      }
    }

    // Travel — deletes (snapshot rows no longer present), creates (negative ids), updates
    const travelKept = new Set(travelToSave.filter((r) => r.id > 0).map((r) => r.id))
    for (const o of originalTravel) {
      if (!travelKept.has(o.id)) {
        ops.push(run(`Delete travel tier "${o.description}"`, api.systemRates.deleteTravelDistance(o.id)))
      }
    }
    for (const r of travelToSave) {
      const payload = {
        description: r.description.trim(),
        unit_price: parseFloat(r.unit_price),
        markup_percent: r.markup_percent.trim() === '' ? 0 : parseFloat(r.markup_percent),
        sort_order: r.sort_order.trim() === '' ? undefined : parseInt(r.sort_order),
      }
      if (r.id < 0) {
        ops.push(run(`Create travel tier "${payload.description}"`, api.systemRates.createTravelDistance(payload)))
      } else {
        const o = originalTravel.find((t) => t.id === r.id)
        const changed =
          !o ||
          payload.description !== o.description ||
          payload.unit_price !== o.unit_price ||
          payload.markup_percent !== o.markup_percent ||
          (payload.sort_order ?? o.sort_order) !== o.sort_order
        if (changed) {
          ops.push(run(`Update travel tier "${payload.description}"`, api.systemRates.updateTravelDistance(r.id, payload)))
        }
      }
    }

    // Cost codes — same delete / create / update diff
    const ccKept = new Set(costCodesToSave.filter((r) => r.id > 0).map((r) => r.id))
    for (const o of originalCostCodes) {
      if (!ccKept.has(o.id)) {
        ops.push(run(`Delete cost code "${o.code}"`, api.costCodes.delete(o.id)))
      }
    }
    for (const r of costCodesToSave) {
      const payload = { code: r.code.trim(), description: r.description.trim() }
      if (r.id < 0) {
        ops.push(run(`Create cost code "${payload.code}"`, api.costCodes.create(payload)))
      } else {
        const o = originalCostCodes.find((c) => c.id === r.id)
        const changed = !o || payload.code !== o.code || payload.description !== o.description
        if (changed) {
          ops.push(run(`Update cost code "${payload.code}"`, api.costCodes.update(r.id, payload)))
        }
      }
    }

    try {
      const results = await Promise.all(ops)
      const errors = results.filter((r): r is string => r !== null)
      // Re-sync to server truth regardless of partial failures so the page never shows
      // phantom state: writes that succeeded persist, ones that failed revert in the UI.
      await loadAll(true)
      if (errors.length > 0) {
        setError(`Some changes could not be saved — ${errors.join('; ')}`)
      } else {
        setSuccessMessage('Settings saved successfully.')
        setTimeout(() => setSuccessMessage(null), 3000)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save settings')
    } finally {
      setSaving(false)
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
        <p className="text-muted-foreground">
          Edit company information and business variables, then save everything at once.
        </p>
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
              <Input id="company-name" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="gst-number">GST Number</Label>
              <Input id="gst-number" value={gstNumber} onChange={(e) => setGstNumber(e.target.value)} />
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="address">Address</Label>
            <Input id="address" value={address} onChange={(e) => setAddress(e.target.value)} />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="phone">Phone</Label>
              <Input id="phone" value={phone} onChange={(e) => setPhone(e.target.value)} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="fax">Fax</Label>
              <Input id="fax" value={fax} onChange={(e) => setFax(e.target.value)} />
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
                    <Button type="button" variant="ghost" size="sm" onClick={() => setLogoDataUrl(null)}>
                      Remove
                    </Button>
                  )}
                </div>
                <p className="text-xs text-muted-foreground">
                  PNG, JPEG, or WebP. Max 2MB. Appears on quote, invoice, and PO PDFs. Saved when you click Save Settings.
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
          {parkingForm ? (
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Description</TableHead>
                    <TableHead className="w-[130px]">Base Cost ($)</TableHead>
                    <TableHead className="w-[130px]">Markup (%)</TableHead>
                    <TableHead className="w-[130px]">Total ($)</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  <TableRow>
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
          <Button size="sm" onClick={addTravelRow} className="gap-1">
            <Plus className="h-4 w-4" />
            Add Tier
          </Button>
        </CardHeader>
        <CardContent>
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Description</TableHead>
                  <TableHead className="w-[120px]">Base Cost ($)</TableHead>
                  <TableHead className="w-[110px]">Markup (%)</TableHead>
                  <TableHead className="w-[120px]">Total ($)</TableHead>
                  <TableHead className="w-[80px]">Order</TableHead>
                  <TableHead className="w-[60px] text-right">
                    <span className="sr-only">Remove</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {travelRows.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell>
                      <Input
                        value={r.description}
                        onChange={(e) => updateTravelRow(r.id, { description: e.target.value })}
                        className="h-8"
                        placeholder="e.g. Zone 1 (0-25km)"
                      />
                    </TableCell>
                    <TableCell>
                      <Input
                        type="number"
                        min="0"
                        step="0.01"
                        value={r.unit_price}
                        onChange={(e) => updateTravelRow(r.id, { unit_price: e.target.value })}
                        className="h-8"
                      />
                    </TableCell>
                    <TableCell>
                      <Input
                        type="number"
                        min="0"
                        step="0.01"
                        value={r.markup_percent}
                        onChange={(e) => updateTravelRow(r.id, { markup_percent: e.target.value })}
                        className="h-8"
                      />
                    </TableCell>
                    <TableCell className="text-sm font-medium">
                      ${(parseFloat(r.unit_price || '0') * (1 + parseFloat(r.markup_percent || '0') / 100)).toFixed(2)}
                    </TableCell>
                    <TableCell>
                      <Input
                        type="number"
                        min="0"
                        value={r.sort_order}
                        onChange={(e) => updateTravelRow(r.id, { sort_order: e.target.value })}
                        className="h-8 w-16"
                      />
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        size="icon"
                        variant="ghost"
                        className="h-7 w-7 text-destructive hover:text-destructive"
                        onClick={() => removeTravelRow(r.id)}
                        aria-label={`Remove travel tier ${r.description || '(new)'}`}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}

                {travelRows.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={6} className="text-center text-muted-foreground py-8">
                      No travel distance tiers. Click "Add Tier" to create one.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      {/* Cost Codes */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>Cost Codes</CardTitle>
          <Button size="sm" onClick={addCostCodeRow} className="gap-1">
            <Plus className="h-4 w-4" />
            Add Code
          </Button>
        </CardHeader>
        <CardContent>
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[160px]">Code</TableHead>
                  <TableHead>Description</TableHead>
                  <TableHead className="w-[60px] text-right">
                    <span className="sr-only">Remove</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {costCodeRows.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell>
                      <Input
                        value={r.code}
                        onChange={(e) => updateCostCodeRow(r.id, { code: e.target.value })}
                        className="h-8 font-mono"
                        placeholder="e.g. 999-100"
                      />
                    </TableCell>
                    <TableCell>
                      <Input
                        value={r.description}
                        onChange={(e) => updateCostCodeRow(r.id, { description: e.target.value })}
                        className="h-8"
                        placeholder="Description"
                      />
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        size="icon"
                        variant="ghost"
                        className="h-7 w-7 text-destructive hover:text-destructive"
                        onClick={() => removeCostCodeRow(r.id)}
                        aria-label={`Remove cost code ${r.code || '(new)'}`}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}

                {costCodeRows.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={3} className="text-center text-muted-foreground py-8">
                      No cost codes. Click "Add Code" to create one.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      {/* Sticky save bar — single commit point for the whole page */}
      <div className="sticky bottom-0 z-10 -mx-4 md:-mx-6 border-t bg-background/95 px-4 py-3 backdrop-blur supports-[backdrop-filter]:bg-background/80">
        <div className="flex items-center justify-end gap-3">
          {isDirty && (
            <span className="mr-auto text-sm text-muted-foreground">You have unsaved changes.</span>
          )}
          {isDirty && (
            <Button variant="outline" onClick={() => setDiscardConfirmOpen(true)} disabled={saving} className="gap-2">
              <Undo2 className="h-4 w-4" />
              Discard
            </Button>
          )}
          <Button onClick={handleSave} disabled={!isDirty || saving} className="gap-2">
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            Save Settings
          </Button>
        </div>
      </div>

      {/* Discard confirmation */}
      <AlertDialog open={discardConfirmOpen} onOpenChange={setDiscardConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-amber-500" />
              Discard changes?
            </AlertDialogTitle>
            <AlertDialogDescription>
              This reverts every field on this page back to the last saved values. Unsaved edits will be lost.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep editing</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDiscard}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Discard changes
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
