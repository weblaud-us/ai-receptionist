import '@tanstack/react-query'

declare module '@tanstack/react-query' {
  interface Register {
    mutationMeta: {
      /** The caller handles this mutation's errors itself; the global error toast stays quiet. */
      silentError?: boolean
    }
  }
}
