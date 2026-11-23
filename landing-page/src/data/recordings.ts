/**
 * Real sample-call audio, when it has been rendered (AI-receptionist-1/scripts/render_sample_calls.py
 * writes public/audio/<id>-<region>.mp3 and recordings/<id>-<region>.json). A recorded call plays
 * the file, and its transcript and agent trace move to the recording's real timing. Without a recording
 * a call keeps its scripted timing and the simulated clock.
 */
import type { CallScript } from './scenarios';
import type { Region } from './regions';

interface Timing {
  duration: number;
  /** Start of each transcript line, in seconds. */
  at: number[];
  /** File name in public/audio/. */
  audio: string;
}

const files = import.meta.glob<Timing>('./recordings/*.json', { eager: true, import: 'default' });
const TIMINGS: Record<string, Timing> = Object.fromEntries(
  Object.entries(files).map(([path, t]) => [path.replace(/^.*\/|\.json$/g, ''), t]),
);

/** Map a time on the scripted clock to the recording's clock, piecewise between transcript lines. */
function retimer(from: number[], to: number[]) {
  return (t: number) => {
    if (t <= from[0]) return to[0] + (t - from[0]);
    for (let i = 1; i < from.length; i++) {
      if (t <= from[i]) {
        const span = from[i] - from[i - 1] || 1;
        return to[i - 1] + ((t - from[i - 1]) / span) * (to[i] - to[i - 1]);
      }
    }
    return to[to.length - 1] + (t - from[from.length - 1]);
  };
}

export function withRecording(id: string, region: Region, script: CallScript): CallScript {
  const timing = TIMINGS[`${id}-${region}`];
  if (script.audio || !timing || timing.at.length !== script.lines.length) return script;
  const from = [...script.lines.map((l) => l.at), script.duration];
  const to = [...timing.at, timing.duration];
  const map = retimer(from, to);
  const round = (t: number) => Math.max(0, Math.round(t * 10) / 10);
  return {
    ...script,
    audio: `/audio/${timing.audio}`,
    duration: Math.ceil(timing.duration),
    lines: script.lines.map((l, i) => ({ ...l, at: round(timing.at[i]) })),
    trace: script.trace.map((s) => ({ ...s, at: round(map(s.at)) })),
  };
}
