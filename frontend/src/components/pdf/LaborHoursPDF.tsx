import { Document, Page, View, Text, StyleSheet } from '@react-pdf/renderer'
import { styles } from './styles'
import { PDFHeader } from './PDFHeader'
import { PDFFooter } from './PDFFooter'
import { formatCurrency } from '@/lib/pricing'
import { computeLaborHoursReport } from '@/lib/laborHours'
import type { Quote, Project, CompanySettings } from '@/types'

interface LaborHoursPDFProps {
  quote: Quote
  project: Project
  companySettings: CompanySettings
}

/** Column widths for this report only; the shared quote/invoice columns don't fit it. */
const lh = StyleSheet.create({
  colDescription: { width: '52%' },
  colQty: { width: '10%', textAlign: 'right' },
  colHrsPerUnit: { width: '14%', textAlign: 'right' },
  colTime: { width: '12%', textAlign: 'right' },
  colCost: { width: '12%', textAlign: 'right' },
  summaryHeader: { width: '28%' },
  summaryCol: { width: '24%', textAlign: 'right' },
  note: {
    marginTop: 10,
    fontSize: 7,
    color: '#666666',
    lineHeight: 1.4,
  },
})

function formatHours(hours: number): string {
  return `${hours.toFixed(1)} hr.`
}

export function LaborHoursPDF({ quote, project, companySettings }: LaborHoursPDFProps) {
  const { rows, total_hours, estimated_cost, rate_display, unresolved_count } =
    computeLaborHoursReport(quote.line_items)

  const formattedDate = new Date(quote.created_at).toLocaleDateString('en-CA', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  })

  return (
    <Document>
      <Page size="LETTER" style={styles.page}>
        <PDFHeader companySettings={companySettings} title="LABOUR HOURS">
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

        {/* Per-item time */}
        <Text style={styles.sectionTitle}>Calculated Labour Hours by Item</Text>
        <View style={styles.tableHeader}>
          <Text style={[styles.colHeaderText, lh.colDescription]}>Description</Text>
          <Text style={[styles.colHeaderText, lh.colQty]}>Qty</Text>
          <Text style={[styles.colHeaderText, lh.colHrsPerUnit]}>Hrs / Unit</Text>
          <Text style={[styles.colHeaderText, lh.colTime]}>Time</Text>
          <Text style={[styles.colHeaderText, lh.colCost]}>Est. Cost</Text>
        </View>

        {rows.map((row, idx) => (
          <View
            key={row.id}
            style={[styles.tableRow, idx % 2 === 1 ? styles.tableRowAlt : {}]}
            wrap={false}
          >
            <Text style={lh.colDescription}>
              {row.description}
              {row.unresolved ? ' *' : ''}
            </Text>
            <Text style={lh.colQty}>{row.quantity}</Text>
            <Text style={lh.colHrsPerUnit}>{row.unresolved ? '—' : row.hours_per_unit.toFixed(1)}</Text>
            <Text style={lh.colTime}>{row.unresolved ? '—' : formatHours(row.time)}</Text>
            <Text style={lh.colCost}>{row.unresolved ? '—' : formatCurrency(row.cost)}</Text>
          </View>
        ))}

        {/* Total row */}
        <View style={styles.tableFooterRow}>
          <Text style={[lh.colDescription, styles.bold]}>Total Calculated Labour Hours</Text>
          <Text style={lh.colQty} />
          <Text style={lh.colHrsPerUnit} />
          <Text style={[lh.colTime, styles.bold]}>{formatHours(total_hours)}</Text>
          <Text style={[lh.colCost, styles.bold]}>{formatCurrency(estimated_cost)}</Text>
        </View>

        {/* Estimate / Actual / Variance summary */}
        <Text style={styles.sectionTitle}>Labour Summary</Text>
        <View style={styles.tableHeader}>
          <Text style={[styles.colHeaderText, lh.summaryHeader]} />
          <Text style={[styles.colHeaderText, lh.summaryCol]}>Estimate</Text>
          <Text style={[styles.colHeaderText, lh.summaryCol]}>Actual</Text>
          <Text style={[styles.colHeaderText, lh.summaryCol]}>Variance</Text>
        </View>
        <View style={styles.tableRow}>
          <Text style={[lh.summaryHeader, styles.bold]}>Labour Hours</Text>
          <Text style={lh.summaryCol}>{formatHours(total_hours)}</Text>
          <Text style={lh.summaryCol}>—</Text>
          <Text style={lh.summaryCol}>—</Text>
        </View>
        <View style={[styles.tableRow, styles.tableRowAlt]}>
          <Text style={[lh.summaryHeader, styles.bold]}>Rate</Text>
          <Text style={lh.summaryCol}>{rate_display}</Text>
          <Text style={lh.summaryCol}>—</Text>
          <Text style={lh.summaryCol}>—</Text>
        </View>
        <View style={styles.tableRow}>
          <Text style={[lh.summaryHeader, styles.bold]}>Cost (Estimated)</Text>
          <Text style={lh.summaryCol}>{formatCurrency(estimated_cost)}</Text>
          <Text style={lh.summaryCol}>—</Text>
          <Text style={lh.summaryCol}>—</Text>
        </View>

        <View style={lh.note}>
          <Text>
            Time = Qty x Hrs / Unit. Only labour items carry hours; parts and miscellaneous
            items show 0.0 hr. Est. Cost is the labour cost before markup (Time x Rate).
          </Text>
          <Text>
            Actual and Variance are not tracked in UC Velocity and are left blank for manual entry.
          </Text>
          {unresolved_count > 0 && (
            <Text>
              * {unresolved_count} labour line{unresolved_count === 1 ? '' : 's'} could not be
              resolved to a labour record; hours are unknown and are excluded from the totals above.
            </Text>
          )}
        </View>

        <PDFFooter />
      </Page>
    </Document>
  )
}
