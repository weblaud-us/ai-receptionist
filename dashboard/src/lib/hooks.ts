import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@receptionist/shared/lib/api'
import type {
  Availability,
  Booking,
  BookingList,
  CallerMessage,
  CallRecord,
  ConversationDetail,
  ConversationSummary,
  Guest,
  GuestDetail,
  LiveCall,
  Occupancy,
  Reminders,
  Room,
  Stats,
  SystemInfo,
} from './types'

type Params = Record<string, string | number | boolean | null | undefined>

/* ───────────── queries ───────────── */

export const useStats = (days: number) =>
  useQuery({
    queryKey: ['stats', days],
    queryFn: () => api<Stats>('/admin/stats', { params: { days } }),
    refetchInterval: 60_000,
  })

export const useBookings = (params: Params) =>
  useQuery({
    queryKey: ['bookings', params],
    queryFn: () => api<BookingList>('/bookings/', { params }),
    placeholderData: keepPreviousData,
  })

export const useBooking = (bookingId: string | null) =>
  useQuery({
    queryKey: ['booking', bookingId],
    queryFn: () => api<Booking>(`/bookings/${bookingId}`),
    enabled: !!bookingId,
  })

export const useAvailability = (params: Params, enabled: boolean) =>
  useQuery({
    queryKey: ['availability', params],
    queryFn: () => api<Availability>('/rooms/availability', { params }),
    enabled,
    staleTime: 0,
    gcTime: 0,
  })

export const useRooms = (params: Params = { include_inactive: true }) =>
  useQuery({
    queryKey: ['rooms', params],
    queryFn: () => api<{ rooms: Room[]; total: number }>('/rooms/', { params }),
  })

export const useOccupancy = (start: string, days: number) =>
  useQuery({
    queryKey: ['occupancy', start, days],
    queryFn: () => api<Occupancy>('/admin/occupancy', { params: { start: start || undefined, days } }),
    placeholderData: keepPreviousData,
  })

export const useConversations = (params: Params) =>
  useQuery({
    queryKey: ['conversations', params],
    queryFn: () =>
      api<{ conversations: ConversationSummary[]; total: number; page: number; pages: number }>(
        '/admin/conversations',
        { params },
      ),
    placeholderData: keepPreviousData,
    refetchInterval: 20_000,
  })

export const useConversation = (sessionId: string | null) =>
  useQuery({
    queryKey: ['conversation', sessionId],
    queryFn: () => api<ConversationDetail>(`/admin/conversations/${sessionId}`),
    enabled: !!sessionId,
    refetchInterval: 10_000,
  })

export const useGuests = (params: Params) =>
  useQuery({
    queryKey: ['guests', params],
    queryFn: () =>
      api<{ guests: Guest[]; total: number; page: number; pages: number }>('/admin/guests', {
        params,
      }),
    placeholderData: keepPreviousData,
  })

export const useGuest = (phone: string | null) =>
  useQuery({
    queryKey: ['guest', phone],
    queryFn: () => api<GuestDetail>('/admin/guests/by-phone', { params: { phone } }),
    enabled: !!phone,
  })

export const useLiveCalls = (enabled = true) =>
  useQuery({
    enabled,
    queryKey: ['live-calls'],
    queryFn: () => api<{ calls: LiveCall[]; total: number }>('/admin/calls/live'),
    refetchInterval: 5_000,
  })

export const useCallerMessages = () =>
  useQuery({
    queryKey: ['caller-messages'],
    queryFn: () => api<{ messages: CallerMessage[]; total: number; new: number }>('/admin/messages'),
    refetchInterval: 30_000,
  })

export const useCallHistory = () =>
  useQuery({
    queryKey: ['call-history'],
    queryFn: () => api<{ calls: CallRecord[]; total: number }>('/admin/calls', { params: { limit: 20 } }),
    refetchInterval: 30_000,
  })

export const useReminders = () =>
  useQuery({ queryKey: ['reminders'], queryFn: () => api<Reminders>('/admin/reminders') })

export const useSystem = () =>
  useQuery({ queryKey: ['system'], queryFn: () => api<SystemInfo>('/admin/system') })

/* ───────────── mutations ───────────── */

