import { describe, it, expect } from 'vitest'
import {
  roundFulfillmentQuantity,
  stepFulfillmentQuantity,
  isFullyStaged,
  fulfillmentInputError,
  isClearFulfillmentInput,
  typedFulfillmentBase,
} from '@/lib/fulfillment'

// Invoicing ("Qty to Fulfill") in the quote editor: labour/misc lines may be
// invoiced in fractions, parts in whole units, never more than is pending, and
// the +/- buttons move by 1 between 0 and the pending amount.
describe('roundFulfillmentQuantity', () => {
  it('drops float noise', () => {
    expect(roundFulfillmentQuantity(1.5 - 1)).toBe(0.5)
    expect(roundFulfillmentQuantity(0.49999999999)).toBe(0.5)
    expect(roundFulfillmentQuantity(0.1 + 0.2)).toBe(0.3)
  })

  it('returns 0 for -0 and non-finite values', () => {
    expect(Object.is(roundFulfillmentQuantity(-0), 0)).toBe(true)
    expect(roundFulfillmentQuantity(Number.NaN)).toBe(0)
  })
})

describe('stepFulfillmentQuantity', () => {
  it('steps up by 1 and stops at a fractional pending amount', () => {
    expect(stepFulfillmentQuantity(0, 1, 1.5)).toBe(1)
    expect(stepFulfillmentQuantity(1, 1, 1.5)).toBe(1.5)
    expect(stepFulfillmentQuantity(1.5, 1, 1.5)).toBe(1.5)
  })

  it('steps down by 1 and never below 0', () => {
    expect(stepFulfillmentQuantity(1.5, -1, 1.5)).toBe(0.5)
    expect(stepFulfillmentQuantity(0.5, -1, 1.5)).toBe(0)
    expect(stepFulfillmentQuantity(0, -1, 1.5)).toBe(0)
  })

  it('rounds away float noise', () => {
    expect(stepFulfillmentQuantity(1.1 * 3, -1, 5)).toBe(2.3)  // 3.3000000000000003 - 1
    expect(stepFulfillmentQuantity(0, 1, 0.30000000000000004)).toBe(0.3)
  })

  it('keeps whole steps for parts', () => {
    expect(stepFulfillmentQuantity(2, 1, 3)).toBe(3)
    expect(stepFulfillmentQuantity(3, 1, 3)).toBe(3)
    expect(stepFulfillmentQuantity(3, -1, 3)).toBe(2)
  })
})

describe('typedFulfillmentBase', () => {
  it('clamps typed text into [0, pending]', () => {
    expect(typedFulfillmentBase('9', 'labor', 1.5)).toBe(1.5)
    expect(typedFulfillmentBase('0.5', 'labor', 1.5)).toBe(0.5)
    expect(typedFulfillmentBase('-2', 'labor', 1.5)).toBe(0)
    expect(typedFulfillmentBase('abc', 'misc', 1.5)).toBe(0)
  })

  it('never starts a part from a fraction', () => {
    expect(typedFulfillmentBase('1.5', 'part', 3)).toBe(1)
  })
})

describe('isFullyStaged', () => {
  it('matches a fractional pending amount', () => {
    expect(isFullyStaged(1.5, 1.5)).toBe(true)
    expect(isFullyStaged(1.5, 1.4999999999)).toBe(true)
    expect(isFullyStaged(1, 1.5)).toBe(false)
    expect(isFullyStaged(undefined, 1.5)).toBe(false)
  })
})

describe('fulfillmentInputError', () => {
  it('accepts a fraction on a labour or misc line', () => {
    expect(fulfillmentInputError('0.5', 'labor', 1.5)).toBeNull()
    expect(fulfillmentInputError('1.5', 'misc', 1.5)).toBeNull()
  })

  it('refuses a fraction on a part line', () => {
    expect(fulfillmentInputError('0.5', 'part', 3)).toBe('Parts must be ordered in whole numbers')
    expect(fulfillmentInputError('2', 'part', 3)).toBeNull()
  })

  it('refuses a third decimal', () => {
    expect(fulfillmentInputError('0.555', 'labor', 1.5)).toBe('Quantity can have at most 2 decimal places')
  })

  it('refuses more than pending, showing pending cleanly', () => {
    expect(fulfillmentInputError('2', 'labor', 1.5)).toBe('Cannot exceed Qty Pending (1.5)')
    expect(fulfillmentInputError('0.31', 'labor', 0.1 + 0.2)).toBe('Cannot exceed Qty Pending (0.3)')
  })

  it('compares against pending after rounding', () => {
    expect(fulfillmentInputError('1.5', 'labor', 1.4999999999)).toBeNull()
  })

  it('refuses junk text', () => {
    expect(fulfillmentInputError('abc', 'labor', 1.5)).toBe('Enter a valid number')
    expect(fulfillmentInputError('-1', 'labor', 1.5)).toBe('Enter a valid number')
  })
})

describe('isClearFulfillmentInput', () => {
  it('treats empty and zero as unstage', () => {
    expect(isClearFulfillmentInput('')).toBe(true)
    expect(isClearFulfillmentInput(' ')).toBe(true)
    expect(isClearFulfillmentInput('0')).toBe(true)
    expect(isClearFulfillmentInput('0.00')).toBe(true)
  })

  it('does not treat real amounts or a lone point as unstage', () => {
    expect(isClearFulfillmentInput('0.5')).toBe(false)
    expect(isClearFulfillmentInput('10')).toBe(false)
    expect(isClearFulfillmentInput('.')).toBe(false)
  })
})
