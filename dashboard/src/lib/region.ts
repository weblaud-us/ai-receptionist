/** Per-country facts the settings pages show: dialling code, emergency number, example phone, call notice rules. */

export interface RegionInfo {
  /** International dialling code, e.g. "+61". */
  dial: string
  /** The number callers ring in an emergency. */
  emergency: string
  /** A placeholder phone number in international format. */
  phoneExample: string
  /** One line on telling callers their call is transcribed, where the business is. */
  callNote: string
}

const GDPR_NOTE =
  'The greeting tells callers the call is transcribed, as GDPR transparency expects. Keep transcripts no longer than you need — check with your adviser.'

const REGION: Record<string, RegionInfo> = {
  AU: {
    dial: '+61', emergency: '000', phoneExample: '+61 2 9000 0000',
    callNote: 'The greeting always tells callers the call is transcribed: Australian states require callers to be told, and some need their consent — check your state\'s rules.',
  },
  NZ: {
    dial: '+64', emergency: '111', phoneExample: '+64 9 000 0000',
    callNote: 'Add a notice below if you want callers told the call is transcribed, as the Privacy Act expects. Check with your adviser.',
  },
  GB: { dial: '+44', emergency: '999', phoneExample: '+44 20 7946 0000', callNote: GDPR_NOTE },
  IE: { dial: '+353', emergency: '112', phoneExample: '+353 1 000 0000', callNote: GDPR_NOTE },
  US: {
    dial: '+1', emergency: '911', phoneExample: '+1 512 555 0100',
    callNote: 'Some US states require every party\'s consent to a call being transcribed — add a notice below, and check with your lawyer.',
  },
  CA: {
    dial: '+1', emergency: '911', phoneExample: '+1 416 555 0100',
    callNote: 'Add a notice below if provincial privacy rules expect callers to be told. Check with your lawyer.',
  },
}

/** Facts for a business's country (ISO code). Unknown countries get European defaults without a dialling code. */
export function regionFor(country: string | null | undefined): RegionInfo {
  const code = (country ?? '').toUpperCase()
  if (code === 'UK') return REGION.GB
  return REGION[code] ?? { dial: '', emergency: '112', phoneExample: '+44 20 7946 0000', callNote: GDPR_NOTE }
}
