import { Document, Page, View, Text } from '@react-pdf/renderer'
import { styles, BORDER_COLOR, HEADER_BG } from './styles'
import { PDFHeader } from './PDFHeader'
import { PDFFooter } from './PDFFooter'
import { formatCurrency } from '@/lib/pricing'
import type {
  PurchaseOrder, POLineItem, POReceiving, Project, CompanySettings,
} from '@/types'

interface ReceivedPurchaseOrderPDFProps {
  po: PurchaseOrder
  receivings: POReceiving[]
  project: Project
  companySettings: CompanySettings
}

// Number of (qty, date) receipt pairs shown inline on each line item row.
// Matches the legacy Vision-style PO layout; extras collapse to a "+N" hint.
const INLINE_RECEIPT_SLOTS = 3

function formatShortDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString('en-CA', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  })
}

function getItemDescription(item: POLineItem): string {
  if (item.item_type === 'part' && item.part)
    return `${item.part.part_number} - ${item.part.description}`
  return item.description || 'Unknown item'
}

interface ReceiptEntry {
  qty: number
  date: string
}

// Build a chronologically-ordered list of receipts per line item.
// Voided receivings are excluded so the PDF reflects the live ledger.
function buildReceiptIndex(receivings: POReceiving[]): Map<number, ReceiptEntry[]> {
  const index = new Map<number, ReceiptEntry[]>()
  const active = receivings.filter(r => !r.voided_at)
  const sorted = [...active].sort(
    (a, b) => new Date(a.received_date).getTime() - new Date(b.received_date).getTime()
  )
  for (const receiving of sorted) {
    for (const line of receiving.line_items) {
      if (line.po_line_item_id == null) continue
      const qty = line.qty_received_this_receiving ?? 0
      if (qty <= 0) continue
      const list = index.get(line.po_line_item_id) ?? []
      list.push({ qty, date: receiving.received_date })
      index.set(line.po_line_item_id, list)
    }
  }
  return index
}

// Column widths for the receiving table (sums to 100%). Designed for landscape LETTER.
const rcvCol = {
  partNo: { width: '8%', fontSize: 7 } as const,
  description: { width: '32%', fontSize: 7 } as const,
  qtyOrd: { width: '5%', textAlign: 'center' as const, fontSize: 7 },
  bo: { width: '5%', textAlign: 'center' as const, color: '#C00000', fontSize: 7 },
  qtyRec: { width: '5%', textAlign: 'center' as const, fontSize: 7 },
  date: { width: '8%', textAlign: 'center' as const, fontSize: 7 },
  unitPrice: { width: '9%', textAlign: 'right' as const, fontSize: 7 },
}

function ReceivingTableHeader() {
  return (
    <View
      style={[styles.tableHeader, { backgroundColor: HEADER_BG, borderColor: BORDER_COLOR }]}
      fixed
    >
      <Text style={[styles.colHeaderText, rcvCol.partNo]}>Manuf. Part #</Text>
      <Text style={[styles.colHeaderText, rcvCol.description]}>Product Description</Text>
      <Text style={[styles.colHeaderText, rcvCol.qtyOrd]}>Qty.Ord.</Text>
      <Text style={[styles.colHeaderText, rcvCol.bo, { color: '#333333' }]}>B.O.</Text>
      <Text style={[styles.colHeaderText, rcvCol.qtyRec]}>Qty.Rec.</Text>
      <Text style={[styles.colHeaderText, rcvCol.date]}>Date</Text>
      <Text style={[styles.colHeaderText, rcvCol.qtyRec]}>Qty.Rec.</Text>
      <Text style={[styles.colHeaderText, rcvCol.date]}>Date</Text>
      <Text style={[styles.colHeaderText, rcvCol.qtyRec]}>Qty.Rec.</Text>
      <Text style={[styles.colHeaderText, rcvCol.date]}>Date</Text>
      <Text style={[styles.colHeaderText, rcvCol.unitPrice]}>Unit Price</Text>
    </View>
  )
}

