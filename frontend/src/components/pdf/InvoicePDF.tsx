import { Document, Page, View, Text } from '@react-pdf/renderer'
import { styles } from './styles'
import { PDFHeader } from './PDFHeader'
import { PDFFooter } from './PDFFooter'
import { formatCurrency } from '@/lib/pricing'
import type { Invoice, InvoiceLineItem, Quote, Project, CompanySettings } from '@/types'

interface InvoicePDFProps {
  invoice: Invoice
  quote: Quote
  project: Project
  companySettings: CompanySettings
}

// Invoice-specific column widths
const invCol = {
  description: { width: '34%' } as const,
  qtyOrd: { width: '10%', textAlign: 'right' as const },
  qtyShip: { width: '10%', textAlign: 'right' as const },
  qtyBO: { width: '10%', textAlign: 'right' as const },
  unitPrice: { width: '16%', textAlign: 'right' as const },
  lineTotal: { width: '20%', textAlign: 'right' as const },
}

function InvoiceLineItemTable({
  title,
  items,
}: {
  title: string
  items: InvoiceLineItem[]
}) {
  if (items.length === 0) return null

  const sectionTotal = items.reduce(
    (sum, item) => sum + (item.unit_price || 0) * item.qty_fulfilled_this_invoice,
    0
  )

  return (
    <>
      <Text style={styles.sectionTitle}>{title}</Text>

      {/* Table header */}
      <View style={styles.tableHeader}>
        <Text style={[styles.colHeaderText, invCol.description]}>Description</Text>
        <Text style={[styles.colHeaderText, invCol.qtyOrd]}>Qty Ord</Text>
        <Text style={[styles.colHeaderText, invCol.qtyShip]}>Qty Ship</Text>
        <Text style={[styles.colHeaderText, invCol.qtyBO]}>Qty B/O</Text>
        <Text style={[styles.colHeaderText, invCol.unitPrice]}>Unit Price</Text>
        <Text style={[styles.colHeaderText, invCol.lineTotal]}>Line Total</Text>
      </View>

      {/* Table rows */}
      {items.map((item, idx) => {
        const lineTotal = (item.unit_price || 0) * item.qty_fulfilled_this_invoice

        return (
          <View
            key={item.id}
            style={[styles.tableRow, idx % 2 === 1 ? styles.tableRowAlt : {}]}
            wrap={false}
          >
            <Text style={invCol.description}>{item.description || 'Unknown item'}</Text>
            <Text style={invCol.qtyOrd}>{item.qty_ordered}</Text>
            <Text style={invCol.qtyShip}>{item.qty_fulfilled_this_invoice}</Text>
            <Text style={invCol.qtyBO}>{item.qty_pending_after}</Text>
            <Text style={invCol.unitPrice}>{formatCurrency(item.unit_price || 0)}</Text>
            <Text style={invCol.lineTotal}>{formatCurrency(lineTotal)}</Text>
          </View>
        )
      })}

      {/* Section subtotal */}
      <View style={styles.tableFooterRow}>
        <Text style={[invCol.description, styles.bold]}>{title} Subtotal</Text>
        <Text style={invCol.qtyOrd} />
        <Text style={invCol.qtyShip} />
        <Text style={invCol.qtyBO} />
        <Text style={invCol.unitPrice} />
        <Text style={[invCol.lineTotal, styles.bold]}>{formatCurrency(sectionTotal)}</Text>
      </View>
    </>
  )
}

