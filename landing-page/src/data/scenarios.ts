/**
 * Sample calls for the "Hear it handle real dental calls" section. Each scenario has an AU and a
 * UK version. `at` is seconds from the start, used to reveal lines in step with the (simulated or
 * real) audio. To use a real recording, drop it in public/audio/ and set `audio` to its path.
 * The practices and numbers are fictional (ACMA / Ofcom reserved ranges).
 */
import type { Regional } from './regions';
import { BRAND } from './site';

export type Speaker = 'patient' | 'assistant' | 'system';

/** One tool call in the agent trace beside the transcript. `at` matches the transcript clock. */
export interface TraceStep {
  at: number;
  fn: string;
  result: string;
  status: 'ok' | 'blocked' | 'alert';
}

export interface Line {
  at: number;
  who: Speaker;
  text: string;
}

export interface CallScript {
  title: string;
  context: string;
  lines: Line[];
  outcome: string[];
  trace: TraceStep[];
  duration: number;
  /** e.g. "/audio/implant-price-au.mp3". Leave undefined until a real recording exists. */
  audio?: string;
}

export interface Scenario {
  id: string;
  tab: Regional<string>;
  script: Regional<CallScript>;
}

export const SCENARIOS: Scenario[] = [
  {
    id: 'implant-price',
    tab: { au: 'Implant price guardrail', uk: 'Implant price guardrail' },
    script: {
      au: {
        title: 'Implant price guardrail',
        context: 'Tuesday 6:42 pm, after hours. The caller wants a full-arch price.',
        duration: 24,
        lines: [
          { at: 0, who: 'assistant', text: `Hi, you've reached Harbourline Dental. I'm the practice's AI assistant. How can I help?` },
          { at: 4, who: 'patient', text: 'I saw your ad for All-on-4. Roughly what does a full arch cost?' },
          {
            at: 8,
            who: 'assistant',
            text: `I can't give a treatment price over the phone, because it depends on your scans and the dentist's assessment. The implant consultation is $95, and you leave with a full written plan and costs.`,
          },
          { at: 16, who: 'patient', text: 'Okay. Anything on Thursday morning?' },
          { at: 19, who: 'assistant', text: 'Dr Patel has Thursday at 10:30. Shall I book that in for you?' },
          { at: 22, who: 'patient', text: 'Yes please.' },
        ],
        trace: [
          { at: 0, fn: 'call.answer(mode="after_hours")', result: 'greeting, AI disclosed', status: 'ok' },
          { at: 4.4, fn: 'deepgram.stt(lang="en-AU")', result: '"All-on-4 … full arch cost?"', status: 'ok' },
          { at: 5, fn: 'intent.classify()', result: 'implant_price, high value', status: 'ok' },
          { at: 8, fn: 'guardrails.block_clinical_quote()', result: 'treatment price withheld; consult fee $95 allowed', status: 'blocked' },
          { at: 17, fn: 'cliniko.get_slots(type="implant_consult")', result: 'Thu 10:30, Dr Patel', status: 'ok' },
          { at: 22.5, fn: 'cliniko.book()', result: 'appointment created', status: 'ok' },
          { at: 23, fn: 'leads.qualify()', result: 'implant, est. $4,500', status: 'ok' },
          { at: 23.4, fn: 'sms.send_confirmation()', result: 'delivered', status: 'ok' },
        ],
        outcome: ['Booked in Cliniko: implant consultation, Thursday 10:30', 'Qualified as an implant lead, estimated value $4,500', 'Confirmation text sent'],
      },
      uk: {
        title: 'Implant price guardrail',
        context: 'Tuesday 6:42 pm, after hours. The caller wants a full-arch price.',
        duration: 24,
        lines: [
          { at: 0, who: 'assistant', text: `Hello, you've reached Wimpole Dental Rooms. I'm the practice's AI assistant. How can I help?` },
          { at: 4, who: 'patient', text: 'I saw your advert for All-on-4. Roughly what does a full arch cost?' },
          {
            at: 8,
            who: 'assistant',
            text: `I'm not able to price treatment over the phone, as it depends on your scans and the dentist's assessment. The implant consultation is £75, and you'll leave with a written plan and costs.`,
          },
          { at: 16, who: 'patient', text: 'All right. Anything on Thursday morning?' },
          { at: 19, who: 'assistant', text: 'Dr Okafor has Thursday at 10:30. Shall I book that for you?' },
          { at: 22, who: 'patient', text: 'Yes, please.' },
        ],
        trace: [
          { at: 0, fn: 'call.answer(mode="after_hours")', result: 'greeting, AI disclosed', status: 'ok' },
          { at: 4.4, fn: 'deepgram.stt(lang="en-GB")', result: '"All-on-4 … full arch cost?"', status: 'ok' },
          { at: 5, fn: 'intent.classify()', result: 'implant_price, high value', status: 'ok' },
          { at: 8, fn: 'guardrails.block_clinical_quote()', result: 'treatment price withheld; consult fee £75 allowed', status: 'blocked' },
          { at: 17, fn: 'dentally.get_slots(type="implant_consult")', result: 'Thu 10:30, Dr Okafor', status: 'ok' },
          { at: 22.5, fn: 'dentally.book()', result: 'appointment created', status: 'ok' },
          { at: 23, fn: 'leads.qualify()', result: 'implant, est. £3,200', status: 'ok' },
          { at: 23.4, fn: 'sms.send_confirmation()', result: 'delivered', status: 'ok' },
        ],
        outcome: ['Booked in Dentally: implant consultation, Thursday 10:30', 'Qualified as an implant lead, estimated value £3,200', 'Confirmation text sent'],
      },
    },
  },
  {
    id: 'regional',
    tab: { au: 'HICAPS health fund', uk: 'NHS-to-private pivot' },
    script: {
      au: {
        title: 'HICAPS and health fund enquiry',
        context: 'Monday 1:05 pm, lunch break. A new patient asks about claiming.',
        duration: 22,
        lines: [
          { at: 0, who: 'patient', text: "Hi, do you take Bupa? And can I claim on the spot?" },
          {
            at: 4,
            who: 'assistant',
            text: 'Yes. We have HICAPS, so you can claim from Bupa or your other health fund at the end of the visit and pay only the gap. What you get back depends on your level of cover.',
          },
          { at: 13, who: 'patient', text: 'Great. I need a check-up and clean.' },
          { at: 16, who: 'assistant', text: 'Our next new-patient check-up and clean is Wednesday at 3:15. Would that suit?' },
          { at: 20, who: 'patient', text: 'Perfect.' },
        ],
        trace: [
          { at: 0, fn: 'call.answer(mode="lunch")', result: 'lunch greeting, AI disclosed', status: 'ok' },
          { at: 0.6, fn: 'deepgram.stt(lang="en-AU")', result: '"do you take Bupa?"', status: 'ok' },
          { at: 1.2, fn: 'intent.classify()', result: 'health_fund_enquiry', status: 'ok' },
          { at: 4, fn: 'practice.facts("hicaps")', result: 'on-the-spot claiming, gap only', status: 'ok' },
          { at: 14, fn: 'cliniko.get_slots(type="new_patient")', result: 'Wed 3:15', status: 'ok' },
          { at: 20.5, fn: 'cliniko.book()', result: 'appointment created', status: 'ok' },
          { at: 21, fn: 'booking.note(fund="Bupa")', result: 'attached', status: 'ok' },
        ],
        outcome: ['Booked in Cliniko: new patient exam and clean, Wednesday 3:15', 'Health fund noted on the booking'],
      },
      uk: {
        title: 'NHS-to-private pivot',
        context: 'Monday 1:05 pm, lunch break. The caller wants an NHS dentist.',
        duration: 24,
        lines: [
          { at: 0, who: 'patient', text: 'Hi, are you taking on new NHS patients?' },
          {
            at: 3,
            who: 'assistant',
            text: "We're not taking new NHS patients at the moment, I'm afraid. We do see private patients, and Denplan is available if you'd like to spread the cost. A new-patient examination is £75.",
          },
          { at: 12, who: 'patient', text: "What if I'd rather stay NHS?" },
          { at: 15, who: 'assistant', text: 'NHS 111 or the NHS website can help you find a practice nearby. Or I can book you in privately this week.' },
          { at: 21, who: 'patient', text: "Let's do private, then." },
        ],
        trace: [
          { at: 0, fn: 'call.answer(mode="lunch")', result: 'lunch greeting, AI disclosed', status: 'ok' },
          { at: 0.6, fn: 'deepgram.stt(lang="en-GB")', result: '"new NHS patients?"', status: 'ok' },
          { at: 1.2, fn: 'intent.classify()', result: 'nhs_enquiry', status: 'ok' },
          { at: 3, fn: 'practice.facts("nhs_list")', result: 'not accepting; private and Denplan offered', status: 'ok' },
          { at: 15, fn: 'signpost("NHS 111")', result: 'offered without pressure', status: 'ok' },
          { at: 21.5, fn: 'dentally.get_slots(type="private_np_exam")', result: 'Thu 9:00', status: 'ok' },
          { at: 22.5, fn: 'dentally.book()', result: 'appointment created', status: 'ok' },
          { at: 23, fn: 'booking.note(denplan_interest=true)', result: 'attached', status: 'ok' },
        ],
        outcome: ['Booked in Dentally: private new-patient examination, Thursday 9:00', 'Denplan interest noted for the team'],
      },
    },
  },
  {
    id: 'emergency',
    tab: { au: 'Emergency 000 red flag', uk: 'Emergency 999 red flag' },
    script: {
      au: {
        title: 'Red-flag emergency triage',
        context: 'Saturday 9:20 pm. The caller describes swelling that affects swallowing.',
        duration: 16,
        lines: [
          { at: 0, who: 'patient', text: "My face has swollen up since this morning and it's getting hard to swallow." },
          {
            at: 4,
            who: 'assistant',
            text: "Please hang up and call 000 now. Swelling that affects swallowing needs emergency care straight away. I'm alerting the practice so they can follow up with you.",
          },
          { at: 12, who: 'patient', text: 'Okay, calling now.' },
        ],
        trace: [
          { at: 0.5, fn: 'deepgram.stt(lang="en-AU")', result: '"swollen … hard to swallow"', status: 'ok' },
          { at: 1.5, fn: 'triage.red_flags()', result: 'swelling + swallowing: RED', status: 'alert' },
          { at: 4, fn: 'guardrails.emergency_first()', result: 'opens with "call 000"', status: 'alert' },
          { at: 6, fn: 'alerts.notify_owner(priority="urgent")', result: 'urgent alert sent', status: 'ok' },
          { at: 7, fn: 'booking.skip()', result: 'no booking on a red flag', status: 'blocked' },
        ],
        outcome: ['Told to call 000 in the first sentence', 'Urgent alert sent to the practice owner', 'No booking attempted'],
      },
      uk: {
        title: 'Red-flag emergency triage',
        context: 'Saturday 9:20 pm. The caller describes swelling that affects swallowing.',
        duration: 16,
        lines: [
          { at: 0, who: 'patient', text: "My face has swollen up since this morning and it's getting hard to swallow." },
          {
            at: 4,
            who: 'assistant',
            text: "Please hang up and call 999 now. Swelling that affects swallowing needs emergency care straight away. I'm alerting the practice so they can follow up with you.",
          },
          { at: 12, who: 'patient', text: 'Okay, calling now.' },
        ],
        trace: [
          { at: 0.5, fn: 'deepgram.stt(lang="en-GB")', result: '"swollen … hard to swallow"', status: 'ok' },
          { at: 1.5, fn: 'triage.red_flags()', result: 'swelling + swallowing: RED', status: 'alert' },
          { at: 4, fn: 'guardrails.emergency_first()', result: 'opens with "call 999"', status: 'alert' },
          { at: 6, fn: 'alerts.notify_owner(priority="urgent")', result: 'urgent alert sent', status: 'ok' },
          { at: 7, fn: 'booking.skip()', result: 'no booking on a red flag', status: 'blocked' },
        ],
        outcome: ['Told to call 999 in the first sentence', 'Urgent alert sent to the practice owner', 'Non-urgent problems out of hours are pointed to NHS 111'],
      },
    },
  },
  {
    id: 'text-back',
    tab: { au: '15 s abandoned-call text', uk: '15 s abandoned-call text' },
    script: {
      au: {
        title: 'Abandoned call, recovered by text',
        context: 'Thursday 12:58 pm. The caller hangs up during the greeting.',
        duration: 14,
        lines: [
          { at: 0, who: 'system', text: '12:58:03  Incoming call from 0491 570 156' },
          { at: 3, who: 'system', text: '12:58:06  Caller hung up before the greeting finished' },
          {
            at: 8,
            who: 'system',
            text: '12:58:16  Text sent: "Sorry we missed you at Harbourline Dental. Book online at harbourline.example/book or call us back on (02) 5550 4120. Reply STOP to opt out."',
          },
        ],
        trace: [
          { at: 0, fn: 'call.inbound()', result: 'ringing, forwarded', status: 'ok' },
          { at: 3, fn: 'call.ended(reason="abandoned")', result: 'hung up during greeting', status: 'alert' },
          { at: 4, fn: 'sms.rate_limit("1 per 24h")', result: 'first text today: allowed', status: 'ok' },
          { at: 8, fn: 'sms.send_recovery()', result: 'delivered, STOP included', status: 'ok' },
          { at: 9, fn: 'roi.count_recovery()', result: 'logged to dashboard', status: 'ok' },
        ],
        outcome: ['Text sent 10 seconds after the hang-up', 'One text per caller per 24 hours; STOP honoured', 'Counted as a recovery in the ROI dashboard'],
      },
      uk: {
        title: 'Abandoned call, recovered by text',
        context: 'Thursday 12:58 pm. The caller hangs up during the greeting.',
        duration: 14,
        lines: [
          { at: 0, who: 'system', text: '12:58:03  Incoming call from 07700 900 418' },
          { at: 3, who: 'system', text: '12:58:06  Caller hung up before the greeting finished' },
          {
            at: 8,
            who: 'system',
            text: '12:58:16  Text sent: "Sorry we missed you at Wimpole Dental Rooms. Book online at wimpole.example/book or call us back on 020 7946 0418. Reply STOP to opt out."',
          },
        ],
        trace: [
          { at: 0, fn: 'call.inbound()', result: 'ringing, forwarded', status: 'ok' },
          { at: 3, fn: 'call.ended(reason="abandoned")', result: 'hung up during greeting', status: 'alert' },
          { at: 4, fn: 'sms.rate_limit("1 per 24h")', result: 'first text today: allowed', status: 'ok' },
          { at: 8, fn: 'sms.send_recovery()', result: 'delivered, STOP included', status: 'ok' },
          { at: 9, fn: 'roi.count_recovery()', result: 'logged to dashboard', status: 'ok' },
        ],
        outcome: ['Text sent 10 seconds after the hang-up', 'One text per caller per 24 hours; STOP honoured', 'Counted as a recovery in the ROI dashboard'],
      },
    },
  },
];

export const SPEAKER_LABEL: Record<Speaker, string> = {
  patient: 'Caller',
  assistant: BRAND,
  system: 'System',
};
