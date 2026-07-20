import type { QuoteLineItem } from '@/types'
import { formatCurrency } from '@/lib/format'

/**
 * Labour-hours report math (Issue #162).
 *
 * The report answers one question: how many labour hours does a quote represent,
 * per item and in total? All the data needed is already embedded in the quote's
 * line items when it loads — each labour line carries its linked `labor` record
 * with `hours` and `rate`. This module is the pure calculation; the PDF component
 * only renders what it returns.
 *
 * Line-item taxonomy for `item_type === 'labor'`:
 *  - normal labour: has a `labor` record -> real hours (quantity x labor.hours)
 *  - PMS line: `is_pms`, no `labor` record -> 0 hours by design (priced by a
 *    percentage or flat amount, not by hours)
 *  - orphaned labour: a non-PMS labour line whose `labor` record didn't load
 *    (deleted inventory row / migration artifact) -> hours genuinely unknown
 *
 * Parts and miscellaneous lines never carry hours, so they contribute 0.
 */

export interface LaborHoursRow {
  id: number
  description: string
  quantity: number
  hours_per_unit: number
  /** quantity x hours_per_unit */
  time: number
  /** time x rate (labour cost before markup) */
  cost: number
  /** True for a non-PMS labour line whose labour record is missing. */
  unresolved: boolean
}

export interface LaborHoursReport {
  rows: LaborHoursRow[]
  total_hours: number
  estimated_cost: number
  /** "$80.00/hr" when every contributing line agrees, "Variable" when they differ, "—" when none. */
  rate_display: string
  /** Count of non-PMS labour lines whose hours couldn't be resolved. */
  unresolved_count: number
}

export function getLaborItemDescription(item: QuoteLineItem): string {
  if (item.item_type === 'labor' && item.labor) return item.labor.description
  if (item.item_type === 'part' && item.part)
    return `${item.part.part_number} - ${item.part.description}`
  if (item.item_type === 'misc' && item.miscellaneous) return item.miscellaneous.description
  return item.description || 'Unknown item'
}

/** Hours per single unit. Only labour lines with a resolved record carry hours. */
export function hoursPerUnit(item: QuoteLineItem): number {
  if (item.item_type === 'labor' && item.labor) return item.labor.hours
  return 0
}

/**
 * A genuine labour line whose `labor` record didn't come back with the quote.
 * PMS lines are excluded: they are labour-typed but carry no record and no hours
 * by design, so they are legitimately 0.0 hr., not "unknown".
 */
export function isUnresolvedLabor(item: QuoteLineItem): boolean {
  return item.item_type === 'labor' && !item.is_pms && !item.labor
}

export function computeLaborHoursReport(items: QuoteLineItem[]): LaborHoursReport {
  const rows: LaborHoursRow[] = items.map(item => {
    const unresolved = isUnresolvedLabor(item)
    const perUnit = hoursPerUnit(item)
    const time = item.quantity * perUnit
    return {
      id: item.id,
      description: getLaborItemDescription(item),
      quantity: item.quantity,
      hours_per_unit: perUnit,
      time,
      cost: time * (item.labor?.rate ?? 0),
      unresolved,
    }
  })

  // Only labour lines with a resolved record contribute to the totals.
  const resolvedLabor = items.filter(i => i.item_type === 'labor' && i.labor)

  const total_hours = resolvedLabor.reduce(
    (sum, item) => sum + item.quantity * hoursPerUnit(item),
    0
  )
  const estimated_cost = resolvedLabor.reduce(
    (sum, item) => sum + item.quantity * hoursPerUnit(item) * (item.labor?.rate ?? 0),
    0
  )

  // UC Velocity has no single company labour rate — each labour record carries
  // its own. Show one rate only when every hour-bearing line agrees.
  const contributingRates = new Set(
    resolvedLabor
      .filter(item => item.quantity * hoursPerUnit(item) > 0)
      .map(item => item.labor?.rate ?? 0)
  )
  const rate_display =
    contributingRates.size === 1
      ? `${formatCurrency([...contributingRates][0])}/hr`
      : contributingRates.size === 0
        ? '—'
        : 'Variable'

  const unresolved_count = items.filter(isUnresolvedLabor).length

  return { rows, total_hours, estimated_cost, rate_display, unresolved_count }
}
