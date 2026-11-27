import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import { AuthProvider } from '@receptionist/shared/auth/auth'
import { Toaster } from '@receptionist/shared/components/ui/sonner'
import { TooltipProvider } from '@receptionist/shared/components/ui/tooltip'
import { initSentry } from '@receptionist/shared/lib/sentry'
import { ErrorBoundary } from '@receptionist/shared/components/error-boundary'
import { ThemeProvider } from '@receptionist/shared/providers/theme'
import App from './App.tsx'
import { createQueryClient } from './lib/query-client'
import './index.css'

initSentry()

const queryClient = createQueryClient()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary scope="app">
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AuthProvider>
            <TooltipProvider>
              <App />
              <Toaster richColors position="top-right" />
            </TooltipProvider>
          </AuthProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
    </ErrorBoundary>
  </StrictMode>,
)
