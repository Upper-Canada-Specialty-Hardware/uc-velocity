/**
 * Invoicing ("Qty to Fulfill") helpers for the quote editor.
 *
 * A labour or misc line may be invoiced in fractions (e.g. 0.5 of a 1.5-hour
 * line), parts only in whole units; the per-line rules come from the shared
 * quantity helpers in `format.ts`. These helpers add the invoicing-specific
 * parts: the amount can never exceed what is still pending, the +/- buttons
 * move by 1 but stop at 0 and at the pending amount, and every result is
 * rounded to 2 decimals so float noise (0.49999999) never reaches the screen
 * or the Create Invoice request.
 */

import type { LineItemType } from "@/types"
import { allowsFractionalQuantity, formatQuantity, quantityInputError } from "@/lib/format"

// Most decimal places a quantity may carry (same as format.ts and the backend).
const FULFILLMENT_DECIMALS = 2

/**
 * Round a quantity to 2 decimals, dropping float noise.
 *
 * @param qty - The raw quantity (e.g. `1.5 - 1` computed in floats).
 * @returns The quantity as a clean 2-decimal number (`0.49999999` -> `0.5`).
 */
export function roundFulfillmentQuantity(qty: number): number {
  if (!Number.isFinite(qty)) return 0  // NaN/Infinity -> nothing staged
  return Number(qty.toFixed(FULFILLMENT_DECIMALS)) || 0  // `|| 0` turns -0 into 0
}

/**
 * Move a staged amount by one +/- step, kept between 0 and the pending amount.
 * With 1.5 pending: 0 -> 1 -> 1.5 going up, 1.5 -> 0.5 -> 0 going down.
 *
 * @param current - The amount staged now (0 when nothing is staged).
 * @param delta - The step to apply, `1` for the + button or `-1` for the - button.
 * @param pending - The line's Qty Pending, the most that can be invoiced.
 * @returns The new amount, rounded to 2 decimals; 0 means "unstage".
 */
export function stepFulfillmentQuantity(current: number, delta: number, pending: number): number {
  const max = Math.max(roundFulfillmentQuantity(pending), 0)  // cap at pending, never negative
  const next = roundFulfillmentQuantity(current + delta)  // step, then drop float noise
  return Math.min(Math.max(next, 0), max)  // clamp into [0, pending]
}

/**
 * Read half-typed stepper text as the starting point for a +/- click.
 * Out-of-range text is pulled back into [0, pending]; a part never starts from a
 * fraction (it is rounded down), so + and - on a part always land on whole units.
 *
 * @param raw - The stepper input's raw text (may be invalid, e.g. `"9"` or `"0.5"` on a part).
 * @param itemType - The quote line type.
 * @param pending - The line's Qty Pending.
 * @returns The starting amount, or 0 when the text is not a non-negative number.
 */
export function typedFulfillmentBase(raw: string, itemType: LineItemType, pending: number): number {
  const parsed = parseFloat(raw)  // lenient, same as the old stepper ("2abc" -> 2)
  if (Number.isNaN(parsed) || parsed < 0) return 0  // junk or negative -> start from nothing
  const capped = Math.min(parsed, Math.max(pending, 0))  // never above pending
  const whole = allowsFractionalQuantity(itemType) ? capped : Math.floor(capped)  // parts whole
  return roundFulfillmentQuantity(whole)  // clean 2-decimal start
}

/**
 * Whether a line's staged amount already covers everything still pending.
 * Compares after rounding, so `1.5` staged against a `1.4999999999` pending counts.
 *
 * @param staged - The staged amount, or undefined when nothing is staged.
 * @param pending - The line's Qty Pending.
 * @returns True when the + button (and "Fulfil all") has nothing left to add.
 */
export function isFullyStaged(staged: number | undefined, pending: number): boolean {
  if (staged === undefined) return false  // nothing staged yet
  return roundFulfillmentQuantity(staged) >= roundFulfillmentQuantity(pending)
}

/**
 * Explain why a typed "Qty to Fulfill" is not acceptable for this line.
 * Uses the same per-type rules as Qty Ordered (parts whole, labour/misc up to
 * 2 decimals), then checks the amount against what is still pending.
 *
 * @param raw - The stepper input's raw text (e.g. `"0.5"`).
 * @param itemType - The quote line type.
 * @param pending - The line's Qty Pending.
 * @returns A short user-facing message, or null when the value can be staged.
 */
export function fulfillmentInputError(raw: string, itemType: LineItemType, pending: number): string | null {
  const typeError = quantityInputError(raw, itemType)  // format, > 0, whole parts, 2 decimals
  if (typeError !== null) return typeError
  const value = roundFulfillmentQuantity(Number(raw.trim()))  // already validated as numeric
  if (value > roundFulfillmentQuantity(pending)) {  // compare after rounding, like the backend
    return `Cannot exceed Qty Pending (${formatQuantity(pending)})`
  }
  return null
}

/**
 * Whether the typed text means "stage nothing" (empty, or any spelling of zero).
 *
 * @param raw - The stepper input's raw text.
 * @returns True for `""`, `"0"`, `"0.0"`, `"0.00"` and similar.
 */
export function isClearFulfillmentInput(raw: string): boolean {
  const text = raw.trim()  // tolerate stray spaces
  if (text === "") return true  // emptied box -> unstage
  return /^0*\.?0*$/.test(text) && text !== "."  // only zeros (and one point) -> unstage
}
