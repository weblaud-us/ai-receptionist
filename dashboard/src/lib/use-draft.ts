import { useState } from 'react'

/**
 * Local, editable copy of server data. Re-syncs when the server value changes (after a save or
 * a business switch) and reports whether the draft differs from it. Uses React's "adjust state
 * while rendering" pattern instead of an effect, so there is no extra render with stale data.
 */
export function useDraft<T>(source: T | null | undefined) {
  const key = JSON.stringify(source ?? null)
  const [draft, setDraft] = useState<T | null>(source ?? null)
  const [syncedKey, setSyncedKey] = useState(key)
  if (syncedKey !== key) {
    setSyncedKey(key)
    setDraft(source ?? null)
  }
  const dirty = JSON.stringify(draft) !== key
  return { draft, setDraft, dirty, reset: () => setDraft(source ?? null) }
}
