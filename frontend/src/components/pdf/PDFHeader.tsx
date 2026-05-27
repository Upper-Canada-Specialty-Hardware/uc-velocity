import { View, Text, Image } from '@react-pdf/renderer'
import { styles } from './styles'
import type { CompanySettings } from '@/types'
import logo from '@/assets/logo.png'

interface PDFHeaderProps {
  companySettings: CompanySettings
  title: string  // "QUOTATION" | "INVOICE"
  /** Extra meta fields rendered in the right column below the title. */
  children?: React.ReactNode
}

export function PDFHeader({ companySettings, title, children }: PDFHeaderProps) {
  return (
    <>
      <View style={styles.headerRow}>
        {/* Left: Logo + Company Info */}
        <View style={styles.headerLeft}>
          <View style={styles.logoBox}>
            <Image src={companySettings.logo_data_url || logo} style={styles.logo} />
          </View>
          <View style={styles.companyInfo}>
            <Text style={styles.companyName}>{companySettings.name}</Text>
            {companySettings.address && <Text>{companySettings.address}</Text>}
            {companySettings.phone && <Text>Phone: {companySettings.phone}</Text>}
            {companySettings.fax && <Text>Fax: {companySettings.fax}</Text>}
            {companySettings.gst_number && <Text>GST #: {companySettings.gst_number}</Text>}
          </View>
        </View>

        {/* Right: Document title + meta fields */}
        <View style={styles.headerRight}>
          <Text style={styles.documentTitle}>{title}</Text>
          {children}
        </View>
      </View>
      <View style={styles.divider} />
    </>
  )
}
