import { Document, Page, View, Text } from '@react-pdf/renderer'
import { styles } from './styles'
import { PDFHeader } from './PDFHeader'
import { PDFFooter } from './PDFFooter'
import {
  getLineItemUnitPrice,
  getLineItemTotal,
  getEffectiveLineItemTotal,
  calculateNonPmsTotal,
  calculateQuoteTotal,
  formatCurrency,
} from '@/lib/pricing'
import type { Quote, QuoteLineItem, Project, CompanySettings } from '@/types'

interface QuotePDFProps {
  quote: Quote
  project: Project
  companySettings: CompanySettings
  includeBreakdown?: boolean
}

function LineItemTable({
  title,
  items,
  nonPmsTotal,
  useEffective,
  showLineDetails,
}: {
  title: string
  items: QuoteLineItem[]
  nonPmsTotal: number
  useEffective?: boolean
  showLineDetails: boolean
}) {
  if (items.length === 0) return null

  const sectionTotal = items.reduce(
    (sum, item) =>
      sum + (useEffective ? getEffectiveLineItemTotal(item, nonPmsTotal) : getLineItemTotal(item)),
    0
  )

  return (
    <>
      <Text style={styles.sectionTitle}>{title}</Text>

      {showLineDetails && (
        <>
          {/* Table header */}
          <View style={styles.tableHeader}>
            <Text style={[styles.colHeaderText, styles.colDescription]}>Description</Text>
            <Text style={[styles.colHeaderText, styles.colQty]}>Qty</Text>
            <Text style={[styles.colHeaderText, styles.colUnitPrice]}>Unit Price</Text>
            <Text style={[styles.colHeaderText, styles.colTotal]}>Total</Text>
          </View>

          {/* Table rows */}
          {items.map((item, idx) => {
            const description = getItemDescription(item)
            const unitPrice = useEffective && item.is_pms && item.pms_percent != null
              ? nonPmsTotal * item.pms_percent / 100
              : getLineItemUnitPrice(item)
            const total = useEffective
              ? getEffectiveLineItemTotal(item, nonPmsTotal)
              : getLineItemTotal(item)

            return (
              <View
                key={item.id}
                style={[styles.tableRow, idx % 2 === 1 ? styles.tableRowAlt : {}]}
                wrap={false}
              >
                <Text style={styles.colDescription}>
                  {description}
                  {item.is_pms && item.pms_percent != null ? ` (PMS ${item.pms_percent}%)` : ''}
                </Text>
                <Text style={styles.colQty}>{item.quantity}</Text>
                <Text style={styles.colUnitPrice}>{formatCurrency(unitPrice)}</Text>
                <Text style={styles.colTotal}>{formatCurrency(total)}</Text>
              </View>
            )
          })}
        </>
      )}

      {/* Section subtotal */}
      <View style={styles.tableFooterRow}>
        <Text style={[styles.colDescription, styles.bold]}>{title} Subtotal</Text>
        <Text style={styles.colQty} />
        <Text style={styles.colUnitPrice} />
        <Text style={[styles.colTotal, styles.bold]}>{formatCurrency(sectionTotal)}</Text>
      </View>
    </>
  )
}

function getItemDescription(item: QuoteLineItem): string {
  if (item.item_type === 'labor' && item.labor) return item.labor.description
  if (item.item_type === 'part' && item.part)
    return `${item.part.part_number} - ${item.part.description}`
  if (item.item_type === 'misc' && item.miscellaneous) return item.miscellaneous.description
  return item.description || 'Unknown item'
}

export function QuotePDF({ quote, project, companySettings, includeBreakdown = true }: QuotePDFProps) {
  const partItems = quote.line_items.filter(i => i.item_type === 'part')
  const laborItems = quote.line_items.filter(i => i.item_type === 'labor')
  const miscItems = quote.line_items.filter(i => i.item_type === 'misc')

  const nonPmsTotal = calculateNonPmsTotal(quote.line_items)
  const subtotal = calculateQuoteTotal(quote.line_items)
  const hstRate = companySettings.hst_rate ?? 13.0
  const hstAmount = subtotal * (hstRate / 100)
  const grandTotal = subtotal + hstAmount

  const formattedDate = new Date(quote.created_at).toLocaleDateString('en-CA', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  })

  return (
    <Document>
      <Page size="LETTER" style={styles.page}>
        {/* Header */}
        <PDFHeader companySettings={companySettings} title="QUOTATION">
          <View>
            <View style={styles.metaRow}>
              <Text style={styles.metaLabel}>Quotation #:</Text>
              <Text style={styles.metaValue}>{quote.quote_number}</Text>
            </View>
            <View style={styles.metaRow}>
              <Text style={styles.metaLabel}>Client PO #:</Text>
              <Text style={styles.metaValue}>{quote.client_po_number || '—'}</Text>
            </View>
            <View style={styles.metaRow}>
              <Text style={styles.metaLabel}>Date:</Text>
              <Text style={styles.metaValue}>{formattedDate}</Text>
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
          <View style={styles.projectField}>
            <Text style={styles.bold}>Date:</Text>
            <Text>{formattedDate}</Text>
          </View>
        </View>

        {/* Work Description */}
        {quote.work_description && (
          <View style={styles.workDescription}>
            <Text style={[styles.bold, { marginBottom: 2 }]}>Work Description:</Text>
            <Text>{quote.work_description}</Text>
          </View>
        )}

        {/* Line Items by Section */}
        <LineItemTable title="Parts" items={partItems} nonPmsTotal={nonPmsTotal} showLineDetails={includeBreakdown} />
        <LineItemTable title="Labour" items={laborItems} nonPmsTotal={nonPmsTotal} useEffective showLineDetails={includeBreakdown} />
        <LineItemTable title="Miscellaneous" items={miscItems} nonPmsTotal={nonPmsTotal} showLineDetails={includeBreakdown} />

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
        </View>

        {/* Signature */}
        <View style={styles.signatureBlock}>
          <View>
            <View style={styles.signatureLine} />
            <Text style={{ fontSize: 7, marginTop: 2 }}>Signature</Text>
          </View>
          <View>
            <View style={styles.signatureLine} />
            <Text style={{ fontSize: 7, marginTop: 2 }}>Date</Text>
          </View>
        </View>

        <PDFFooter />
      </Page>
    </Document>
  )
}
