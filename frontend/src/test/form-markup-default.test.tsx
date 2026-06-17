import { describe, it, expect, vi, beforeAll } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import { LaborForm } from '@/components/forms/LaborForm'
import { MiscForm } from '@/components/forms/MiscForm'
import { PartForm } from '@/components/forms/PartForm'
import type { Labor, Miscellaneous, Part } from '@/types'

// PartForm fetches labour + vendors on mount, and the create dialog for linked
// labour relies on a couple of browser APIs jsdom does not implement. Mock the
// api client so the form mounts without touching the network, and stub the
// missing DOM APIs so the multi-select can render.
vi.mock('@/api/client', () => ({
  api: {
    labor: { getAll: vi.fn().mockResolvedValue([]) },
    profiles: { getAll: vi.fn().mockResolvedValue([]) },
  },
}))

beforeAll(() => {
  class ResizeObserverMock {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  ;(globalThis as unknown as { ResizeObserver: typeof ResizeObserverMock }).ResizeObserver = ResizeObserverMock
})

// Follow-up to issue #146: new inventory must default to 50% markup. The
// backend create-schema default only applies when the request omits
// markup_percent, but these forms always send an explicit value, so the field
// itself has to default to 50 in create mode (and still show the real value
// when editing an existing item).
describe('inventory forms — markup defaults to 50% on create (issue #146)', () => {
  const markupValue = (container: HTMLElement): string =>
    (container.querySelector('#markup') as HTMLInputElement).value

  it('LaborForm defaults markup to 50 in create mode', () => {
    const { container } = render(<LaborForm />)
    expect(markupValue(container)).toBe('50')
  })

  it('MiscForm defaults markup to 50 in create mode', () => {
    const { container } = render(<MiscForm />)
    expect(markupValue(container)).toBe('50')
  })

  it('PartForm defaults markup to 50 in create mode', async () => {
    const { container } = render(<PartForm />)
    await waitFor(() => expect(container.querySelector('#markup')).not.toBeNull())
    expect(markupValue(container)).toBe('50')
  })

  it('LaborForm shows the existing markup when editing, not 50', () => {
    const labor = { id: 1, description: 'Install', hours: 2, rate: 50, markup_percent: 12 } as Labor
    const { container } = render(<LaborForm labor={labor} />)
    expect(markupValue(container)).toBe('12')
  })

  it('MiscForm shows the existing markup when editing, not 50', () => {
    const misc = { id: 1, description: 'Rental', unit_price: 100, markup_percent: 12 } as Miscellaneous
    const { container } = render(<MiscForm misc={misc} />)
    expect(markupValue(container)).toBe('12')
  })

  it('PartForm shows the existing markup when editing, not 50', async () => {
    const part = { id: 1, part_number: 'P-1', description: 'Widget', cost: 100, markup_percent: 12 } as Part
    const { container } = render(<PartForm part={part} />)
    await waitFor(() => expect(markupValue(container)).toBe('12'))
  })
})
