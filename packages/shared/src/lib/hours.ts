/** One opening interval of a business. */
export interface HoursEntry {
  dow: number // 0 = Monday … 6 = Sunday
  open: string // HH:MM
  close: string // HH:MM, may be ≤ open for overnight, or 24:00
}
