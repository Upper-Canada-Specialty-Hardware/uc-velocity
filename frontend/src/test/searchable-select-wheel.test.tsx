import { describe, it, expect, vi, beforeAll } from 'vitest'
import { render, fireEvent, waitFor } from '@testing-library/react'
import { SearchableSelect } from '@/components/ui/searchable-select'

// jsdom is missing several browser APIs that cmdk + Radix Popover rely on.
// Stub them so the dropdown can render and the wheel event can fire.
beforeAll(() => {
  class ResizeObserverMock {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  ;(globalThis as unknown as { ResizeObserver: typeof ResizeObserverMock }).ResizeObserver = ResizeObserverMock

  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = () => {}
  }
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {}
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = () => {}
  }
})

// Regression test for issue #69. When SearchableSelect is rendered inside a
// Radix Dialog, react-remove-scroll cancels wheel events on the portaled
// Popover content. The fix is `onWheel={e => e.stopPropagation()}` on the
// CommandList — this test asserts that wheel events on the list do not bubble
// past the SearchableSelect's React tree, which is the only contract we can
// verify in jsdom (jsdom does not perform real scroll).
describe('SearchableSelect — wheel propagation (issue #69)', () => {
  it('stops wheel events from propagating past the dropdown list', async () => {
    const onWheelOutside = vi.fn()
    const { getByRole } = render(
      <div onWheel={onWheelOutside}>
        <SearchableSelect
          options={[
            { value: '1', label: 'Option 1' },
            { value: '2', label: 'Option 2' },
            { value: '3', label: 'Option 3' },
          ]}
          value={undefined}
          onChange={() => {}}
        />
      </div>
    )

    fireEvent.click(getByRole('combobox'))

    const list = await waitFor(() => {
      const el = document.querySelector('[cmdk-list]')
      if (!el) throw new Error('CommandList not yet rendered')
      return el as HTMLElement
    })

    fireEvent.wheel(list, { deltaY: 100 })

    expect(onWheelOutside).not.toHaveBeenCalled()
  })
})
