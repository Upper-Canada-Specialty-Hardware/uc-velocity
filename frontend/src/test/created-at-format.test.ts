import { describe, it, expect } from 'vitest'
import { dateTimeLocalToIso, toDateTimeLocalValue, formatDateTime } from '@/lib/format'

describe('created-at datetime round-trip', () => {
  it('round-trips a local wall-clock value through ISO and back unchanged', () => {
    // The datetime-local input value the user picked (local wall clock, no offset).
    const localValue = '2026-03-15T09:30'

    // Convert to the UTC ISO string we send to the backend.
    const iso = dateTimeLocalToIso(localValue)
    expect(iso).not.toBeNull()
    // The ISO is a valid instant ending in Z (UTC).
    expect(iso as string).toMatch(/Z$/)

    // Converting that stored value back for the input yields the SAME local value,
    // regardless of the machine's timezone. This is the property that guarantees the
    // displayed/edited time does not silently shift by the browser offset.
    expect(toDateTimeLocalValue(iso as string)).toBe(localValue)
  })

  it('treats a backend naive-UTC timestamp (no offset) as UTC, not local', () => {
    // Backend serializes created_at as naive-UTC with no timezone suffix.
    const stored = '2026-03-15T14:30:00'
    // new Date(stored) would parse as LOCAL and shift; our helper appends Z first, so
    // the instant is fixed to 14:30 UTC. Re-deriving the same instant proves it.
    const local = toDateTimeLocalValue(stored)
    const iso = dateTimeLocalToIso(local)
    expect(new Date(iso as string).toISOString()).toBe('2026-03-15T14:30:00.000Z')
  })

  it('returns null / empty for blank input', () => {
    expect(dateTimeLocalToIso('')).toBeNull()
    expect(dateTimeLocalToIso(null)).toBeNull()
    expect(toDateTimeLocalValue('')).toBe('')
    expect(toDateTimeLocalValue(null)).toBe('')
  })

  it('formatDateTime renders a non-empty string for a valid timestamp and em-dash for missing', () => {
    expect(formatDateTime('2026-03-15T14:30:00')).not.toBe('—')
    expect(formatDateTime('')).toBe('—')
    expect(formatDateTime(null)).toBe('—')
  })
})
