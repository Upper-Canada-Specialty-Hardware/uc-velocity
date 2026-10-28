import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { PartForm } from '@/components/forms/PartForm'
import { api } from '@/api/client'
import type { Part } from '@/types'

/** An existing inventory part being edited. */
const part = { id: 5, part_number: 'M10-120A', description: 'Magic Force Pull Arm', cost: 120.5, markup_percent: 50 } as Part

// Stub the form's lookups and the save, so nothing touches the network.
vi.mock('@/api/client', () => ({
  api: {
    labor: { getAll: vi.fn(async () => []) },
    profiles: { getAll: vi.fn(async () => []) },
    parts: { update: vi.fn(async () => part), create: vi.fn() },
  },
}))

const MESSAGE = 'Edit inventory part changes this part for every future quote and PO.'

// From a PO, saving an inventory part asks first (issue #291); elsewhere it saves straight away.
describe('PartForm save confirmation', () => {
  beforeEach(() => vi.clearAllMocks())

  it('asks before saving, and Cancel leaves inventory untouched', async () => {
    render(<PartForm part={part} confirmSaveMessage={MESSAGE} />)
    fireEvent.click(await screen.findByText('Save Changes'))
    expect(await screen.findByText(MESSAGE)).toBeTruthy()             // the are-you-sure prompt
    fireEvent.click(screen.getByText('Cancel', { selector: '[role="alertdialog"] button' }))
    expect(api.parts.update).not.toHaveBeenCalled()

    fireEvent.click(screen.getByText('Save Changes'))
    fireEvent.click(await screen.findByText('Save to inventory'))
    await waitFor(() => expect(api.parts.update).toHaveBeenCalledWith(5, expect.objectContaining({ part_number: 'M10-120A' })))
  })

  it('saves without asking when no message is given (the inventory screen)', async () => {
    render(<PartForm part={part} />)
    fireEvent.click(await screen.findByText('Save Changes'))
    await waitFor(() => expect(api.parts.update).toHaveBeenCalledTimes(1))
    expect(screen.queryByText(MESSAGE)).toBeNull()
  })
})
