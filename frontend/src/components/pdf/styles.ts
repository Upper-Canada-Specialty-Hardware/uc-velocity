import { StyleSheet } from '@react-pdf/renderer'

/** Dark maroon/red accent matching the reference images. */
export const ACCENT_COLOR = '#8B0000'
export const BORDER_COLOR = '#999999'
export const LIGHT_BG = '#F5F5F5'
export const HEADER_BG = '#E8E8E8'

export const styles = StyleSheet.create({
  // ---- Page ----
  page: {
    padding: 40,
    fontSize: 9,
    fontFamily: 'Helvetica',
    color: '#333333',
  },
  pageLandscape: {
    padding: 40,
    fontSize: 8,
    fontFamily: 'Helvetica',
    color: '#333333',
  },

  // ---- Header ----
  headerRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: 4,
  },
  headerLeft: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 10,
  },
  logoBox: {
    width: 120,
    height: 50,
    justifyContent: 'center',
    alignItems: 'flex-start',
  },
  logo: {
    width: '100%',
    height: '100%',
    objectFit: 'contain',
  },
  companyInfo: {
    fontSize: 7,
    lineHeight: 1.4,
  },
  companyName: {
    fontSize: 12,
    fontFamily: 'Helvetica-Bold',
    marginBottom: 2,
  },
  headerRight: {
    alignItems: 'flex-end',
  },
  documentTitle: {
    fontSize: 18,
    fontFamily: 'Helvetica-Bold',
    color: ACCENT_COLOR,
    marginBottom: 4,
  },

  // ---- Divider ----
  divider: {
    borderBottomWidth: 2,
    borderBottomColor: ACCENT_COLOR,
    marginVertical: 6,
  },
  thinDivider: {
    borderBottomWidth: 0.5,
    borderBottomColor: BORDER_COLOR,
    marginVertical: 4,
  },

  // ---- Meta fields ----
  metaRow: {
    flexDirection: 'row',
    marginBottom: 2,
  },
  metaLabel: {
    fontFamily: 'Helvetica-Bold',
    fontSize: 8,
    width: 100,
  },
  metaValue: {
    fontSize: 8,
  },

  // ---- Customer block ----
  customerSection: {
    marginBottom: 8,
    padding: 6,
    backgroundColor: LIGHT_BG,
    borderWidth: 0.5,
    borderColor: BORDER_COLOR,
  },
  customerLabel: {
    fontSize: 8,
    fontFamily: 'Helvetica-Bold',
    marginBottom: 2,
    color: ACCENT_COLOR,
  },
  customerName: {
    fontSize: 10,
    fontFamily: 'Helvetica-Bold',
    marginBottom: 1,
  },
  customerAddress: {
    fontSize: 8,
  },

  // ---- Project info ----
  projectRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: 6,
    padding: 4,
    backgroundColor: LIGHT_BG,
    borderWidth: 0.5,
    borderColor: BORDER_COLOR,
  },
  projectField: {
    flexDirection: 'row',
    gap: 4,
  },

  // ---- Work Description ----
  workDescription: {
    marginBottom: 8,
    padding: 6,
    borderWidth: 0.5,
    borderColor: BORDER_COLOR,
    fontSize: 8,
    lineHeight: 1.4,
  },

  // ---- Table ----
  sectionTitle: {
    fontSize: 10,
    fontFamily: 'Helvetica-Bold',
    marginBottom: 4,
    marginTop: 8,
    color: ACCENT_COLOR,
  },
  tableHeader: {
    flexDirection: 'row',
    backgroundColor: HEADER_BG,
    borderTopWidth: 1,
    borderBottomWidth: 1,
    borderColor: BORDER_COLOR,
    paddingVertical: 3,
    paddingHorizontal: 4,
  },
  tableRow: {
    flexDirection: 'row',
    borderBottomWidth: 0.5,
    borderBottomColor: '#DDDDDD',
    paddingVertical: 2,
    paddingHorizontal: 4,
    minHeight: 14,
  },
  tableRowAlt: {
    backgroundColor: '#FAFAFA',
  },
  tableFooterRow: {
    flexDirection: 'row',
    backgroundColor: HEADER_BG,
    borderTopWidth: 1,
    borderBottomWidth: 1,
    borderColor: BORDER_COLOR,
    paddingVertical: 3,
    paddingHorizontal: 4,
    fontFamily: 'Helvetica-Bold',
  },
  // Column widths (for quote/invoice tables)
  colDescription: { width: '50%' },
  colQty: { width: '10%', textAlign: 'right' },
  colUnitPrice: { width: '20%', textAlign: 'right' },
  colTotal: { width: '20%', textAlign: 'right' },
  // Column header text
  colHeaderText: {
    fontFamily: 'Helvetica-Bold',
    fontSize: 8,
  },

  // ---- Totals block ----
  totalsBlock: {
    marginTop: 10,
    alignItems: 'flex-end',
  },
  totalsRow: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    width: 220,
    paddingVertical: 2,
  },
  totalsLabel: {
    width: 120,
    textAlign: 'right',
    paddingRight: 10,
    fontSize: 9,
  },
  totalsValue: {
    width: 100,
    textAlign: 'right',
    fontSize: 9,
  },
  totalsBold: {
    fontFamily: 'Helvetica-Bold',
  },
  grandTotalRow: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    width: 220,
    paddingVertical: 3,
    borderTopWidth: 1.5,
    borderTopColor: ACCENT_COLOR,
    marginTop: 2,
  },

  // ---- Payment terms ----
  paymentTerms: {
    marginTop: 12,
    fontSize: 8,
    lineHeight: 1.4,
  },
  paymentTermsBold: {
    fontFamily: 'Helvetica-Bold',
    fontSize: 8,
  },

  // ---- Signature ----
  signatureBlock: {
    marginTop: 30,
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  signatureLine: {
    width: 200,
    borderBottomWidth: 1,
    borderBottomColor: '#333333',
    paddingBottom: 2,
    fontSize: 8,
  },

  // ---- Footer ----
  footer: {
    position: 'absolute',
    bottom: 20,
    left: 40,
    right: 40,
    fontSize: 7,
    color: '#999999',
    flexDirection: 'row',
    justifyContent: 'space-between',
  },

  // ---- Summary report (landscape) ----
  reportTitle: {
    fontSize: 16,
    fontFamily: 'Helvetica-Bold',
    color: ACCENT_COLOR,
    marginBottom: 2,
  },
  reportSubtitle: {
    fontSize: 8,
    color: '#666666',
    marginBottom: 10,
  },

  // ---- Bold / italic helpers ----
  bold: { fontFamily: 'Helvetica-Bold' },
  italic: { fontFamily: 'Helvetica-Oblique' },
  textRight: { textAlign: 'right' },
  textCenter: { textAlign: 'center' },
})
