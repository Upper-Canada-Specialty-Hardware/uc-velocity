import { useEffect, useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { SearchableSelect } from '@/components/ui/searchable-select'
import { api } from '@/api/client'
import { formatCurrency } from '@/lib/pricing'
import { formatDate } from '@/lib/format'
import type { InvoiceSummaryItem, CompanySettings, BacklogQuoteItem, InventoryHealthReport, InventoryHealthIssueCode, ProjectListView } from '@/types'
import { FileText, Download, Loader2, ChevronRight, ChevronDown, FileSpreadsheet, ShieldAlert } from 'lucide-react'

export function ReportsPage() {
  // Invoice Summary state
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [invoices, setInvoices] = useState<InvoiceSummaryItem[] | null>(null)
  const [companySettings, setCompanySettings] = useState<CompanySettings | null>(null)

  // Project picker for Invoice Summary ('' = All Projects)
  const [projects, setProjects] = useState<ProjectListView[]>([])
  const [selectedProjectId, setSelectedProjectId] = useState<string>('')

  useEffect(() => {
    api.projects.getListView().then(setProjects).catch(() => {
      // Picker stays empty; report still works in "All Projects" mode
    })
  }, [])

  const projectOptions = useMemo(
    () => [
      { value: '', label: 'All Projects' },
      ...projects.map((p) => ({
        value: String(p.id),
        label: `${p.uca_project_number} — ${p.name}`,
        description: p.customer_name,
      })),
    ],
    [projects],
  )

  const selectedProject = useMemo(
    () => projects.find((p) => String(p.id) === selectedProjectId) ?? null,
    [projects, selectedProjectId],
  )

  // Backlog Quotes state
  const [backlogLoading, setBacklogLoading] = useState(false)
  const [backlogError, setBacklogError] = useState<string | null>(null)
  const [backlogData, setBacklogData] = useState<BacklogQuoteItem[] | null>(null)
  const [expandedQuotes, setExpandedQuotes] = useState<Set<number>>(new Set())

  // Inventory Health state (UX-7)
  const [healthLoading, setHealthLoading] = useState(false)
  const [healthError, setHealthError] = useState<string | null>(null)
  const [healthData, setHealthData] = useState<InventoryHealthReport | null>(null)

  const handleInventoryHealthGenerate = async () => {
    setHealthLoading(true)
    setHealthError(null)
    setHealthData(null)
    try {
      const data = await api.reports.getInventoryHealth()
      setHealthData(data)
    } catch (err) {
      setHealthError(err instanceof Error ? err.message : 'Failed to load inventory health')
    } finally {
      setHealthLoading(false)
    }
  }

  // ===== Invoice Summary handlers =====
  const handleGenerate = async () => {
    if (!startDate || !endDate) {
      setError('Please select both start and end dates.')
      return
    }

    setLoading(true)
    setError(null)
    setInvoices(null)

    try {
      const projectIdNum = selectedProjectId ? Number(selectedProjectId) : undefined
      const [data, settings] = await Promise.all([
        api.invoices.getSummary(startDate, endDate, projectIdNum),
        api.companySettings.get(),
      ])
      setInvoices(data)
      setCompanySettings(settings)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch invoice data')
    } finally {
      setLoading(false)
    }
  }

  const handleDownload = async () => {
    if (!invoices || !companySettings) return

    setLoading(true)
    try {
      const [{ pdf }, { InvoiceSummaryPDF }] = await Promise.all([
        import('@react-pdf/renderer'),
        import('@/components/pdf/InvoiceSummaryPDF'),
      ])
      const blob = await pdf(
        <InvoiceSummaryPDF
          invoices={invoices}
          dateRange={{ start: startDate, end: endDate }}
          companySettings={companySettings}
          project={selectedProject}
        />
      ).toBlob()

      const projectTag = selectedProject ? `_${selectedProject.uca_project_number}` : ''
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `Invoice_Report${projectTag}_${startDate}_to_${endDate}.pdf`
      link.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate PDF')
    } finally {
      setLoading(false)
    }
  }

  const handleOpenInTab = async () => {
    if (!invoices || !companySettings) return

    setLoading(true)
    try {
      const [{ pdf }, { InvoiceSummaryPDF }] = await Promise.all([
        import('@react-pdf/renderer'),
        import('@/components/pdf/InvoiceSummaryPDF'),
      ])
      const blob = await pdf(
        <InvoiceSummaryPDF
          invoices={invoices}
          dateRange={{ start: startDate, end: endDate }}
          companySettings={companySettings}
          project={selectedProject}
        />
      ).toBlob()

      const url = URL.createObjectURL(blob)
      window.open(url, '_blank')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate PDF')
    } finally {
      setLoading(false)
    }
  }

  // ===== Backlog Quotes handlers =====
  const handleBacklogGenerate = async () => {
    setBacklogLoading(true)
    setBacklogError(null)
    setBacklogData(null)
    setExpandedQuotes(new Set())

    try {
      const data = await api.reports.getBacklogQuotes()
      setBacklogData(data)
    } catch (err) {
      setBacklogError(err instanceof Error ? err.message : 'Failed to fetch backlog data')
    } finally {
      setBacklogLoading(false)
    }
  }

  const handleBacklogDownload = async () => {
    if (!backlogData) return
    const { generateBacklogExcel } = await import('@/lib/excel')
    generateBacklogExcel(backlogData)
  }

  const toggleQuoteExpanded = (quoteId: number) => {
    setExpandedQuotes(prev => {
      const next = new Set(prev)
      if (next.has(quoteId)) {
        next.delete(quoteId)
      } else {
        next.add(quoteId)
      }
      return next
    })
  }

  const statusVariant = (status: string) => {
    if (status === 'Work Order') return 'default' as const
    if (status === 'Invoiced') return 'secondary' as const
    return 'outline' as const
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Reports</h1>
        <p className="text-muted-foreground">Generate and download reports</p>
      </div>

      {/* Invoice Summary Report */}
      <div className="bg-card rounded-lg border shadow-sm">
        <div className="p-4 border-b">
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <FileText className="h-5 w-5" />
            Invoice Summary Report
          </h2>
          <p className="text-sm text-muted-foreground mt-1">
            Generate a summary of all invoices within a date range.
          </p>
        </div>

        <div className="p-4 space-y-4">
          {/* Filters: project + date range */}
          <div className="flex flex-wrap gap-4 items-end">
            <div className="space-y-2">
              <Label>Project</Label>
              <SearchableSelect
                options={projectOptions}
                value={selectedProjectId}
                onChange={setSelectedProjectId}
                placeholder="All Projects"
                searchPlaceholder="Search by UCA #, name, or customer..."
                emptyMessage="No projects found."
                className="w-72"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="start-date">Start Date</Label>
              <Input
                id="start-date"
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="w-48"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="end-date">End Date</Label>
              <Input
                id="end-date"
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                className="w-48"
              />
            </div>
            <Button onClick={handleGenerate} disabled={loading || !startDate || !endDate}>
              {loading ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
              Generate
            </Button>
          </div>

          {error && (
            <div className="p-3 bg-destructive/10 border border-destructive/20 rounded-md text-destructive text-sm">
              {error}
            </div>
          )}

          {/* Results */}
          {invoices !== null && (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <p className="text-sm text-muted-foreground">
                  Found <span className="font-medium text-foreground">{invoices.length}</span> invoice{invoices.length !== 1 ? 's' : ''} in the selected range.
                </p>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" onClick={handleOpenInTab} disabled={loading || invoices.length === 0}>
                    <FileText className="h-4 w-4 mr-2" />
                    Open PDF
                  </Button>
                  <Button size="sm" onClick={handleDownload} disabled={loading || invoices.length === 0}>
                    <Download className="h-4 w-4 mr-2" />
                    Download PDF
                  </Button>
                </div>
              </div>

              {/* Preview table */}
              {invoices.length > 0 && (
                <div className="border rounded-md overflow-hidden">
                  <table className="w-full text-sm">
                    <thead className="bg-muted/50">
                      <tr>
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">Invoice #</th>
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">Date</th>
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">UCA Project #</th>
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">P/O Number</th>
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">Customer / Project</th>
                        <th className="px-3 py-2 text-right font-medium text-muted-foreground">Net Sales</th>
                        <th className="px-3 py-2 text-right font-medium text-muted-foreground">HST</th>
                        <th className="px-3 py-2 text-right font-medium text-muted-foreground">Total</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y">
                      {invoices.map((inv) => (
                        <tr key={inv.invoice_id} className="hover:bg-muted/50">
                          <td className="px-3 py-2">{inv.invoice_id}</td>
                          <td className="px-3 py-2">{formatDate(inv.invoice_date)}</td>
                          <td className="px-3 py-2">{inv.uca_project_number}</td>
                          <td className="px-3 py-2">{inv.client_po_number || '—'}</td>
                          <td className="px-3 py-2">{inv.customer_name} — {inv.project_name}</td>
                          <td className="px-3 py-2 text-right">${inv.net_sales.toFixed(2)}</td>
                          <td className="px-3 py-2 text-right">${inv.hst_amount.toFixed(2)}</td>
                          <td className="px-3 py-2 text-right font-medium">${inv.grand_total.toFixed(2)}</td>
                        </tr>
                      ))}
                    </tbody>
                    <tfoot className="bg-muted/50 font-medium">
                      <tr>
                        <td colSpan={5} className="px-3 py-2">Totals</td>
                        <td className="px-3 py-2 text-right">
                          ${invoices.reduce((s, i) => s + i.net_sales, 0).toFixed(2)}
                        </td>
                        <td className="px-3 py-2 text-right">
                          ${invoices.reduce((s, i) => s + i.hst_amount, 0).toFixed(2)}
                        </td>
                        <td className="px-3 py-2 text-right">
                          ${invoices.reduce((s, i) => s + i.grand_total, 0).toFixed(2)}
                        </td>
                      </tr>
                    </tfoot>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Backlog Quotes Report */}
      <div className="bg-card rounded-lg border shadow-sm">
        <div className="p-4 border-b">
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <FileSpreadsheet className="h-5 w-5" />
            Backlog Quotes Report
          </h2>
          <p className="text-sm text-muted-foreground mt-1">
            Quotes with uninvoiced line items (Work Order and partially Invoiced). Point-in-time snapshot.
          </p>
        </div>

        <div className="p-4 space-y-4">
          <Button onClick={handleBacklogGenerate} disabled={backlogLoading}>
            {backlogLoading ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
            Generate
          </Button>

          {backlogError && (
            <div className="p-3 bg-destructive/10 border border-destructive/20 rounded-md text-destructive text-sm">
              {backlogError}
            </div>
          )}

          {backlogData !== null && (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <p className="text-sm text-muted-foreground">
                  Found <span className="font-medium text-foreground">{backlogData.length}</span> quote{backlogData.length !== 1 ? 's' : ''} with uninvoiced items.
                </p>
                <Button size="sm" onClick={handleBacklogDownload} disabled={backlogData.length === 0}>
                  <Download className="h-4 w-4 mr-2" />
                  Download Excel
                </Button>
              </div>

              {backlogData.length > 0 && (
                <div className="border rounded-md overflow-hidden">
                  <table className="w-full text-sm">
                    <thead className="bg-muted/50">
                      <tr>
                        <th className="w-8 px-2 py-2" />
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">Quote #</th>
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">UCA Project #</th>
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">Customer / Project</th>
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">Client PO</th>
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">Status</th>
                        <th className="px-3 py-2 text-right font-medium text-muted-foreground">Backlog Value</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y">
                      {backlogData.map((q) => {
                        const isExpanded = expandedQuotes.has(q.quote_id)
                        return (
                          <BacklogQuoteRow
                            key={q.quote_id}
                            quote={q}
                            isExpanded={isExpanded}
                            onToggle={() => toggleQuoteExpanded(q.quote_id)}
                            statusVariant={statusVariant}
                          />
                        )
                      })}
                    </tbody>
                    <tfoot className="bg-muted/50 font-medium">
                      <tr>
                        <td colSpan={6} className="px-3 py-2">Grand Total</td>
                        <td className="px-3 py-2 text-right">
                          {formatCurrency(backlogData.reduce((s, q) => s + q.backlog_total, 0))}
                        </td>
                      </tr>
                    </tfoot>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Inventory Health Report (UX-7) */}
      <div className="bg-card rounded-lg border shadow-sm">
        <div className="p-4 border-b">
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <ShieldAlert className="h-5 w-5" />
            Inventory Health Report
          </h2>
          <p className="text-sm text-muted-foreground mt-1">
            Parts flagged with data-quality issues — zero cost, description equal to part number,
            or CSV-escape artifacts. Surfaces cleanup candidates; never mutates data.
          </p>
        </div>

        <div className="p-4 space-y-4">
          <Button onClick={handleInventoryHealthGenerate} disabled={healthLoading}>
            {healthLoading ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
            Generate
          </Button>

          {healthError && (
            <div className="p-3 bg-destructive/10 border border-destructive/20 rounded-md text-destructive text-sm">
              {healthError}
            </div>
          )}

          {healthData !== null && (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                <span className="font-medium text-foreground">{healthData.flagged.toLocaleString()}</span> of{' '}
                <span className="font-medium text-foreground">{healthData.total_parts.toLocaleString()}</span>{' '}
                parts flagged ({healthData.total_parts > 0
                  ? ((healthData.flagged / healthData.total_parts) * 100).toFixed(1)
                  : '0.0'}%).
              </p>

              {healthData.items.length > 0 && (
                <div className="border rounded-md overflow-hidden">
                  <table className="w-full text-sm">
                    <thead className="bg-muted/50">
                      <tr>
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">Part Number</th>
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">Description</th>
                        <th className="px-3 py-2 text-right font-medium text-muted-foreground">Cost</th>
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">Issues</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y">
                      {healthData.items.map((it) => (
                        <tr key={it.part_id} className="hover:bg-muted/50">
                          <td className="px-3 py-2 font-mono text-xs">{it.part_number}</td>
                          <td className="px-3 py-2 max-w-md truncate" title={it.description}>{it.description || '—'}</td>
                          <td className="px-3 py-2 text-right">${it.cost.toFixed(2)}</td>
                          <td className="px-3 py-2">
                            <div className="flex flex-wrap gap-1">
                              {it.issues.map((code) => (
                                <Badge key={code} variant="outline" className="text-[10px]">
                                  {issueLabel(code)}
                                </Badge>
                              ))}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function issueLabel(code: InventoryHealthIssueCode): string {
  switch (code) {
    case "zero_cost": return "Zero cost"
    case "description_matches_part_number": return "Description = part number"
    case "description_has_escaped_quotes": return "Escaped quotes"
  }
}

function BacklogQuoteRow({
  quote,
  isExpanded,
  onToggle,
  statusVariant,
}: {
  quote: BacklogQuoteItem
  isExpanded: boolean
  onToggle: () => void
  statusVariant: (s: string) => 'default' | 'secondary' | 'outline'
}) {
  return (
    <>
      <tr className="hover:bg-muted/50 cursor-pointer" onClick={onToggle}>
        <td className="px-2 py-2 text-center">
          {isExpanded
            ? <ChevronDown className="h-4 w-4 text-muted-foreground" />
            : <ChevronRight className="h-4 w-4 text-muted-foreground" />}
        </td>
        <td className="px-3 py-2 font-medium">{quote.quote_number}</td>
        <td className="px-3 py-2">{quote.uca_project_number}</td>
        <td className="px-3 py-2">{quote.customer_name} — {quote.project_name}</td>
        <td className="px-3 py-2">{quote.client_po_number || '—'}</td>
        <td className="px-3 py-2">
          <Badge variant={statusVariant(quote.status)}>{quote.status}</Badge>
        </td>
        <td className="px-3 py-2 text-right font-medium">{formatCurrency(quote.backlog_total)}</td>
      </tr>
      {isExpanded && quote.line_items.map((li) => (
        <tr key={li.line_item_id} className="bg-muted/30">
          <td />
          <td className="px-3 py-1.5 text-xs text-muted-foreground pl-8" colSpan={2}>
            <span className="capitalize">{li.item_type}</span> — {li.description}
          </td>
          <td className="px-3 py-1.5 text-xs text-muted-foreground">
            Ord: {li.quantity} / Ful: {li.qty_fulfilled} / Pend: {li.qty_pending}
          </td>
          <td className="px-3 py-1.5 text-xs text-muted-foreground">
            {formatCurrency(li.unit_price)}
          </td>
          <td />
          <td className="px-3 py-1.5 text-xs text-right text-muted-foreground">
            {formatCurrency(li.backlog_value)}
          </td>
        </tr>
      ))}
    </>
  )
}
