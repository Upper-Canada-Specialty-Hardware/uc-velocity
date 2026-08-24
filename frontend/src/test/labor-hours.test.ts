import { describe, it, expect } from 'vitest'
import { computeLaborHoursReport } from '@/lib/laborHours'
import type { QuoteLineItem, Labor } from '@/types'

/** Build a labour record with sane defaults. */
function labor(overrides: Partial<Labor> = {}): Labor {
  return {
    id: 1,
    description: 'Labour',
    hours: 1,
    rate: 80,
    markup_percent: 50,
    ...overrides,
  }
}

let nextId = 1
/** Build a quote line item with sane defaults; only set what a test cares about. */
function lineItem(overrides: Partial<QuoteLineItem> = {}): QuoteLineItem {
  return {
    id: nextId++,
    quote_id: 1,
    item_type: 'labor',
    quantity: 1,
    qty_pending: 0,
    qty_fulfilled: 0,
    is_pms: false,
    ...overrides,
  }
}

describe('computeLaborHoursReport', () => {
  it('sums labour hours and estimated cost at a uniform rate (Dexter template: 15.0 hr / $1,200)', () => {
    const items = [
      lineItem({ item_type: 'labor', quantity: 1, labor: labor({ hours: 5, rate: 80 }) }), // 5 hr, $400
      lineItem({ item_type: 'labor', quantity: 2, labor: labor({ hours: 5, rate: 80 }) }), // 10 hr, $800
      lineItem({ item_type: 'part', quantity: 3, part: { id: 9, part_number: 'P-1', description: 'Bracket', cost: 12, markup_percent: 0, is_usd_priced: false } }),
    ]

    const report = computeLaborHoursReport(items)

    expect(report.total_hours).toBe(15)
    expect(report.estimated_cost).toBe(1200)
    expect(report.rate_display).toBe('$80.00/hr')
    expect(report.unresolved_count).toBe(0)
  })

  it('gives parts and miscellaneous lines 0.0 hours', () => {
    const items = [
      lineItem({ item_type: 'part', quantity: 4, part: { id: 2, part_number: 'P-2', description: 'Widget', cost: 5, markup_percent: 0, is_usd_priced: false } }),
      lineItem({ item_type: 'misc', quantity: 1, miscellaneous: { id: 3, description: 'Freight', unit_price: 50, markup_percent: 0, is_system_item: false } }),
    ]

    const report = computeLaborHoursReport(items)

    expect(report.total_hours).toBe(0)
    expect(report.rows.every(r => r.time === 0)).toBe(true)
    expect(report.rate_display).toBe('—')
  })

  it('shows "Variable" when labour lines carry different rates', () => {
    const items = [
      lineItem({ item_type: 'labor', quantity: 1, labor: labor({ hours: 2, rate: 80 }) }),
      lineItem({ item_type: 'labor', quantity: 1, labor: labor({ hours: 2, rate: 120 }) }),
    ]

    const report = computeLaborHoursReport(items)

    expect(report.total_hours).toBe(4)
    expect(report.estimated_cost).toBe(400) // 2*80 + 2*120
    expect(report.rate_display).toBe('Variable')
  })

  it('treats a PMS line as 0 hours and NOT as unresolved', () => {
    const items = [
      lineItem({ item_type: 'labor', quantity: 1, labor: labor({ hours: 10, rate: 80 }) }),
      // PMS: labour-typed, is_pms, no labor record, priced by percentage
      lineItem({ item_type: 'labor', quantity: 1, is_pms: true, pms_percent: 15, description: 'Project Management Services' }),
    ]

    const report = computeLaborHoursReport(items)

    const pmsRow = report.rows.find(r => r.description === 'Project Management Services')!
    expect(pmsRow.time).toBe(0)
    expect(pmsRow.unresolved).toBe(false)
    expect(report.unresolved_count).toBe(0)
    // PMS must not disturb the real labour totals
    expect(report.total_hours).toBe(10)
    expect(report.estimated_cost).toBe(800)
    expect(report.rate_display).toBe('$80.00/hr')
  })

  it('flags a non-PMS labour line with no labour record as unresolved and excludes it from totals', () => {
    const items = [
      lineItem({ item_type: 'labor', quantity: 1, labor: labor({ hours: 10, rate: 80 }) }),
      // Orphaned: had a labor_id but the record did not load (deleted/migrated)
      lineItem({ item_type: 'labor', quantity: 2, labor_id: 999, description: 'Electrical' }),
    ]

    const report = computeLaborHoursReport(items)

    const orphan = report.rows.find(r => r.description === 'Electrical')!
    expect(orphan.unresolved).toBe(true)
    expect(report.unresolved_count).toBe(1)
    // Only the resolved 10 hr line counts
    expect(report.total_hours).toBe(10)
    expect(report.estimated_cost).toBe(800)
    expect(report.rate_display).toBe('$80.00/hr')
  })

  it('handles an empty quote without throwing', () => {
    const report = computeLaborHoursReport([])
    expect(report.total_hours).toBe(0)
    expect(report.estimated_cost).toBe(0)
    expect(report.rate_display).toBe('—')
    expect(report.rows).toHaveLength(0)
  })
})