/** Anything that changes bookings also changes stats, the grid and the guest views. */
function useInvalidateBookings() {
  const qc = useQueryClient()
  return () => {
    for (const key of ['bookings', 'booking', 'stats', 'occupancy', 'guests', 'guest', 'reminders']) {
      void qc.invalidateQueries({ queryKey: [key] })
    }
  }
}

export interface NewBooking {
  guest_name: string
  guest_phone: string
  guest_email?: string | null
  room_id: string
  check_in_date: string
  check_out_date: string
  adults: number
  children: number
  special_requests?: string | null
}

export function useCreateBooking() {
  const invalidate = useInvalidateBookings()
  return useMutation({
    mutationFn: ({ booking, sendWhatsapp }: { booking: NewBooking; sendWhatsapp: boolean }) =>
      api<{
        booking_id: string
        room_number: string
        total_amount: number
        whatsapp_sent?: boolean
        whatsapp_error?: string
      }>('/bookings/', {
        method: 'POST',
        body: booking,
        params: { send_whatsapp: sendWhatsapp },
      }),
    onSuccess: invalidate,
  })
}

export function useSetBookingStatus() {
  const invalidate = useInvalidateBookings()
  return useMutation({
    mutationFn: ({ bookingId, status }: { bookingId: string; status: string }) =>
      api(`/bookings/${bookingId}/status`, { method: 'PATCH', params: { status } }),
    onSuccess: invalidate,
  })
}

export function useCancelBooking() {
  const invalidate = useInvalidateBookings()
  return useMutation({
    mutationFn: ({ bookingId, reason }: { bookingId: string; reason?: string }) =>
      api<{ cancellation_fee: number; note: string; currency: string }>(`/bookings/${bookingId}`, {
        method: 'DELETE',
        params: { reason },
      }),
    onSuccess: invalidate,
  })
}

export function useUpdateBooking() {
  const invalidate = useInvalidateBookings()
  return useMutation({
    mutationFn: ({
      bookingId,
      ...body
    }: {
      bookingId: string
      guest_name?: string
      guest_email?: string | null
      special_requests?: string | null
      payment_status?: string
      add_note?: string
    }) => api<Booking>(`/bookings/${bookingId}`, { method: 'PATCH', body }),
    onSuccess: invalidate,
  })
}

export const useSendConfirmation = () =>
  useMutation({
    mutationFn: (bookingId: string) =>
      api(`/bookings/${bookingId}/send-confirmation`, { method: 'POST' }),
  })

export function useRoomMutations() {
  const qc = useQueryClient()
  const done = () => {
    for (const key of ['rooms', 'occupancy', 'stats', 'availability']) {
      void qc.invalidateQueries({ queryKey: [key] })
    }
  }
  return {
    create: useMutation({
      mutationFn: (room: Omit<Room, 'id'>) => api('/rooms/', { method: 'POST', body: room }),
      onSuccess: done,
    }),
    update: useMutation({
      mutationFn: ({ id, ...body }: { id: string } & Partial<Omit<Room, 'id'>>) =>
        api<Room>(`/rooms/${id}`, { method: 'PATCH', body }),
      onSuccess: done,
    }),
    deactivate: useMutation({
      mutationFn: (id: string) => api(`/rooms/${id}`, { method: 'DELETE' }),
      onSuccess: done,
    }),
  }
}

export function useUpdateGuest() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      phone,
      ...body
    }: {
      phone: string
      name?: string | null
      email?: string | null
      nationality?: string | null
      vip_tier?: string
      notes?: string | null
    }) => api<Guest>('/admin/guests/by-phone', { method: 'PATCH', params: { phone }, body }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['guests'] })
      void qc.invalidateQueries({ queryKey: ['guest'] })
    },
  })
}

export const useSendWhatsApp = () =>
  useMutation({
    mutationFn: (body: { to: string; message: string }) =>
      api('/admin/whatsapp/send', { method: 'POST', body }),
  })

export function useUpdateMessage() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, status }: { id: string; status: 'new' | 'handled' }) =>
      api(`/admin/messages/${id}`, { method: 'PATCH', body: { status } }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['caller-messages'] }),
  })
}

export function useRunReminders() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api('/admin/reminders/run', { method: 'POST' }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['reminders'] })
      void qc.invalidateQueries({ queryKey: ['bookings'] })
    },
  })
}
