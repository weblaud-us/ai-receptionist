/**
 * Sample calls for the "Hear it handle real dental calls" section. Each scenario has an AU and a
 * UK version. `at` is seconds from the start, used to reveal lines in step with the (simulated or
 * real) audio. To use a real recording, drop it in public/audio/ and set `audio` to its path.
 * The practices and numbers are fictional (ACMA / Ofcom reserved ranges).
 */
import type { Regional } from './regions';
import { BRAND } from './site';

export type Speaker = 'patient' | 'assistant' | 'system';

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
    tab: { au: 'Implant price question', uk: 'Implant price question' },
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
        outcome: ['Booked in Dentally: implant consultation, Thursday 10:30', 'Qualified as an implant lead, estimated value £3,200', 'Confirmation text sent'],
      },
    },
  },
  {
    id: 'regional',
    tab: { au: 'Health fund question', uk: 'NHS to private' },
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
        outcome: ['Booked in Dentally: private new-patient examination, Thursday 9:00', 'Denplan interest noted for the team'],
      },
    },
  },
  {
    id: 'emergency',
    tab: { au: 'Emergency red flag', uk: 'Emergency red flag' },
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
        outcome: ['Told to call 999 in the first sentence', 'Urgent alert sent to the practice owner', 'Non-urgent problems out of hours are pointed to NHS 111'],
      },
    },
  },
  {
    id: 'text-back',
    tab: { au: 'Missed-call text-back', uk: 'Missed-call text-back' },
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
