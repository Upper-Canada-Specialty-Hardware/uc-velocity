import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { POAuditTrail } from '@/components/editors/POAuditTrail'

// One PO snapshot exactly as GET /purchase-orders/{id}/snapshots returns it:
// the lines arrive under `line_item_states` (backend POSnapshot schema).
const apiSnapshot = {
  id: 11,
  purchase_order_id: 4974,
  version: 1,
  action_type: 'edit',
  action_description: 'Batch edit: Edited 1 item(s)',
  created_at: '2026-10-28T12:26:31.861535',
  actor_user_id: null,
  actor_email: null,
  receiving_id: null,
  line_item_states: [{
    id: 15, snapshot_id: 11, original_line_item_id: 9109, item_type: 'part', part_id: 3600,
    description: 'Custom PO description', quantity: 1, unit_price: 90.07,
    qty_pending: 1, qty_received: 0, actual_unit_price: null, is_deleted: false,
  }],
}

// Stub the api client: the audit trail only fetches snapshots on mount.
vi.mock('@/api/client', () => ({
  api: { purchaseOrders: { getSnapshots: vi.fn(async () => [apiSnapshot]) } },
}))

// Opening a version lists that version's lines; it used to crash the page
// because the screen read a field name the API never sends.
describe('POAuditTrail', () => {
  it('opens a version and lists its lines with their descriptions', async () => {
    render(<POAuditTrail purchaseOrderId={4974} currentVersion={1} />)
    fireEvent.click(await screen.findByText('v1', { selector: 'span' }))  // expand the version row
    expect(screen.getByText('Line Items at this version:')).toBeTruthy()
    expect(screen.getByText(/Custom PO description/)).toBeTruthy()       // the line's own description
  })
})
