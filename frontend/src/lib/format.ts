/**
 * Shared formatting utilities so dates, currency, and empty values look
 * identical across the entire app. Adopting these is part of UX-3
 * (visual consistency).
 */

const dateFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
})

const dateTimeFormatter = new Intl.DateTimeFormat("en-CA", {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
})

// narrowSymbol → `$` instead of the default `CA$` for en-CA.
const currencyFormatter = new Intl.NumberFormat("en-CA", {
  style: "currency",
  currency: "CAD",
  currencyDisplay: "narrowSymbol",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

/** Render a date as `MMM d, yyyy` (e.g. `Jun 8, 2020`); em-dash for missing values. */
export function formatDate(value: string | Date | null | undefined): string {
  if (value == null || value === "") return "—"
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return "—"
  return dateFormatter.format(date)
}

/**
 * Render a stored timestamp as local date + time (e.g. `Jun 8, 2020, 3:30 p.m.`).
 *
 * Backend timestamps are naive-UTC and serialized without a timezone suffix, so we
 * append `Z` before parsing to force UTC interpretation; the formatter then renders
 * them in the viewer's local timezone. em-dash for missing values.
 */
export function formatDateTime(value: string | Date | null | undefined): string {
  if (value == null || value === "") return EMPTY_VALUE
  const date = value instanceof Date ? value : new Date(asUtcIso(value))
  if (Number.isNaN(date.getTime())) return EMPTY_VALUE
  return dateTimeFormatter.format(date)
}

/**
 * Normalize a backend timestamp string to an unambiguous UTC ISO string.
 *
 * Backend created_at values are naive-UTC and may arrive without a timezone (e.g.
 * `2026-06-17T14:30:00`). A bare `new Date(...)` of that string is interpreted as
 * LOCAL time by JS, shifting it by the browser offset. Appending `Z` (when no offset
 * is already present) pins it to UTC so every conversion stays consistent.
 */
function asUtcIso(value: string): string {
  // Already has a timezone (Z or ±hh:mm)? leave it.
  if (/[zZ]$|[+-]\d{2}:?\d{2}$/.test(value)) return value
  return `${value}Z`
}

/**
 * Convert a stored UTC timestamp into the `YYYY-MM-DDTHH:mm` value a
 * `<input type="datetime-local">` expects, expressed in the viewer's local time.
 */
export function toDateTimeLocalValue(value: string | null | undefined): string {
  if (value == null || value === "") return ""
  const date = new Date(asUtcIso(value))
  if (Number.isNaN(date.getTime())) return ""
  // Build local wall-clock components (the input is local time).
  const pad = (n: number) => String(n).padStart(2, "0")
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  )
}

/**
 * Convert a `datetime-local` input value (local wall-clock, no timezone) into a UTC
 * ISO-8601 string with a `Z` suffix, ready to send to the backend. Returns null for
 * empty/invalid input.
 */
export function dateTimeLocalToIso(localValue: string | null | undefined): string | null {
  if (!localValue) return null
  // `new Date("YYYY-MM-DDTHH:mm")` parses as LOCAL time (per the spec for that form),
  // which is exactly what we want: the user typed local wall-clock. toISOString() then
  // yields the equivalent UTC instant.
  const date = new Date(localValue)
  if (Number.isNaN(date.getTime())) return null
  return date.toISOString()
}

/** Render a number as CAD currency with thousand separators (e.g. `$1,234.56`). */
export function formatCurrency(amount: number): string {
  return currencyFormatter.format(amount)
}

/** The single empty-value glyph used everywhere a read-only value is missing. */
export const EMPTY_VALUE = "—"

/** Title-case a status string for display (e.g. `archived` -> `Archived`, `work_order` -> `Work Order`). */
export function titleCaseStatus(value: string): string {
  if (!value) return EMPTY_VALUE
  return value
    .replace(/[_-]+/g, " ")
    .split(" ")
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(" ")
}
