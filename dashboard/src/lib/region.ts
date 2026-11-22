/** Per-country facts the settings pages show: dialling code, emergency number, example phone, recording rules. */

export interface RegionInfo {
  /** International dialling code, e.g. "+61". */
  dial: string
  /** The number callers ring in an emergency. */
  emergency: string
  /** A placeholder phone number in international format. */
  phoneExample: string
  /** One line on call-recording consent where the business is. */
  recordingNote: string
}

const GDPR_NOTE =
  'The greeting then announces recording. Under GDPR you also need a lawful reason to record and should keep recordings no longer than you need — check with your adviser.'

const REGION: Record<string, RegionInfo> = {
  AU: {
    dial: '+61', emergency: '000', phoneExample: '+61 2 9000 0000',
    recordingNote: 'The greeting then announces recording. Australian states require callers to be told, and some need their consent — check your state\'s rules.',
  },
  NZ: {
    dial: '+64', emergency: '111', phoneExample: '+64 9 000 0000',
    recordingNote: 'The greeting then announces recording, as the Privacy Act expects. Check with your adviser.',
  },
  GB: { dial: '+44', emergency: '999', phoneExample: '+44 20 7946 0000', recordingNote: GDPR_NOTE },
  IE: { dial: '+353', emergency: '112', phoneExample: '+353 1 000 0000', recordingNote: GDPR_NOTE },
  US: {
    dial: '+1', emergency: '911', phoneExample: '+1 512 555 0100',
    recordingNote: 'The greeting then announces recording. Some US states require every party\'s consent — check with your lawyer.',
  },
  CA: {
    dial: '+1', emergency: '911', phoneExample: '+1 416 555 0100',
    recordingNote: 'The greeting then announces recording. Check provincial privacy rules with your lawyer.',
  },
}

/** Facts for a business's country (ISO code). Unknown countries get European defaults without a dialling code. */
export function regionFor(country: string | null | undefined): RegionInfo {
  const code = (country ?? '').toUpperCase()
  if (code === 'UK') return REGION.GB
  return REGION[code] ?? { dial: '', emergency: '112', phoneExample: '+44 20 7946 0000', recordingNote: GDPR_NOTE }
}