export function InvoicePDF({ invoice, quote, project, companySettings }: InvoicePDFProps) {
  const laborItems = invoice.line_items.filter(i => i.item_type === 'labor')
  const partItems = invoice.line_items.filter(i => i.item_type === 'part')
  const miscItems = invoice.line_items.filter(i => i.item_type === 'misc')

  const subtotal = invoice.line_items.reduce(
    (sum, item) => sum + (item.unit_price || 0) * item.qty_fulfilled_this_invoice,
    0
  )
  const hstRate = companySettings.hst_rate ?? 13.0
  const hstAmount = subtotal * (hstRate / 100)
  const grandTotal = subtotal + hstAmount

  const invoiceDate = new Date(invoice.created_at).toLocaleDateString('en-CA', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  })

  const quoteDate = new Date(quote.created_at).toLocaleDateString('en-CA', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  })

  return (
    <Document>
      <Page size="LETTER" style={styles.page}>
        {/* Header */}
        <PDFHeader companySettings={companySettings} title="INVOICE">
          <View>
            <View style={styles.metaRow}>
              <Text style={styles.metaLabel}>Quote #:</Text>
              <Text style={styles.metaValue}>{quote.quote_number}</Text>
            </View>
            <View style={styles.metaRow}>
              <Text style={styles.metaLabel}>Invoice #:</Text>
              <Text style={styles.metaValue}>{invoice.invoice_number ?? invoice.id}</Text>
            </View>
            <View style={styles.metaRow}>
              <Text style={styles.metaLabel}>Quote Date:</Text>
              <Text style={styles.metaValue}>{quoteDate}</Text>
            </View>
            <View style={styles.metaRow}>
              <Text style={styles.metaLabel}>Invoice Date:</Text>
              <Text style={styles.metaValue}>{invoiceDate}</Text>
            </View>
            <View style={styles.metaRow}>
              <Text style={styles.metaLabel}>Client PO #:</Text>
              <Text style={styles.metaValue}>{quote.client_po_number || '—'}</Text>
            </View>
          </View>
        </PDFHeader>

        {/* Customer */}
        <View style={styles.customerSection}>
          <Text style={styles.customerLabel}>CUSTOMER:</Text>
          <Text style={styles.customerName}>{project.customer.name}</Text>
          <Text style={styles.customerAddress}>{project.customer.address}</Text>
          <Text style={styles.customerAddress}>{project.customer.postal_code}</Text>
        </View>

        {/* Project Info */}
        <View style={styles.projectRow}>
          <View style={styles.projectField}>
            <Text style={styles.bold}>UCA #:</Text>
            <Text>{project.uca_project_number}</Text>
          </View>
          <View style={styles.projectField}>
            <Text style={styles.bold}>Project:</Text>
            <Text>{project.name}</Text>
          </View>
        </View>

        {/* Line Items by Section */}
        <InvoiceLineItemTable title="Labour" items={laborItems} />
        <InvoiceLineItemTable title="Parts / Material" items={partItems} />
        <InvoiceLineItemTable title="Miscellaneous" items={miscItems} />

        {/* Totals */}
        <View style={styles.totalsBlock}>
          <View style={styles.totalsRow}>
            <Text style={[styles.totalsLabel, styles.bold]}>SUBTOTAL:</Text>
            <Text style={styles.totalsValue}>{formatCurrency(subtotal)}</Text>
          </View>
          {hstRate > 0 && (
            <View style={styles.totalsRow}>
              <Text style={styles.totalsLabel}>HST (Extra):</Text>
              <Text style={styles.totalsValue}>{formatCurrency(hstAmount)}</Text>
            </View>
          )}
          <View style={styles.grandTotalRow}>
            <Text style={[styles.totalsLabel, styles.bold]}>TOTAL:</Text>
            <Text style={[styles.totalsValue, styles.bold]}>{formatCurrency(grandTotal)}</Text>
          </View>
        </View>

        {/* Payment Terms */}
        <View style={styles.paymentTerms}>
          <Text style={styles.paymentTermsBold}>Payment terms: Net 30 days O.A.C.</Text>
          <Text style={{ fontSize: 7, marginTop: 2 }}>
            Outstanding balances will be assessed a charge of 2% per month.
          </Text>
        </View>

        {/* Invoice notes */}
        {invoice.notes && (
          <View style={[styles.workDescription, { marginTop: 8 }]}>
            <Text style={[styles.bold, { marginBottom: 2 }]}>Notes:</Text>
            <Text>{invoice.notes}</Text>
          </View>
        )}

        <PDFFooter leftText="Supplied and Installed." />
      </Page>
    </Document>
  )
}
