import { useState, useEffect } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Button } from "@/components/ui/button"
import { api } from "@/api/client"
import type { Invoice, Project, CompanySettings } from "@/types"
import { Receipt, Package, Wrench, FileText, AlertTriangle, Printer, Loader2 } from "lucide-react"

interface InvoiceEditorProps {
  invoiceId: number
  onUpdate?: () => void
}

export function InvoiceEditor({ invoiceId, onUpdate }: InvoiceEditorProps) {
  const [invoice, setInvoice] = useState<Invoice | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [isPrinting, setIsPrinting] = useState(false)

  const fetchInvoice = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await api.invoices.get(invoiceId)
      setInvoice(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch invoice")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchInvoice()
  }, [invoiceId])

  const handleStatusChange = async (newStatus: "Sent" | "Paid") => {
    if (!invoice || invoice.status === "Voided") return

    try {
      await api.invoices.updateStatus(invoiceId, { status: newStatus })
      fetchInvoice()
      onUpdate?.()
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to update status")
    }
  }

  const getStatusBadgeVariant = (status: string) => {
    switch (status) {
      case "Sent":
        return "secondary"
      case "Paid":
        return "default"
      case "Voided":
        return "destructive"
      default:
        return "outline"
    }
  }

  const getTypeIcon = (type: string) => {
    switch (type) {
      case "labor":
        return <Wrench className="h-4 w-4" />
      case "part":
        return <Package className="h-4 w-4" />
      case "misc":
        return <FileText className="h-4 w-4" />
      default:
        return null
    }
  }

  const calculateTotal = () => {
    if (!invoice) return 0
    return invoice.line_items.reduce((sum, item) => {
      const price = item.unit_price || 0
      return sum + price * item.qty_fulfilled_this_invoice
    }, 0)
  }

  const handlePrintInvoice = async () => {
    if (!invoice) return
    setIsPrinting(true)
    try {
      const [quote, companySettings, { pdf }, { InvoicePDF }] = await Promise.all([
        api.quotes.get(invoice.quote_id),
        api.companySettings.get(),
        import('@react-pdf/renderer'),
        import('@/components/pdf/InvoicePDF'),
      ])
      const project = await api.projects.get(quote.project_id) as unknown as Project
      const blob = await pdf(
        <InvoicePDF invoice={invoice} quote={quote} project={project} companySettings={companySettings} />
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

  if (error || !invoice) {
    return (
      <div className="p-8">
        <div className="p-4 bg-destructive/10 border border-destructive/20 rounded-md text-destructive">
          {error || "Invoice not found"}
        </div>
      </div>
    )
  }

  const isVoided = invoice.status === "Voided"

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex justify-between items-start">
        <div>
          <div className="flex items-center gap-3">
            <Receipt className="h-6 w-6" />
            <h2 className="text-xl font-semibold">Invoice #{invoice.id}</h2>
            <Badge variant={getStatusBadgeVariant(invoice.status)}>
              {invoice.status}
            </Badge>
          </div>
          <p className="text-sm text-muted-foreground mt-1">
            Created: {new Date(invoice.created_at).toLocaleString()}
          </p>
          {invoice.voided_at && (
            <p className="text-sm text-destructive mt-1">
              Voided: {new Date(invoice.voided_at).toLocaleString()}
            </p>
          )}
        </div>

        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={handlePrintInvoice}
            disabled={isPrinting}
            className="gap-2"
          >
            {isPrinting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Printer className="h-4 w-4" />}
            {isPrinting ? "Generating..." : "Print Invoice"}
          </Button>
          {!isVoided && (
            <Select
              value={invoice.status}
              onValueChange={(v) => handleStatusChange(v as "Sent" | "Paid")}
            >
              <SelectTrigger className="w-32">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="Sent">Sent</SelectItem>
                <SelectItem value="Paid">Paid</SelectItem>
              </SelectContent>
            </Select>
          )}
        </div>
      </div>

      {/* Voided Warning */}
      {isVoided && (
        <div className="bg-destructive/10 border border-destructive/20 p-4 rounded-md flex items-start gap-3">
          <AlertTriangle className="h-5 w-5 text-destructive flex-shrink-0 mt-0.5" />
          <div>
            <h4 className="text-sm font-medium text-destructive">Invoice Voided</h4>
            <p className="text-sm text-destructive/80">
              This invoice has been voided due to a quote revert. The fulfilled
              quantities have been returned to pending status on the quote.
            </p>
          </div>
        </div>
      )}

      {/* Notes */}
      {invoice.notes && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Notes</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">{invoice.notes}</p>
          </CardContent>
        </Card>
      )}

      {/* Line Items */}
      <Card className={isVoided ? "opacity-60" : undefined}>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Fulfilled Items</CardTitle>
        </CardHeader>
        <CardContent>
          {invoice.line_items.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-4">
              No line items.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Type</TableHead>
                  <TableHead>Description</TableHead>
                  <TableHead className="text-right">Qty Ordered</TableHead>
                  <TableHead className="text-right">Qty This Invoice</TableHead>
                  <TableHead className="text-right">Qty Fulfilled Total</TableHead>
                  <TableHead className="text-right">Qty Pending After</TableHead>
                  <TableHead className="text-right">Unit Price</TableHead>
                  <TableHead className="text-right">Line Total</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {invoice.line_items.map((item) => {
                  const lineTotal = (item.unit_price || 0) * item.qty_fulfilled_this_invoice

                  return (
                    <TableRow key={item.id}>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          {getTypeIcon(item.item_type)}
                          <span className="capitalize">{item.item_type}</span>
                        </div>
                      </TableCell>
                      <TableCell className="font-medium">
                        {item.description || "-"}
                      </TableCell>
                      <TableCell className="text-right">{item.qty_ordered}</TableCell>
                      <TableCell className="text-right">
                        <span className="font-medium text-green-600">
                          {item.qty_fulfilled_this_invoice}
                        </span>
                      </TableCell>
                      <TableCell className="text-right">
                        {item.qty_fulfilled_total}
                      </TableCell>
                      <TableCell className="text-right">
                        {item.qty_pending_after}
                      </TableCell>
                      <TableCell className="text-right">
                        ${(item.unit_price || 0).toFixed(2)}
                      </TableCell>
                      <TableCell className="text-right font-medium">
                        ${lineTotal.toFixed(2)}
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Total */}
      <Card className={isVoided ? "opacity-60" : undefined}>
        <CardContent className="pt-6">
          <div className="flex justify-between items-center">
            <span className="text-lg font-semibold">Invoice Total:</span>
            <span className={`text-2xl font-bold ${isVoided ? "line-through" : ""}`}>
              ${calculateTotal().toFixed(2)}
            </span>
          </div>
        </CardContent>
      </Card>

      {/* Read-only notice */}
      <p className="text-xs text-muted-foreground text-center">
        Invoices are read-only. To modify fulfilled quantities, revert the quote to a
        previous version.
      </p>
    </div>
  )
}
