export type BookingStatus =
  | 'pending'
  | 'confirmed'
  | 'checked_in'
  | 'checked_out'
  | 'cancelled'
  | 'no_show'

export type PaymentStatus = 'pending' | 'partial' | 'paid'
export type RoomType = 'standard' | 'deluxe' | 'suite' | 'executive' | 'presidential'
export type BedType = 'single' | 'double' | 'queen' | 'king' | 'twin'
export type Channel = 'voice' | 'whatsapp'
export type VipTier = 'standard' | 'silver' | 'gold' | 'platinum'

export const BOOKING_STATUSES: BookingStatus[] = [
  'pending',
  'confirmed',
  'checked_in',
  'checked_out',
  'cancelled',
  'no_show',
]
export const PAYMENT_STATUSES: PaymentStatus[] = ['pending', 'partial', 'paid']
export const ROOM_TYPES: RoomType[] = ['standard', 'deluxe', 'suite', 'executive', 'presidential']
export const BED_TYPES: BedType[] = ['single', 'double', 'queen', 'king', 'twin']
export const VIP_TIERS: VipTier[] = ['standard', 'silver', 'gold', 'platinum']

export interface PricingBreakdown {
  base_total: number
  taxes: number
  discounts: number
  final_total: number
  per_night_avg: number
  nights: number
  currency: string
}

export interface Booking {
  id: string
  booking_id: string
  guest_phone: string
  guest_name: string
  guest_email?: string | null
  room_id: string
  room_number: string
  room_type: RoomType
  check_in_date: string
  check_out_date: string
  adults: number
  children: number
  special_requests?: string | null
  pricing: PricingBreakdown
  status: BookingStatus
  payment_status: PaymentStatus
  source: string
  booking_notes: string[]
  calendar_token?: string | null
  one_day_reminder_sent?: boolean
  one_day_reminder_sent_at?: string | null
  one_day_reminder_error?: string | null
  whatsapp_confirmation_sent?: boolean
  created_at: string
  updated_at: string
  checked_in_at?: string | null
  checked_out_at?: string | null
}

export interface BookingList {
  bookings: Booking[]
  total: number
  page: number
  pages: number
}

export interface RoomAmenities {
  wifi: boolean
  ac: boolean
  tv: boolean
  minibar: boolean
  balcony: boolean
  sea_view: boolean
  jacuzzi: boolean
  kitchen: boolean
  workspace: boolean
  breakfast_included: boolean
}

export interface RoomPricing {
  base_price_per_night: number
  weekend_surcharge_pct: number
  peak_season_surcharge_pct: number
  discount_weekly_pct: number
  discount_monthly_pct: number
  tax_pct: number
}

export interface Room {
  id: string
  room_number: string
  room_type: RoomType
  bed_type: BedType
  floor: number
  capacity: number
  size_sqft?: number | null
  description: string
  amenities: RoomAmenities
  pricing: RoomPricing
  images: string[]
  is_active: boolean
  is_maintenance: boolean
  created_at?: string
  updated_at?: string
}

export interface AvailableRoom {
  room_id: string
  room_number: string
  room_type: RoomType
  bed_type: BedType
  floor: number
  capacity: number
  price_per_night: number
  total_price: number
  currency: string
  description: string
}

export interface Availability {
  available: boolean
  error?: string
  rooms: AvailableRoom[]
  nights: number
  total_available: number
  alternative_room_types: { room_type: RoomType; room_number: string; price_per_night: number; total_price: number }[]
  alternative_dates: { check_in_date: string; check_out_date: string; available_rooms: number }[]
}

export interface MiniBooking {
  booking_id: string
  guest_name: string
  guest_phone: string
  room_number: string
  room_type: RoomType
  status: BookingStatus
  check_in_date: string
  check_out_date: string
  payment_status: PaymentStatus
  total: number | null
}

