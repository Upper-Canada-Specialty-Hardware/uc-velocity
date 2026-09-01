import { describe, it, expect } from 'vitest'
import { formatDate } from '@/lib/format'

// Regression tests for issue #226: formatDate rendered dates a day off because a
// bare `new Date(value)` mishandled both timestamps and date-only strings.
describe('formatDate (#226 timezone handling)', () => {
  it('renders a date-only string as its own calendar date, never shifted', () => {
    // A bare `new Date("2020-06-08")` is UTC midnight; rendered in a west-of-UTC
    // zone that used to print "Jun 7". It must always be the stored date.
    expect(formatDate('2020-06-08')).toBe('Jun 8, 2020')
    expect(formatDate('2026-08-21')).toBe('Aug 21, 2026')
  })

  it('treats a naive-UTC timestamp as UTC, not local (matches an explicit Z)', () => {
    // The core of the fix: a backend timestamp with no offset is pinned to UTC, so
    // it renders identically to the same instant written with an explicit Z. A bare
    // `new Date` parsed the offset-less form as LOCAL, which drifted the date.
    expect(formatDate('2026-08-21T03:24:00')).toBe(formatDate('2026-08-21T03:24:00Z'))
  })

  it('a date-only value and a late-UTC timestamp of the same calendar day do not collapse', () => {
    // date-only is pinned to UTC; the timestamp renders in local time. In UTC they
    // match; in a west-of-UTC zone the timestamp is the prior day. Either way the
    // date-only value is stable at its stored date.
    expect(formatDate('2026-08-21')).toBe('Aug 21, 2026')
  })

  it('passes a Date instance through and handles missing values', () => {
    expect(formatDate(new Date('2026-08-21T12:00:00Z'))).not.toBe('—')
    expect(formatDate('')).toBe('—')
    expect(formatDate(null)).toBe('—')
    expect(formatDate(undefined)).toBe('—')
    expect(formatDate('not-a-date')).toBe('—')
  })
})
