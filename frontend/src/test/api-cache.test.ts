import { describe, it, expect, vi, afterEach } from 'vitest'
import { api } from '@/api/client'

describe('api client - HTTP caching', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('passes cache: no-store to fetch on a GET routed through request()', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [],
    } as Response)
    vi.stubGlobal('fetch', fetchMock)

    await api.costCodes.getAll()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [, options] = fetchMock.mock.calls[0]
    expect(options).toMatchObject({ cache: 'no-store' })
  })
})
