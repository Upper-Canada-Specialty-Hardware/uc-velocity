import { describe, it, expect, vi, beforeEach, beforeAll } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { POEditor } from '@/components/editors/POEditor'
import { api } from '@/api/client'

/** The inventory part both tests order. */
const part = { id: 1, part_number: '8200', description: 'Push bar', cost: 10, markup_percent: 0 }

/** A Draft PO with one part line (qty 2) and one misc line, as GET /purchase-orders/{id} returns it. */
const po = {
  id: 7, project_id: 3, vendor_id: 4, created_at: '2026-10-28T12:00:00', po_sequence: 1,
  po_number: 'PO-A0001-0001-0', current_version: 0, status: 'Draft', line_items: [
    { id: 11, purchase_order_id: 7, item_type: 'part', part_id: 1, part, description: 'Push bar',
      quantity: 2, unit_price: 10, qty_pending: 2, qty_received: 0 },
    { id: 12, purchase_order_id: 7, item_type: 'misc', description: 'Shipping',
      quantity: 1, unit_price: 5, qty_pending: 1, qty_received: 0 },
  ],
  vendor: { id: 4, name: '[TEST] Vendor', type: 'vendor', contacts: [] },
}

// Stub every call the editor makes on load (and the commit), so nothing touches the network.
vi.mock('@/api/client', () => ({
  api: {
    purchaseOrders: {
      get: vi.fn(async () => po),
      getReceivings: vi.fn(async () => []),
      getSnapshots: vi.fn(async () => []),
      commitEdits: vi.fn(async () => ({ success: true, message: 'ok', purchase_order: po, snapshot_version: 1 })),
    },
    parts: { getAll: vi.fn(async () => [part]) },
    companySettings: { get: vi.fn(async () => ({})) },
    costCodes: { getAll: vi.fn(async () => []) },
    projects: { get: vi.fn(async () => ({})), getAll: vi.fn(async () => []) },
  },
}))

/** Render the editor, wait for the PO to load, and enter edit mode. */
async function openInEditMode() {
  render(<POEditor poId={7} />)
  fireEvent.click(await screen.findByText('Edit PO'))
}

/** Open the Edit Line Item dialog of a committed line by its row label. */
function openLine(label: string) {
  fireEvent.click(screen.getByLabelText(`Edit line item ${label}`))
  return screen.getByRole('dialog')
}

// jsdom lacks the browser APIs the part picker (cmdk + Radix Popover) uses; stub them.
beforeAll(() => {
  class ResizeObserverMock { observe() {} unobserve() {} disconnect() {} }
  ;(globalThis as unknown as { ResizeObserver: typeof ResizeObserverMock }).ResizeObserver = ResizeObserverMock
  Element.prototype.scrollIntoView ??= () => {}
  Element.prototype.hasPointerCapture ??= () => false
  Element.prototype.releasePointerCapture ??= () => {}
})

describe('PO editor line edits (issue #283 review)', () => {
  beforeEach(() => vi.clearAllMocks())

  it('adds a part with its own PO-only description and sends it on commit', async () => {
    await openInEditMode()
    fireEvent.click(screen.getByText('Add Part'))
    const dialog = screen.getByRole('dialog')
    fireEvent.click(within(dialog).getByRole('combobox'))               // open the part picker
    fireEvent.click(await screen.findByRole('option', { name: /8200 - Push bar/ }))  // pick the part
    const text = within(dialog).getByLabelText('Description (this PO only)') as HTMLInputElement
    expect(text.value).toBe('Push bar')                                 // prefilled from inventory
    fireEvent.change(text, { target: { value: '  41in rh push  ' } })
    fireEvent.click(within(dialog).getByText('Add Line Item'))

    fireEvent.click(screen.getByText('Commit (1)'))
    fireEvent.click(await screen.findByText('Commit Changes', { selector: 'button' }))
    await waitFor(() => expect(api.purchaseOrders.commitEdits).toHaveBeenCalled())
    const [, request] = vi.mocked(api.purchaseOrders.commitEdits).mock.calls[0]
    expect(request.changes).toEqual([expect.objectContaining({ action: 'add', part_id: 1, description: '41in rh push' })])
  })

  it('drops a staged quantity change when it is typed back to the original', async () => {
    await openInEditMode()
    let dialog = openLine('8200 - Push bar')
    fireEvent.change(within(dialog).getByDisplayValue('2'), { target: { value: '5' } })
    fireEvent.click(within(dialog).getByText('Save Changes'))
    expect(screen.getByText('Commit (1)')).toBeTruthy()                 // qty 5 staged

    dialog = openLine('8200 - Push bar')
    fireEvent.change(within(dialog).getByDisplayValue('5'), { target: { value: '2' } })
    fireEvent.click(within(dialog).getByText('Save Changes'))
    await waitFor(() => expect(screen.queryByText('Commit (1)')).toBeNull())  // back to 2: nothing staged
  })

  it('will not save a misc line with a blank description', async () => {
    await openInEditMode()
    const dialog = openLine('Shipping')
    fireEvent.change(within(dialog).getByLabelText('Description'), { target: { value: '   ' } })
    expect(within(dialog).getByText('Misc lines need a description.')).toBeTruthy()
    expect((within(dialog).getByText('Save Changes') as HTMLButtonElement).disabled).toBe(true)
  })
})
