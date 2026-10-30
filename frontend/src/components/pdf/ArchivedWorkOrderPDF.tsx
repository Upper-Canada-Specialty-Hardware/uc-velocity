import { Document, Page, View, Text } from '@react-pdf/renderer'
import { styles } from './styles'
import { PDFHeader } from './PDFHeader'
import { PDFFooter } from './PDFFooter'
import { formatCurrency } from '@/lib/pricing'
import { formatQuantity } from '@/lib/format'
import type { ArchivedLine, ArchivedWorkOrder, CompanySettings } from '@/types'

/** Props for the plain archived-work-order sheet. */
interface ArchivedWorkOrderPDFProps {
  /** The Vision work order (as archived): number, date, description, lines. */
  workOrder: ArchivedWorkOrder
  /** The project's UCA number (header). */
  uca: string
  /** The project name (header). */
  projectName: string | null
  /** The customer name (header). */
  customer: string | null
  /** Company settings, for the shared PDF header/logo. */
  companySettings: CompanySettings
}

/**
 * One archived Vision work order as a plain printable sheet (#319/#323).
 * For a Vision-only archive (no Velocity rows) there is no quote to run through
 * QuotePDF, so this prints the work order directly: project, WO number, date,
 * description, and the lines with qty, unit price and a simple line total
 * (quantity x unit price, exactly as Vision held them). It reuses the shared PDF
 * styles so it matches the other documents.
 * @returns A @react-pdf document element.
 */
export function ArchivedWorkOrderPDF({ workOrder, uca, projectName, customer, companySettings }: ArchivedWorkOrderPDFProps) {
  const lines = workOrder.lines || []                          // the WO's lines, or none
  const lineTotal = (l: ArchivedLine) => (l.quantity ?? 0) * (l.unit_price ?? 0)   // qty x unit price, as Vision held it
  const subtotal = lines.reduce((s, l) => s + lineTotal(l), 0) // sum of the line totals
  const date = workOrder.created_at                            // the WO date, formatted, or a dash
    ? new Date(workOrder.created_at).toLocaleDateString('en-CA', { day: '2-digit', month: 'short', year: 'numeric' })
    : '—'
  return (
    <Document>
      <Page size="LETTER" style={styles.page}>
        <PDFHeader companySettings={companySettings} title="WORK ORDER">
          <View>
            <View style={styles.metaRow}>
              <Text style={styles.metaLabel}>WO #:</Text>
              <Text style={styles.metaValue}>{workOrder.wo ?? '—'}</Text>
            </View>
            <View style={styles.metaRow}>
              <Text style={styles.metaLabel}>Client PO #:</Text>
              <Text style={styles.metaValue}>{workOrder.client_po_number || '—'}</Text>
            </View>
            <View style={styles.metaRow}>
              <Text style={styles.metaLabel}>Date:</Text>
              <Text style={styles.metaValue}>{date}</Text>
            </View>
          </View>
        </PDFHeader>

        <View style={styles.customerSection}>
          <Text style={styles.customerLabel}>CUSTOMER:</Text>
          <Text style={styles.customerName}>{customer || '—'}</Text>
        </View>

        <View style={styles.projectRow}>
          <View style={styles.projectField}>
            <Text style={styles.bold}>UCA #:</Text>
            <Text>{uca}</Text>
          </View>
          <View style={styles.projectField}>
            <Text style={styles.bold}>Project:</Text>
            <Text>{projectName || '—'}</Text>
          </View>
          <View style={styles.projectField}>
            <Text style={styles.bold}>Date:</Text>
            <Text>{date}</Text>
          </View>
        </View>

        {workOrder.work_description && (
          <View style={styles.workDescription}>
            <Text style={[styles.bold, { marginBottom: 2 }]}>Work Description:</Text>
            <Text>{workOrder.work_description}</Text>
          </View>
        )}

        <View style={styles.tableHeader}>
          <Text style={[styles.colHeaderText, styles.colDescription]}>Description</Text>
          <Text style={[styles.colHeaderText, styles.colQty]}>Qty</Text>
          <Text style={[styles.colHeaderText, styles.colUnitPrice]}>Unit Price</Text>
          <Text style={[styles.colHeaderText, styles.colTotal]}>Total</Text>
        </View>
        {lines.map((l, idx) => (
          <View key={l.vid ?? idx} style={[styles.tableRow, idx % 2 === 1 ? styles.tableRowAlt : {}]} wrap={false}>
            <Text style={styles.colDescription}>{l.description || l.part_number || l.ref || '—'}</Text>
            <Text style={styles.colQty}>{l.quantity != null ? formatQuantity(l.quantity) : '—'}</Text>
            <Text style={styles.colUnitPrice}>{l.unit_price != null ? formatCurrency(l.unit_price) : '—'}</Text>
            <Text style={styles.colTotal}>{formatCurrency(lineTotal(l))}</Text>
          </View>
        ))}
        <View style={styles.tableFooterRow}>
          <Text style={[styles.colDescription, styles.bold]}>Subtotal</Text>
          <Text style={styles.colQty} />
          <Text style={styles.colUnitPrice} />
          <Text style={[styles.colTotal, styles.bold]}>{formatCurrency(subtotal)}</Text>
        </View>

        <PDFFooter />
      </Page>
    </Document>
  )
}
