/**
 * Shared formatting utilities so dates and empty values look identical across
 * the entire app. Adopting these is part of UX-3 (visual consistency).
 */

const dateFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
})

/** Render a date as `MMM d, yyyy` (e.g. `Jun 8, 2020`); em-dash for missing values. */
export function formatDate(value: string | Date | null | undefined): string {
  if (value == null || value === "") return "—"
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return "—"
  return dateFormatter.format(date)
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
