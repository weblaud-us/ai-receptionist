import type { Regional } from './regions';
import { BRAND } from './site';

export interface Faq {
  q: string;
  a: Regional<string>;
}

const same = (s: string): Regional<string> => ({ au: s, uk: s });

export const FAQS: Faq[] = [
  {
    q: 'Will it sound robotic to my patients?',
    a: {
      au: `It speaks with a natural Australian voice, replies in about a second and understands dental words like Invisalign, zirconia and periodontal. It says it's an AI assistant at the start of every call, because patients should know. Play the sample calls above and judge for yourself.`,
      uk: `It speaks with a natural British voice, replies in about a second and understands dental words like Invisalign, zirconia and periodontal. It says it's an AI assistant at the start of every call, because patients should know. Play the sample calls above and judge for yourself.`,
    },
  },
  {
    q: 'Will my receptionists feel threatened?',
    a: same(
      `It takes the calls they can't: when every line is busy, over lunch and after you close. Your team keeps the front desk, and instead of a voicemail backlog they find consults already booked, with notes, in the diary. It's cover, and that's the honest way to introduce it to your team.`,
    ),
  },
  {
    q: 'How does it connect to Cliniko or Dentally?',
    a: {
      au: `Paste a Cliniko API key into the dashboard. ${BRAND} reads your practitioners, appointment types and live availability, and books straight into the right column of your diary. Dentally is in early access; until it's switched on for your practice, the built-in diary syncs with Google Calendar.`,
      uk: `Dentally is in early access: we connect it during onboarding once your practice is approved. Cliniko practices paste an API key into the dashboard and ${BRAND} books straight into the diary. In the meantime the built-in diary syncs with Google Calendar.`,
    },
  },
  {
    q: 'What if a patient has an emergency, or asks for an exact price?',
    a: {
      au: `If a caller describes swelling that affects breathing or swallowing, heavy bleeding or facial trauma, the first thing it says is to call 000, and it alerts the practice straight away. Other urgent problems get your earliest same-day slot, and out of hours they're also pointed to Healthdirect on 1800 022 222. It never estimates treatment costs or gives clinical advice; it quotes only the consultation fees and “from” prices you set.`,
      uk: `If a caller describes swelling that affects breathing or swallowing, heavy bleeding or facial trauma, the first thing it says is to call 999, and it alerts the practice straight away. Out of hours, other urgent problems are pointed to NHS 111. It never estimates treatment costs or gives clinical advice; it quotes only the consultation fees and “from” prices you set.`,
    },
  },
  {
    q: 'How do we forward calls only at lunch or when we’re busy?',
    a: same(
      `Most phone systems support conditional forwarding: forward when the line is busy, or when nobody answers after a set number of rings (four works well). Set your opening hours and lunch break in the dashboard, and callers hear a lunch-break or after-hours greeting at the right times. We set up the forwarding with you during onboarding.`,
    ),
  },
];
