/**
 * Shared formatting utilities so dates, currency, and empty values look
 * identical across the entire app. Adopting these is part of UX-3
 * (visual consistency).
 */

import type { LineItemType } from "@/types"

const dateFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
})

// Same shape as dateFormatter but pinned to UTC. Used for date-ONLY values
// (`YYYY-MM-DD`, e.g. invoice_date): JS parses those as UTC midnight, so rendering
// them in the local zone would shift the calendar date back a day in west-of-UTC
// zones (e.g. Eastern). Formatting in UTC prints exactly the stored calendar date.
const dateOnlyFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
  timeZone: "UTC",
})

// A bare `YYYY-MM-DD` calendar date (no time component).
const DATE_ONLY_RE = /^\d{4}-\d{2}-\d{2}$/

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

/**
 * Render a date as `MMM d, yyyy` (e.g. `Jun 8, 2020`); em-dash for missing values.
 *
 * Two input shapes are handled distinctly so neither shifts by a day (issue #226):
 * - a date-ONLY string (`YYYY-MM-DD`) is a calendar date with no timezone -> print
 *   its components as-is (formatted in UTC), never converted to local;
 * - a full timestamp (backend `created_at` is naive-UTC, no suffix) is a UTC instant
 *   -> pin it to UTC (`asUtcIso`) then render in the viewer's local zone, so the
 *   local calendar date is correct (e.g. `2026-08-21T03:24Z` -> Aug 20 in Eastern).
 * A bare `new Date(value)` mishandled BOTH: it read a timestamp as local (wrong date
 * after ~8pm ET) and a date-only string as UTC-midnight-then-local (a day early).
 */
export function formatDate(value: string | Date | null | undefined): string {
  if (value == null || value === "") return "—"
  if (value instanceof Date) {                      // already an instant -> local calendar date
    return Number.isNaN(value.getTime()) ? "—" : dateFormatter.format(value)
  }
  if (DATE_ONLY_RE.test(value)) {                   // calendar date -> no timezone shift
    const date = new Date(`${value}T00:00:00Z`)     // parse the components at UTC midnight
    return Number.isNaN(date.getTime()) ? "—" : dateOnlyFormatter.format(date)
  }
  const date = new Date(asUtcIso(value))            // naive-UTC timestamp -> local calendar date
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

// ===== Quote quantities =====
// Labour and misc lines may be ordered in fractions (e.g. 1.5 hours) with at most
// 2 decimals; parts stay whole. The backend enforces the same rules (a fractional
// part is refused with 400, a 3rd decimal with 422), so these helpers let the
// editor refuse bad input up front instead of letting Commit fail.

// Most decimal places a quote quantity may carry (matches the backend).
const QUANTITY_DECIMALS = 2
// Float slack: 0.1 + 0.2 is 0.30000000000000004, still "0.3" (same tolerance as backend)
const QUANTITY_EPSILON = 1e-9
// Plain unsigned decimal as typed: digits, optional point, optional digits
const QUANTITY_INPUT_RE = /^\d*\.?\d*$/

/**
 * Render a quantity cleanly: whole values without decimals, fractions with up to
 * 2 decimals and no trailing zeros (2 -> "2", 1.5 -> "1.5", 0.25 -> "0.25").
 *
 * @param qty - The quantity to display; null/undefined/NaN count as 0.
 * @returns The display string, never float noise such as `0.30000000000000004`.
 */
export function formatQuantity(qty: number | null | undefined): string {
  if (qty == null || !Number.isFinite(qty)) return "0"  // missing -> "0", like an empty count
  // toFixed(2) rounds off float noise; Number() then drops trailing zeros (and -0)
  return String(Number(qty.toFixed(QUANTITY_DECIMALS)) || 0)
}

/**
 * Whether a line of this type may be ordered in fractions.
 *
 * @param itemType - The quote line type.
 * @returns True for labour and misc lines, false for parts.
 */
export function allowsFractionalQuantity(itemType: LineItemType): boolean {
  return itemType !== "part"  // parts are physical units -> whole numbers only
}

/**
 * The `step` (and `min`) for a quantity `<input type="number">` of this line type.
 *
 * @param itemType - The quote line type.
 * @returns `"0.01"` for labour/misc, `"1"` for parts.
 */
export function quantityStep(itemType: LineItemType): string {
  return allowsFractionalQuantity(itemType) ? "0.01" : "1"
}

/**
 * Whether a numeric quantity is acceptable for a line of this type.
 * Parts: a whole number of at least 1. Labour/misc: above 0, at most 2 decimals.
 *
 * @param value - The quantity to check.
 * @param itemType - The quote line type.
 * @returns True when the backend would accept this quantity for this line type.
 */
export function isValidQuantity(value: number, itemType: LineItemType): boolean {
  if (!Number.isFinite(value)) return false  // NaN/Infinity never valid
  const rounded = Number(value.toFixed(QUANTITY_DECIMALS))  // snap to 2 decimals
  if (Math.abs(rounded - value) >= QUANTITY_EPSILON) return false  // a real 3rd decimal
  if (rounded <= 0) return false  // ordered amounts must be positive
  if (!allowsFractionalQuantity(itemType)) return Number.isInteger(rounded)  // parts whole (>= 1 follows)
  return true
}

/**
 * Explain why a typed quantity is not acceptable, for inline form messages.
 *
 * @param raw - The input's raw text (e.g. `"1.5"`, `"0.0"`, `""`).
 * @param itemType - The quote line type.
 * @returns A short user-facing message, or null when the value is acceptable.
 */
export function quantityInputError(raw: string, itemType: LineItemType): string | null {
  const text = raw.trim()  // tolerate stray spaces
  if (text === "" || text === ".") return "Enter a quantity"  // nothing numeric yet
  if (!QUANTITY_INPUT_RE.test(text)) return "Enter a valid number"  // signs, letters, exponents
  const value = Number(text)  // "1." -> 1, ".5" -> 0.5
  if (!(value > 0)) return "Quantity must be greater than 0"
  if (!allowsFractionalQuantity(itemType) && !Number.isInteger(value)) {
    return "Parts must be ordered in whole numbers"  // same wording as the backend 400
  }
  if (!isValidQuantity(value, itemType)) return "Quantity can have at most 2 decimal places"
  return null
}

/**
 * Parse a typed quantity for a line of this type.
 *
 * @param raw - The input's raw text.
 * @param itemType - The quote line type.
 * @returns The quantity rounded to 2 decimals, or null when the text is not acceptable.
 */
export function parseQuantityInput(raw: string, itemType: LineItemType): number | null {
  if (quantityInputError(raw, itemType) !== null) return null  // reject, caller keeps last good value
  return Number(Number(raw.trim()).toFixed(QUANTITY_DECIMALS))  // clean 2-decimal number
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
