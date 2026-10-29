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
 *
 * A credit line (negative quantity, issue #277) is invoiced toward zero with a
 * negative amount: Qty Pending is negative, the staged amount shares that sign,
 * and "how much is left" and "fully staged" are judged by MAGNITUDE. The + / -
 * buttons grow / shrink that magnitude (−2 -> −3 on +, −3 -> 0 on -), so the
 * same control serves a credit and a normal line; only the sign differs.
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
 * The step moves the MAGNITUDE toward |pending|, then reattaches the pending's
 * sign, so a credit (#277) steps the same way: with 1.5 pending 0 -> 1 -> 1.5 and
 * back; with -5 pending 0 -> -1 ... -> -5 on +, and back toward 0 on -.
 *
 * @param current - The amount staged now (0 when nothing is staged; negative on a credit).
 * @param delta - The step magnitude, `1` for the + button or `-1` for the - button.
 * @param pending - The line's Qty Pending (negative on a credit); the most that can be invoiced.
 * @returns The new amount, rounded to 2 decimals; 0 means "unstage".
 */
export function stepFulfillmentQuantity(current: number, delta: number, pending: number): number {
  const roundedPending = roundFulfillmentQuantity(pending)  // drop float noise before measuring
  const sign = roundedPending < 0 ? -1 : 1  // credit lines stage negative, normal lines positive
  const maxMag = Math.abs(roundedPending)  // never invoice beyond what is pending
  const nextMag = roundFulfillmentQuantity(Math.abs(current) + delta)  // grow/shrink the magnitude
  const mag = Math.min(Math.max(nextMag, 0), maxMag)  // clamp into [0, |pending|]
  return roundFulfillmentQuantity(sign * mag)  // reattach the credit/positive sign
}

/**
 * Read half-typed stepper text as the starting point for a +/- click.
 * The typed magnitude is pulled back into [0, |pending|] and given the pending's
 * sign (so a credit line, #277, starts negative); a part never starts from a
 * fraction (it is floored), so + and - on a part always land on whole units.
 *
 * @param raw - The stepper input's raw text (may be invalid, e.g. `"9"`, `"0.5"` on a part, `"-2"` on a credit).
 * @param itemType - The quote line type.
 * @param pending - The line's Qty Pending (negative on a credit).
 * @returns The starting amount (signed like pending), or 0 when the text is not a number.
 */
export function typedFulfillmentBase(raw: string, itemType: LineItemType, pending: number): number {
  const parsed = parseFloat(raw)  // lenient, same as the old stepper ("2abc" -> 2)
  if (Number.isNaN(parsed)) return 0  // junk -> start from nothing
  const roundedPending = roundFulfillmentQuantity(pending)  // measure against clean pending
  const sign = roundedPending < 0 ? -1 : 1  // the line's side of zero decides the sign
  // Input on the wrong side of zero is contradictory (a negative on a positive line, or
  // vice versa) -> start from nothing, as the stepper always did for a negative on a normal line
  if ((sign > 0 && parsed < 0) || (sign < 0 && parsed > 0)) return 0
  const capped = Math.min(Math.abs(parsed), Math.abs(roundedPending))  // magnitude, never above pending
  const whole = allowsFractionalQuantity(itemType) ? capped : Math.floor(capped)  // parts whole
  return roundFulfillmentQuantity(sign * whole)  // clean 2-decimal start, signed like pending
}

/**
 * Whether a line's staged amount already covers everything still pending.
 * Compares after rounding, so `1.5` staged against a `1.4999999999` pending counts.
 *
 * @param staged - The staged amount, or undefined when nothing is staged.
 * @param pending - The line's Qty Pending (negative on a credit).
 * @returns True when the + button (and "Fulfil all") has nothing left to add.
 */
export function isFullyStaged(staged: number | undefined, pending: number): boolean {
  if (staged === undefined) return false  // nothing staged yet
  // Compare by magnitude so a credit (-2 of -5) is judged like a normal line (2 of 5)
  return Math.abs(roundFulfillmentQuantity(staged)) >= Math.abs(roundFulfillmentQuantity(pending))
}

/**
 * Explain why a typed "Qty to Fulfill" is not acceptable for this line.
 * Uses the same per-type rules as Qty Ordered (parts whole, labour/misc up to
 * 2 decimals), then checks the amount against what is still pending.
 *
 * @param raw - The stepper input's raw text (e.g. `"0.5"`, or `"-2"` on a credit).
 * @param itemType - The quote line type.
 * @param pending - The line's Qty Pending (negative on a credit).
 * @returns A short user-facing message, or null when the value can be staged.
 */
export function fulfillmentInputError(raw: string, itemType: LineItemType, pending: number): string | null {
  const typeError = quantityInputError(raw, itemType)  // format, non-zero, whole parts, 2 decimals
  if (typeError !== null) return typeError
  const value = roundFulfillmentQuantity(Number(raw.trim()))  // already validated as numeric
  const roundedPending = roundFulfillmentQuantity(pending)  // compare after rounding, like the backend
  // The amount must invoice on the same side of zero as the pending balance (#277)
  if ((roundedPending > 0 && value < 0) || (roundedPending < 0 && value > 0)) {
    return roundedPending < 0 ? "This is a credit; enter a negative amount" : "Enter a positive amount"
  }
  // ...and never beyond what is pending, judged by magnitude (−6 cannot invoice a −5 credit)
  if (Math.abs(value) > Math.abs(roundedPending)) {
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
