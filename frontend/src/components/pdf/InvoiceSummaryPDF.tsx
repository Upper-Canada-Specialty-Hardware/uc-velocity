import { Document, Page, View, Text } from '@react-pdf/renderer'
import { styles } from './styles'
import { PDFFooter } from './PDFFooter'
import { formatCurrency } from '@/lib/pricing'
import type { InvoiceSummaryItem, CompanySettings, ProjectListView } from '@/types'

interface InvoiceSummaryPDFProps {
  invoices: InvoiceSummaryItem[]
  dateRange: { start: string; end: string }
  companySettings: CompanySettings
  project?: ProjectListView | null
}

// Column widths for the summary table
const col = {
  invoiceNum: { width: '8%' } as const,
  invoiceDate: { width: '11%' } as const,
  ucaProject: { width: '10%' } as const,
  poNumber: { width: '10%' } as const,
  customer: { width: '14%' } as const,
  project: { width: '14%' } as const,
  netSales: { width: '11%', textAlign: 'right' as const },
  hst: { width: '11%', textAlign: 'right' as const },
  total: { width: '11%', textAlign: 'right' as const },
}

export function InvoiceSummaryPDF({ invoices, dateRange, companySettings, project }: InvoiceSummaryPDFProps) {
  const totals = invoices.reduce(
    (acc, inv) => ({
      netSales: acc.netSales + inv.net_sales,
      hst: acc.hst + inv.hst_amount,
      total: acc.total + inv.grand_total,
    }),
    { netSales: 0, hst: 0, total: 0 }
  )

  const formatDate = (dateStr: string) =>
    new Date(dateStr).toLocaleDateString('en-CA', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    })

  return (
    <Document>
      <Page size="LETTER" orientation="landscape" style={styles.pageLandscape}>
        {/* Title */}
        <Text style={styles.reportTitle}>WorkOrder Invoice Report</Text>
        <Text style={styles.reportSubtitle}>
          {companySettings.name} — Invoice Report
          {project ? ` — ${project.uca_project_number} ${project.name}` : ''}
          {' — '}{formatDate(dateRange.start)} to {formatDate(dateRange.end)}
        </Text>

        <View style={styles.divider} />

        {/* Table Header */}
        <View style={styles.tableHeader}>
          <Text style={[styles.colHeaderText, col.invoiceNum]}>Invoice #</Text>
          <Text style={[styles.colHeaderText, col.invoiceDate]}>Invoice Date</Text>
          <Text style={[styles.colHeaderText, col.ucaProject]}>UCA Project #</Text>
          <Text style={[styles.colHeaderText, col.poNumber]}>P/O Number</Text>
          <Text style={[styles.colHeaderText, col.customer]}>Customer</Text>
          <Text style={[styles.colHeaderText, col.project]}>Project</Text>
          <Text style={[styles.colHeaderText, col.netSales]}>Net Sales</Text>
          <Text style={[styles.colHeaderText, col.hst]}>HST</Text>
          <Text style={[styles.colHeaderText, col.total]}>Total</Text>
        </View>

        {/* Table Rows */}
        {invoices.map((inv, idx) => (
          <View
            key={inv.invoice_id}
            style={[styles.tableRow, idx % 2 === 1 ? styles.tableRowAlt : {}]}
            wrap={false}
          >
            <Text style={col.invoiceNum}>{inv.invoice_id}</Text>
            <Text style={col.invoiceDate}>{formatDate(inv.invoice_date)}</Text>
            <Text style={col.ucaProject}>{inv.uca_project_number}</Text>
            <Text style={col.poNumber}>{inv.client_po_number || ''}</Text>
            <Text style={col.customer}>{inv.customer_name}</Text>
            <Text style={col.project}>{inv.project_name}</Text>
            <Text style={col.netSales}>{formatCurrency(inv.net_sales)}</Text>
            <Text style={col.hst}>{formatCurrency(inv.hst_amount)}</Text>
            <Text style={col.total}>{formatCurrency(inv.grand_total)}</Text>
          </View>
        ))}

        {/* Totals Row */}
        <View style={styles.tableFooterRow}>
          <Text style={[col.invoiceNum, styles.bold]}>Totals</Text>
          <Text style={col.invoiceDate} />
          <Text style={col.ucaProject} />
          <Text style={col.poNumber} />
          <Text style={col.customer} />
          <Text style={col.project} />
          <Text style={[col.netSales, styles.bold]}>{formatCurrency(totals.netSales)}</Text>
          <Text style={[col.hst, styles.bold]}>{formatCurrency(totals.hst)}</Text>
          <Text style={[col.total, styles.bold]}>{formatCurrency(totals.total)}</Text>
        </View>

        <PDFFooter />
      </Page>
    </Document>
  )
}
