import { describe, expect, it, vi } from 'vitest'
import { createQueryClient } from '@/lib/query-client'

describe('mutation errors', () => {
  it('shows exactly one toast per failure, and none for a silent mutation', async () => {
    const notify = vi.fn()
    const client = createQueryClient(notify)
    const run = (meta?: { silentError?: boolean }) =>
      client.getMutationCache().build(client, { mutationFn: () => Promise.reject(new Error('nope')), meta }).execute(undefined).catch(() => {})
    await run()
    expect(notify).toHaveBeenCalledTimes(1)
    expect(notify).toHaveBeenCalledWith('nope')
    await run({ silentError: true })
    expect(notify).toHaveBeenCalledTimes(1)
  })
})
