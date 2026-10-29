import { describe, it, expect } from 'vitest'
import {
  formatQuantity,
  isValidQuantity,
  quantityStep,
  quantityInputError,
  parseQuantityInput,
  allowsFractionalQuantity,
} from '@/lib/format'

// Quote quantities: labour and misc lines may be fractional (up to 2 decimals),
// parts stay whole. These rules mirror the backend so the editor refuses bad
// input before Commit instead of the API rejecting it.
describe('formatQuantity', () => {
  it('shows whole values without decimals', () => {
    expect(formatQuantity(2)).toBe('2')
    expect(formatQuantity(2.0)).toBe('2')
    expect(formatQuantity(0)).toBe('0')
  })

  it('shows fractions with up to 2 decimals and no trailing zeros', () => {
    expect(formatQuantity(1.5)).toBe('1.5')
    expect(formatQuantity(0.25)).toBe('0.25')
    expect(formatQuantity(12345.25)).toBe('12345.25')
    expect(formatQuantity(1.1)).toBe('1.1')
  })

  it('hides float noise', () => {
    expect(formatQuantity(0.1 + 0.2)).toBe('0.3')  // 0.30000000000000004
    expect(formatQuantity(1.1 * 3)).toBe('3.3')  // 3.3000000000000003
    expect(formatQuantity(-0)).toBe('0')
  })

  it('treats missing values as 0', () => {
    expect(formatQuantity(null)).toBe('0')
    expect(formatQuantity(undefined)).toBe('0')
    expect(formatQuantity(Number.NaN)).toBe('0')
  })

  it('renders a credit (negative) quantity with its sign (#277)', () => {
    // The quote editor and both PDFs render quantities through this helper, so a
    // credit line must print as "-5" / "-1.5", not drop the sign or show "0".
    expect(formatQuantity(-5)).toBe('-5')
    expect(formatQuantity(-1.5)).toBe('-1.5')
    expect(formatQuantity(-0.25)).toBe('-0.25')
  })
})

describe('quantityStep / allowsFractionalQuantity', () => {
  it('parts step by whole units, labour and misc by hundredths', () => {
    expect(quantityStep('part')).toBe('1')
    expect(quantityStep('labor')).toBe('0.01')
    expect(quantityStep('misc')).toBe('0.01')
    expect(allowsFractionalQuantity('part')).toBe(false)
    expect(allowsFractionalQuantity('labor')).toBe(true)
    expect(allowsFractionalQuantity('misc')).toBe(true)
  })
})

describe('isValidQuantity', () => {
  it('parts must be whole and non-zero; a whole credit (negative) is allowed', () => {
    expect(isValidQuantity(1, 'part')).toBe(true)
    expect(isValidQuantity(12, 'part')).toBe(true)
    expect(isValidQuantity(1.5, 'part')).toBe(false)
    expect(isValidQuantity(0, 'part')).toBe(false)
    expect(isValidQuantity(-1, 'part')).toBe(true)   // whole credit, #277
    expect(isValidQuantity(-2, 'part')).toBe(true)   // whole credit, #277
    expect(isValidQuantity(-1.5, 'part')).toBe(false)  // a part credit must still be whole
  })

  it('labour and misc accept non-zero values (credits too) with up to 2 decimals', () => {
    for (const type of ['labor', 'misc'] as const) {
      expect(isValidQuantity(1, type)).toBe(true)
      expect(isValidQuantity(1.5, type)).toBe(true)
      expect(isValidQuantity(0.25, type)).toBe(true)
      expect(isValidQuantity(0.01, type)).toBe(true)
      expect(isValidQuantity(1.234, type)).toBe(false)  // 3rd decimal
      expect(isValidQuantity(0, type)).toBe(false)
      expect(isValidQuantity(-0.5, type)).toBe(true)    // fractional credit, #277
      expect(isValidQuantity(-1.234, type)).toBe(false)  // credit still capped at 2 decimals
    }
  })

  it('tolerates float noise but not NaN/Infinity', () => {
    expect(isValidQuantity(0.1 + 0.2, 'labor')).toBe(true)
    expect(isValidQuantity(Number.NaN, 'labor')).toBe(false)
    expect(isValidQuantity(Number.POSITIVE_INFINITY, 'misc')).toBe(false)
  })
})

describe('quantityInputError / parseQuantityInput', () => {
  it('accepts a fractional labour or misc quantity', () => {
    expect(quantityInputError('1.5', 'labor')).toBeNull()
    expect(parseQuantityInput('1.5', 'labor')).toBe(1.5)
    expect(parseQuantityInput('0.25', 'misc')).toBe(0.25)
    expect(parseQuantityInput('1.50', 'labor')).toBe(1.5)
    expect(parseQuantityInput(' 2 ', 'misc')).toBe(2)
  })

  it('refuses a fractional part with the backend wording', () => {
    expect(quantityInputError('1.5', 'part')).toBe('Parts must be ordered in whole numbers')
    expect(parseQuantityInput('1.5', 'part')).toBeNull()
    expect(parseQuantityInput('3', 'part')).toBe(3)
    expect(parseQuantityInput('3.00', 'part')).toBe(3)
  })

  it('refuses more than 2 decimals', () => {
    expect(quantityInputError('1.234', 'labor')).toBe('Quantity can have at most 2 decimal places')
    expect(parseQuantityInput('1.234', 'misc')).toBeNull()
  })

  it('refuses empty and zero; accepts a credit; refuses non-numeric text', () => {
    expect(quantityInputError('', 'labor')).toBe('Enter a quantity')
    expect(quantityInputError('.', 'labor')).toBe('Enter a quantity')
    expect(quantityInputError('-', 'labor')).toBe('Enter a quantity')  // just the minus of a credit so far
    expect(quantityInputError('0', 'labor')).toBe('Quantity cannot be zero')
    expect(quantityInputError('0.0', 'misc')).toBe('Quantity cannot be zero')
    expect(quantityInputError('-1', 'labor')).toBeNull()      // a credit, #277
    expect(quantityInputError('-1.5', 'misc')).toBeNull()     // a fractional credit
    expect(quantityInputError('-2', 'part')).toBeNull()       // a whole part credit
    expect(quantityInputError('-1.5', 'part')).toBe('Parts must be ordered in whole numbers')
    expect(quantityInputError('1e2', 'labor')).toBe('Enter a valid number')
    expect(quantityInputError('abc', 'part')).toBe('Enter a valid number')
  })

  it('treats half-typed decimals sensibly', () => {
    expect(parseQuantityInput('1.', 'labor')).toBe(1)  // "1." is still 1
    expect(parseQuantityInput('.5', 'labor')).toBe(0.5)
  })
})