export interface Stats {
  as_of: string
  hotel_today: string
  currency: string
  period_days: number
  rooms: {
    total: number
    maintenance: number
    sellable: number
    occupied: number
    available: number
    occupancy_pct: number
  }
  today: { arrivals: MiniBooking[]; departures: MiniBooking[]; in_house: number }
  bookings: {
    by_status: Record<string, number>
    period_total: number
    period_cancelled: number
    cancellation_rate_pct: number
    by_source: { name: string; count: number; revenue: number }[]
    by_room_type: { name: string; count: number; revenue: number }[]
  }
  revenue: { period_total: number; avg_booking_value: number }
  series: { date: string; bookings: number; revenue: number }[]
  upcoming_check_ins: { date: string; check_ins: number }[]
  conversations: {
    period_total: number
    by_channel: Record<string, number>
    converted: number
    conversion_pct: number
    live_calls: number
  }
  guests: { total: number; new_in_period: number }
  attention: { reminders_failed: number; pending_bookings: number; unpaid_soon: number }
}

export interface Occupancy {
  today: string
  start: string
  end: string
  days: number
  rooms: { id: string; room_number: string; room_type: RoomType; floor: number; capacity: number; is_maintenance: boolean }[]
  bookings: {
    booking_id: string
    room_id: string
    guest_name: string
    status: BookingStatus
    check_in_date: string
    check_out_date: string
  }[]
}

export interface ToolCall {
  name: string
  args: Record<string, unknown>
  result: string
}

export interface ConversationMessage {
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  timestamp?: string
  tool_calls?: ToolCall[] | null
}

export interface ConversationSummary {
  session_id: string
  phone: string
  channel: Channel
  status: 'active' | 'completed' | 'abandoned'
  call_sid?: string | null
  created_at: string
  updated_at: string
  ended_at?: string | null
  duration_sec?: number | null
  booking_ids: string[]
  guest_name?: string | null
  stage?: string | null
  message_count: number
  last_message: { role: string; content: string; timestamp?: string } | null
}

export interface ConversationDetail extends Omit<ConversationSummary, 'message_count' | 'last_message'> {
  messages: ConversationMessage[]
  context: Record<string, unknown>
  bookings: Booking[]
}

export interface Guest {
  phone: string
  name?: string | null
  email?: string | null
  nationality?: string | null
  vip_tier: VipTier
  total_stays: number
  notes?: string | null
  booking_count: number
  total_spent: number
  created_at?: string
  last_interaction?: string | null
}

export interface GuestDetail extends Guest {
  bookings: Booking[]
  conversations: Omit<ConversationSummary, 'phone' | 'last_message' | 'guest_name' | 'stage'>[]
}

export interface LiveCall {
  call_sid: string
  phone: string
  session_id: string
  stream_connected: boolean
  ai_speaking: boolean
  processing: boolean
  seconds_since_speech: number
  source?: 'twilio' | 'livekit'
  channel?: 'phone' | 'browser'
}

/** A message a caller left for the team (voice agent / WhatsApp `take_message`). */
export interface CallerMessage {
  message_id: string
  caller_name: string
  callback_number: string
  message: string
  urgent: boolean
  channel: string
  call_id: string | null
  status: 'new' | 'handled'
  created_at: string
}

/** A voice-agent call (LiveKit), without transcript. */
export interface CallRecord {
  call_id: string
  channel: 'phone' | 'browser'
  caller_phone: string | null
  started_at: string
  duration_s?: number
  status: 'in_progress' | 'completed'
  outcome: string | null
  latency?: { turns: number; p50_s: number | null; p95_s: number | null; tool_p50_s: number | null }
  greeting_latency_s?: number | null
}

export interface ReminderRow {
  booking_id: string
  guest_name: string
  guest_phone: string
  room_number: string
  check_in_date: string
  state: 'sent' | 'pending' | 'failed'
  sent_at?: string | null
  attempted_at?: string | null
  error?: string | null
}

export interface Reminders {
  enabled: boolean
  poll_minutes: number
  reminder_for_date: string
  tomorrow: ReminderRow[]
  failed: ReminderRow[]
  recent_sent: ReminderRow[]
}

export interface SystemInfo {
  hotel: {
    name: string
    receptionist_name: string
    phone: string
    address: string
    checkin_time: string
    checkout_time: string
    currency: string
    timezone: string
  }
  environment: { app_env: string; debug: boolean; llm_model: string; db_name: string }
  database: { connected: boolean }
  integrations: Record<string, boolean>
  webhooks: Record<string, string>
  features: Record<string, boolean>
  memory: { days: number; max_messages: number }
  collections: Record<string, number>
}
