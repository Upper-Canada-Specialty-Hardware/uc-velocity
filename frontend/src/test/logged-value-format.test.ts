import { describe, it, expect } from 'vitest'
import { formatDate, formatDateTime, formatLoggedValue } from '@/lib/format'

// The decisions page showed the cutover's logged timestamps raw, in UTC (#334).
describe('formatLoggedValue (decisions page dates)', () => {
  it('shows a Velocity timestamp in local time, like the rest of the app', () => {
    // Same instant through the shared formatter, so the test holds in any timezone.
    expect(formatLoggedValue('2026-07-17 13:15:19.444000')).toBe(formatDateTime('2026-07-17T13:15:19.444'))
  })

  it('shows a midnight timestamp or a bare date as just the day', () => {
    expect(formatLoggedValue('2026-07-17 00:00:00')).toBe('Jul 17, 2026')
    expect(formatLoggedValue('2026-07-17 00:00:00.000000', true)).toBe('Jul 17, 2026')
    expect(formatLoggedValue('2026-07-17')).toBe(formatDate('2026-07-17'))
  })

  it("keeps a time on Vision's side as-is, never shifted", () => {
    expect(formatLoggedValue('2026-07-17 13:15:19', true)).toBe('Jul 17, 2026, 13:15')
  })

  it('passes other text through and dashes missing values', () => {
    expect(formatLoggedValue('Draft')).toBe('Draft')
    expect(formatLoggedValue('12.5')).toBe('12.5')
    expect(formatLoggedValue(null)).toBe('—')
    expect(formatLoggedValue('')).toBe('—')
  })
})
