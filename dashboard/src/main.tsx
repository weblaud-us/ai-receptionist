import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import { toast } from 'sonner'
import { AuthProvider } from '@receptionist/shared/auth/auth'
import { Toaster } from '@receptionist/shared/components/ui/sonner'
import { TooltipProvider } from '@receptionist/shared/components/ui/tooltip'
import { ApiError } from '@receptionist/shared/lib/api'
import { initSentry } from '@receptionist/shared/lib/sentry'
import { ErrorBoundary } from '@receptionist/shared/components/error-boundary'
import { TenantProvider } from '@/lib/tenant'
import { ThemeProvider } from '@receptionist/shared/providers/theme'
import App from './App.tsx'
import './index.css'

initSentry()

const queryClient = new QueryClient({
  // Every failed mutation surfaces as a toast (401s are handled by the auth layer).
  mutationCache: new MutationCache({
    onError: (error) => {
      if (error instanceof ApiError && error.status === 401) return
      toast.error(error instanceof Error ? error.message : 'Something went wrong')
    },
  }),
  defaultOptions: {
    queries: {
      retry: (count, error) => !(error instanceof ApiError && error.status < 500 && error.status !== 0) && count < 1,
      refetchOnWindowFocus: false,
      staleTime: 15_000,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary scope="app">
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AuthProvider>
            <TenantProvider>
              <TooltipProvider>
                <App />
                <Toaster richColors position="top-right" />
              </TooltipProvider>
            </TenantProvider>
          </AuthProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
    </ErrorBoundary>
  </StrictMode>,
)