function ReceivingLineRow({
  item, receipts, alt,
}: {
  item: POLineItem
  receipts: ReceiptEntry[]
  alt: boolean
}) {
  const partNo = item.item_type === 'part' && item.part ? item.part.part_number : ''
  const description = getItemDescription(item)
  const unitPrice = item.unit_price ?? 0
  const visible = receipts.slice(0, INLINE_RECEIPT_SLOTS)
  const overflow = receipts.length - INLINE_RECEIPT_SLOTS

  const slot = (i: number) => {
    const r = visible[i]
    const isLast = i === INLINE_RECEIPT_SLOTS - 1
    const dateText = r ? formatShortDate(r.date) : ''
    const overflowSuffix = isLast && overflow > 0 ? ` +${overflow}` : ''
    return (
      <>
        <Text style={rcvCol.qtyRec}>{r ? r.qty : ''}</Text>
        <Text style={rcvCol.date}>{`${dateText}${overflowSuffix}`}</Text>
      </>
    )
  }

  return (
    <View style={[styles.tableRow, alt ? styles.tableRowAlt : {}]} wrap={false}>
      <Text style={rcvCol.partNo}>{partNo}</Text>
      <Text style={rcvCol.description}>{description}</Text>
      <Text style={rcvCol.qtyOrd}>{item.quantity}</Text>
      <Text style={rcvCol.bo}>{item.qty_pending}</Text>
      {slot(0)}
      {slot(1)}
      {slot(2)}
      <Text style={rcvCol.unitPrice}>{formatCurrency(unitPrice)}</Text>
    </View>
  )
}

