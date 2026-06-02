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
