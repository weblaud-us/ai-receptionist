/** Public site details from the API (GET /public/site): the Help menu's outside links. */
import { useQuery } from '@tanstack/react-query'
import { api } from './api'

export interface PublicSite {
  demo_phone_number: string
  status_page_url: string
  support_email: string
  help_center_url: string
}

export const usePublicSite = () =>
  useQuery({ queryKey: ['public-site'], queryFn: () => api<PublicSite>('/public/site', { tenant: null }), staleTime: Infinity })