export function ReceivedPurchaseOrderPDF({
  po, receivings, project, companySettings,
}: ReceivedPurchaseOrderPDFProps) {
  const receiptIndex = buildReceiptIndex(receivings)

  // For the receiving-history section: resolve line descriptions and order receipts.
  const descById = new Map(po.line_items.map((i) => [i.id, getItemDescription(i)]))
  const historyReceivings = [...receivings.filter((r) => !r.voided_at)].sort(
    (a, b) => new Date(a.received_date).getTime() - new Date(b.received_date).getTime()
  )

  const orderedSubtotal = po.line_items.reduce(
    (sum, item) => sum + (item.unit_price ?? 0) * item.quantity, 0
  )
  // Use actual price where captured by a receipt, otherwise fall back to PO unit price.
  const receivedSubtotal = po.line_items.reduce((sum, item) => {
    const effectivePrice = item.actual_unit_price ?? item.unit_price ?? 0
    return sum + effectivePrice * item.qty_received
  }, 0)
  const hstRate = companySettings.hst_rate ?? 13.0
  const receivedHst = receivedSubtotal * (hstRate / 100)
  const receivedTotal = receivedSubtotal + receivedHst

  const orderDate = new Date(po.created_at).toLocaleDateString('en-CA', {
    day: '2-digit', month: 'short', year: 'numeric',
  })
  const expectedDelivery = po.expected_delivery_date
    ? new Date(po.expected_delivery_date).toLocaleDateString('en-CA', {
        day: '2-digit', month: 'short', year: 'numeric',
      })
    : null

  return (
    <Document>
      <Page size="LETTER" orientation="landscape" style={styles.pageLandscape}>
        {/* Header — full version on page 1 */}
        <PDFHeader companySettings={companySettings} title="RECEIVED PO">
          <View>
            <View style={styles.metaRow}>
              <Text style={styles.metaLabel}>PO #:</Text>
              <Text style={styles.metaValue}>{po.po_number}</Text>
            </View>
            {po.vendor_po_number && (
              <View style={styles.metaRow}>
                <Text style={styles.metaLabel}>Vendor PO #:</Text>
                <Text style={styles.metaValue}>{po.vendor_po_number}</Text>
              </View>
            )}
            <View style={styles.metaRow}>
              <Text style={styles.metaLabel}>Order Date:</Text>
              <Text style={styles.metaValue}>{orderDate}</Text>
            </View>
            {expectedDelivery && (
              <View style={styles.metaRow}>
                <Text style={styles.metaLabel}>Date Required:</Text>
                <Text style={styles.metaValue}>{expectedDelivery}</Text>
              </View>
            )}
            <View style={styles.metaRow}>
              <Text style={styles.metaLabel}>Status:</Text>
              <Text style={styles.metaValue}>{po.status}</Text>
            </View>
          </View>
        </PDFHeader>

        {/* Vendor */}
        <View style={styles.customerSection}>
          <Text style={styles.customerLabel}>VENDOR:</Text>
          <Text style={styles.customerName}>{po.vendor.name}</Text>
          {po.vendor.address && <Text style={styles.customerAddress}>{po.vendor.address}</Text>}
          {po.vendor.postal_code && <Text style={styles.customerAddress}>{po.vendor.postal_code}</Text>}
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

        {/* Work Description */}
        {po.work_description && (
          <View style={styles.workDescription}>
            <Text style={[styles.bold, { marginBottom: 2 }]}>Work Description:</Text>
            <Text>{po.work_description}</Text>
          </View>
        )}

        {/* Receiving Table */}
        <ReceivingTableHeader />
        {po.line_items.map((item, idx) => (
          <ReceivingLineRow
            key={item.id}
            item={item}
            receipts={receiptIndex.get(item.id) ?? []}
            alt={idx % 2 === 1}
          />
        ))}

        {/* Totals */}
        <View style={styles.totalsBlock}>
          <View style={styles.totalsRow}>
            <Text style={styles.totalsLabel}>Ordered Subtotal:</Text>
            <Text style={styles.totalsValue}>{formatCurrency(orderedSubtotal)}</Text>
          </View>
          <View style={styles.totalsRow}>
            <Text style={[styles.totalsLabel, styles.bold]}>Received Subtotal:</Text>
            <Text style={styles.totalsValue}>{formatCurrency(receivedSubtotal)}</Text>
          </View>
          {hstRate > 0 && (
            <View style={styles.totalsRow}>
              <Text style={styles.totalsLabel}>HST ({hstRate}%):</Text>
              <Text style={styles.totalsValue}>{formatCurrency(receivedHst)}</Text>
            </View>
          )}
          <View style={styles.grandTotalRow}>
            <Text style={[styles.totalsLabel, styles.bold]}>Received Total:</Text>
            <Text style={[styles.totalsValue, styles.bold]}>{formatCurrency(receivedTotal)}</Text>
          </View>
        </View>

        {/* Receiving History — what was received, when, and any notes */}
        {historyReceivings.length > 0 && (
          <View style={{ marginTop: 12 }}>
            <Text style={styles.sectionTitle}>Receiving History</Text>
            {historyReceivings.map((r, ri) => {
              const lines = r.line_items.filter((li) => (li.qty_received_this_receiving ?? 0) > 0)
              return (
                <View
                  key={ri}
                  style={{ marginBottom: 6, paddingBottom: 4, borderBottomWidth: 0.5, borderBottomColor: BORDER_COLOR }}
                  wrap={false}
                >
                  <Text style={[styles.bold, { fontSize: 8 }]}>{formatShortDate(r.received_date)}</Text>
                  {lines.map((li, li_i) => (
                    <Text key={li_i} style={{ fontSize: 8, marginLeft: 8 }}>
                      • {li.qty_received_this_receiving} × {li.po_line_item_id != null ? (descById.get(li.po_line_item_id) ?? 'Item') : 'Item'}
                    </Text>
                  ))}
                  {r.notes ? (
                    <Text style={[styles.italic, { fontSize: 8, marginTop: 2 }]}>Notes: {r.notes}</Text>
                  ) : null}
                </View>
              )
            })}
          </View>
        )}

        <PDFFooter leftText={`PO ${po.po_number} — Receiving Document`} />
      </Page>
    </Document>
  )
}
