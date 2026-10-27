import { describe, it, expect } from 'vitest'
import { getPOLineDescriptionText, getPOLineLabel } from '@/lib/poLines'
import type { Part } from '@/types'

/** An inventory part with only the fields the PO line label reads. */
const part = { id: 1, part_number: '8200', description: 'Push bar' } as Part

describe('PO line description (issue #283)', () => {
  it('shows the line text after the part number', () => {
    const line = { item_type: 'part', part, description: '41" rh push anodized clear' }
    expect(getPOLineLabel(line, 'Item')).toBe('8200 - 41" rh push anodized clear')
  })

  it('falls back to the inventory text when the part line has none', () => {
    expect(getPOLineLabel({ item_type: 'part', part }, 'Item')).toBe('8200 - Push bar')
    expect(getPOLineDescriptionText({ item_type: 'part', part, description: '  ' })).toBe('Push bar')
  })

  it('lets a staged edit win, and a cleared one read as the inventory text', () => {
    const line = { item_type: 'part', part, description: 'old wording' }
    expect(getPOLineLabel(line, 'Item', 'new wording')).toBe('8200 - new wording')
    expect(getPOLineDescriptionText(line, '')).toBe('Push bar')
  })

  it('uses the misc text, or the fallback when there is none', () => {
    expect(getPOLineLabel({ item_type: 'misc', description: 'Installation' }, 'Miscellaneous')).toBe('Installation')
    expect(getPOLineLabel({ item_type: 'misc' }, 'Miscellaneous')).toBe('Miscellaneous')
  })

  it('shows the stored text of a part line whose inventory part is gone', () => {
    expect(getPOLineLabel({ item_type: 'part', description: 'Imported line' }, 'Unknown item')).toBe('Imported line')
  })
})
