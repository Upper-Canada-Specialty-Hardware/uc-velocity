import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { QuoteQuantityInput } from '@/components/editors/QuoteEditor'

// The editor module imports the api client; stub it so nothing touches the network.
vi.mock('@/api/client', () => ({ api: {} }))

// Inline Qty Ordered input in the quote editor: labour/misc accept fractions
// (up to 2 decimals), parts only whole numbers, and invalid text is never staged.
describe('QuoteQuantityInput', () => {
  it('stages 1.5 on a labour line', () => {
    const onValidChange = vi.fn()
    render(<QuoteQuantityInput value={1} itemType="labor" onValidChange={onValidChange} />)
    const input = screen.getByRole('spinbutton')
    expect(input.getAttribute('step')).toBe('0.01')
    fireEvent.change(input, { target: { value: '1.5' } })
    expect(onValidChange).toHaveBeenLastCalledWith(1.5)
  })

  it('refuses 1.5 on a part line and snaps back on blur', () => {
    const onValidChange = vi.fn()
    render(<QuoteQuantityInput value={2} itemType="part" onValidChange={onValidChange} />)
    const input = screen.getByRole('spinbutton')
    expect(input.getAttribute('step')).toBe('1')
    fireEvent.change(input, { target: { value: '1.5' } })
    expect(onValidChange).not.toHaveBeenCalled()
    expect(input.getAttribute('aria-invalid')).toBe('true')
    expect(input.getAttribute('title')).toBe('Parts must be ordered in whole numbers')
    fireEvent.blur(input)
    expect((input as HTMLInputElement).value).toBe('2')  // last valid quantity restored
  })

  it('refuses a 3rd decimal on a misc line', () => {
    const onValidChange = vi.fn()
    render(<QuoteQuantityInput value={1} itemType="misc" onValidChange={onValidChange} />)
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '1.234' } })
    expect(onValidChange).not.toHaveBeenCalled()
  })

  it('shows the stored quantity cleanly', () => {
    render(<QuoteQuantityInput value={0.1 + 0.2} itemType="labor" onValidChange={() => {}} />)
    expect((screen.getByRole('spinbutton') as HTMLInputElement).value).toBe('0.3')
  })
})
