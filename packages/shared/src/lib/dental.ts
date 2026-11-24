/**
 * Dental practices: the only business LaudLine sells to, in Australia and the UK only.
 * The practice types mirror PRACTICE_TYPES in AI-receptionist-1/core/prompts/verticals/dental.py and are
 * stored as `dental.practice_type`. `country` limits a type to one country.
 */

export type DentalCountry = 'AU' | 'GB'

export const DENTAL_COUNTRIES: { value: DentalCountry; label: string }[] = [
  { value: 'AU', label: 'Australia' },
  { value: 'GB', label: 'United Kingdom' },
]

export const DENTAL_PRACTICE_TYPES: { value: string; label: string; country?: DentalCountry }[] = [
  { value: 'private', label: 'Private general practice' },
  { value: 'mixed_nhs', label: 'Mixed NHS and private', country: 'GB' },
  { value: 'mixed_public', label: 'Mixed private and public (e.g. CDBS)', country: 'AU' },
  { value: 'implant_cosmetic', label: 'Implant and cosmetic clinic' },
  { value: 'specialist', label: 'Specialist practice (orthodontics, perio, endo)' },
]

/** The practice types offered for a country (all of them when the country isn't AU or GB). */
export function practiceTypesFor(country: string) {
  const c = country.toUpperCase()
  return DENTAL_PRACTICE_TYPES.filter((t) => !t.country || t.country === c || (c !== 'AU' && c !== 'GB'))
}

/** A sensible timezone for a new practice in that country. */
export function defaultTimezone(country: string): string {
  return country.toUpperCase() === 'GB' ? 'Europe/London' : 'Australia/Sydney'
}
