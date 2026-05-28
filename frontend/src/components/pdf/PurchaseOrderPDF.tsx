import { Document, Page, View, Text } from '@react-pdf/renderer'
import { styles } from './styles'
import { PDFHeader } from './PDFHeader'
import { PDFFooter } from './PDFFooter'
import { formatCurrency } from '@/lib/pricing'
import type { PurchaseOrder, POLineItem, Project, CompanySettings } from '@/types'

interface PurchaseOrderPDFProps {
  po: PurchaseOrder
  project: Project
  companySettings: CompanySettings
  includeBreakdown?: boolean
}

function LineItemTable({
  title,
  items,
  showLineDetails,
}: {
  title: string
  items: POLineItem[]
  showLineDetails: boolean
}) {
  if (items.length === 0) return null

  // Quantities-only mode: list each item's description + qty, with no pricing
  // and no section subtotal (only the final Subtotal/HST/Total appear at the bottom).
  if (!showLineDetails) {
    return (
      <>
        <Text style={styles.sectionTitle}>{title}</Text>
        <View style={styles.tableHeader}>
          <Text style={[styles.colHeaderText, styles.colDescriptionWide]}>Description</Text>
          <Text style={[styles.colHeaderText, styles.colQtyWide]}>Qty</Text>
        </View>
        {items.map((item, idx) => (
          <View
            key={item.id}
            style={[styles.tableRow, idx % 2 === 1 ? styles.tableRowAlt : {}]}
            wrap={false}
          >
            <Text style={styles.colDescriptionWide}>{getItemDescription(item)}</Text>
            <Text style={styles.colQtyWide}>{item.quantity}</Text>
          </View>
        ))}
      </>
    )
  }

  const sectionTotal = items.reduce(
    (sum, item) => sum + (item.unit_price ?? 0) * item.quantity,
    0
  )

  return (
    <>
      <Text style={styles.sectionTitle}>{title}</Text>

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
        const unitPrice = item.unit_price ?? 0
        const total = unitPrice * item.quantity

        return (
          <View
            key={item.id}
            style={[styles.tableRow, idx % 2 === 1 ? styles.tableRowAlt : {}]}
            wrap={false}
          >
            <Text style={styles.colDescription}>{description}</Text>
            <Text style={styles.colQty}>{item.quantity}</Text>
            <Text style={styles.colUnitPrice}>{formatCurrency(unitPrice)}</Text>
            <Text style={styles.colTotal}>{formatCurrency(total)}</Text>
          </View>
        )
      })}

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

function getItemDescription(item: POLineItem): string {
  if (item.item_type === 'part' && item.part)
    return `${item.part.part_number} - ${item.part.description}`
  return item.description || 'Unknown item'
}

export function PurchaseOrderPDF({ po, project, companySettings, includeBreakdown = true }: PurchaseOrderPDFProps) {
  const partItems = po.line_items.filter(i => i.item_type === 'part')
  const miscItems = po.line_items.filter(i => i.item_type === 'misc')

  const subtotal = po.line_items.reduce(
    (sum, item) => sum + (item.unit_price ?? 0) * item.quantity,
    0
  )
  const hstRate = companySettings.hst_rate ?? 13.0
  const hstAmount = subtotal * (hstRate / 100)
  const grandTotal = subtotal + hstAmount

  const formattedDate = new Date(po.created_at).toLocaleDateString('en-CA', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  })

  const formattedDelivery = po.expected_delivery_date
    ? new Date(po.expected_delivery_date).toLocaleDateString('en-CA', {
        day: '2-digit',
        month: 'short',
        year: 'numeric',
      })
    : null

  return (
    <Document>
      <Page size="LETTER" style={styles.page}>
        {/* Header */}
        <PDFHeader companySettings={companySettings} title="PURCHASE ORDER">
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
              <Text style={styles.metaLabel}>Date:</Text>
              <Text style={styles.metaValue}>{formattedDate}</Text>
            </View>
            {formattedDelivery && (
              <View style={styles.metaRow}>
                <Text style={styles.metaLabel}>Expected Delivery:</Text>
                <Text style={styles.metaValue}>{formattedDelivery}</Text>
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
          <Text style={styles.customerAddress}>{po.vendor.address}</Text>
          <Text style={styles.customerAddress}>{po.vendor.postal_code}</Text>
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
        {po.work_description && (
          <View style={styles.workDescription}>
            <Text style={[styles.bold, { marginBottom: 2 }]}>Work Description:</Text>
            <Text>{po.work_description}</Text>
          </View>
        )}

        {/* Line Items by Section */}
        <LineItemTable title="Parts" items={partItems} showLineDetails={includeBreakdown} />
        <LineItemTable title="Miscellaneous" items={miscItems} showLineDetails={includeBreakdown} />

        {/* Totals */}
        <View style={styles.totalsBlock}>
          <View style={styles.totalsRow}>
            <Text style={[styles.totalsLabel, styles.bold]}>SUBTOTAL:</Text>
            <Text style={styles.totalsValue}>{formatCurrency(subtotal)}</Text>
          </View>
          {hstRate > 0 && (
            <View style={styles.totalsRow}>
              <Text style={styles.totalsLabel}>HST ({hstRate}%):</Text>
              <Text style={styles.totalsValue}>{formatCurrency(hstAmount)}</Text>
            </View>
          )}
          <View style={styles.grandTotalRow}>
            <Text style={[styles.totalsLabel, styles.bold]}>TOTAL:</Text>
            <Text style={[styles.totalsValue, styles.bold]}>{formatCurrency(grandTotal)}</Text>
          </View>
        </View>

        <PDFFooter />
      </Page>
    </Document>
  )
}
