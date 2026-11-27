import { useState } from 'react'
import { cn } from '@receptionist/shared/lib/utils'
import { curveBars, heatmapCells, type LearningPage, type PickupTimes } from '@/lib/learning'

/**
 * Small charts for the sales dialer, in plain SVG (no chart library in this app).
 * Colours: the validated reference categorical slots 1 (blue) and 2 (orange), stepped per mode;
 * text stays in the text colours; one y-axis (both series are rates of the same base).
 */
const SERIES = {
  next: 'stroke-[#2a78d6] fill-[#2a78d6] dark:stroke-[#3987e5] dark:fill-[#3987e5]',
  meet: 'stroke-[#eb6834] fill-[#eb6834] dark:stroke-[#d95926] dark:fill-[#d95926]',
}
const SWATCH = { next: 'bg-[#2a78d6] dark:bg-[#3987e5]', meet: 'bg-[#eb6834] dark:bg-[#d95926]' }

export function LearningCurve({ curve }: { curve: LearningPage['curve'] }) {
  const bars = curveBars(curve.points)
  const [hover, setHover] = useState<number | null>(null)
  if (bars.length === 0) {
    return <p className="rounded-md border border-dashed p-4 text-center text-xs text-muted-foreground">No AI calls for this offer in the last 12 weeks yet.</p>
  }
  const W = 640, H = 180, L = 36, R = 64, T = 12, B = 24
  const top = Math.max(25, Math.ceil(Math.max(...bars.map((b) => Math.max(b.next, b.meet))) / 25) * 25)
  const x = (i: number) => (bars.length === 1 ? L + (W - L - R) / 2 : L + (i * (W - L - R)) / (bars.length - 1))
  const y = (v: number) => T + (H - T - B) * (1 - v / top)
  const path = (key: 'next' | 'meet') => bars.map((b, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(b[key]).toFixed(1)}`).join(' ')
  const weekOf = (at: string) => {
    const t = new Date(at).getTime()
    let best = 0
    bars.forEach((b, i) => { if (Math.abs(new Date(`${b.week}T00:00:00Z`).getTime() - t) < Math.abs(new Date(`${bars[best].week}T00:00:00Z`).getTime() - t)) best = i })
    return best
  }
  const step = Math.max(1, Math.ceil(bars.length / 6))
  const last = bars.length - 1
  const h = hover != null ? bars[hover] : null
  return (
    <figure className="space-y-2">
      <div className="flex flex-wrap gap-4 text-xs text-muted-foreground" aria-hidden>
        <span className="flex items-center gap-1.5"><span className={`inline-block h-0.5 w-4 rounded ${SWATCH.next}`} /> Took a real next step</span>
        <span className="flex items-center gap-1.5"><span className={`inline-block h-0.5 w-4 rounded ${SWATCH.meet}`} /> Booked a meeting</span>
      </div>
      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img"
          aria-label={`Weekly next-step and meeting rates for the last ${bars.length} weeks`} onMouseLeave={() => setHover(null)}>
          {[0, top / 2, top].map((v) => (
            <g key={v}>
              <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} className="stroke-border" strokeWidth={1} />
              <text x={L - 6} y={y(v) + 3} textAnchor="end" className="fill-muted-foreground text-[10px]">{v}%</text>
            </g>
          ))}
          {curve.marks.map((mk, i) => {
            const mx = x(weekOf(mk.at))
            return (
              <g key={i}>
                <line x1={mx} x2={mx} y1={T} y2={H - B} className={mk.kind === 'rolled_back' ? 'stroke-destructive/60' : 'stroke-muted-foreground/50'} strokeDasharray="3 3" strokeWidth={1} />
                <text x={mx + 3} y={T + 8} className="fill-muted-foreground text-[9px]">v{mk.playbook} {mk.kind === 'live' ? 'live' : 'rolled back'}</text>
              </g>
            )
          })}
          {hover != null && <line x1={x(hover)} x2={x(hover)} y1={T} y2={H - B} className="stroke-muted-foreground/40" strokeWidth={1} />}
          {(['next', 'meet'] as const).map((key) => (
            <g key={key}>
              <path d={path(key)} className={`${SERIES[key]} fill-none`} style={{ fill: 'none' }} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
              {bars.map((b, i) => (
                <circle key={i} cx={x(i)} cy={y(b[key])} r={hover === i ? 4.5 : 3.5} className={`${SERIES[key]} stroke-background`} strokeWidth={2} />
              ))}
              <text x={x(last) + 8} y={y(bars[last][key]) + 3} className="fill-foreground text-[10px] font-medium">{bars[last][key]}%</text>
            </g>
          ))}
          {bars.map((b, i) => (i % step === 0 || i === last) && (
            <text key={b.week} x={x(i)} y={H - 6} textAnchor="middle" className="fill-muted-foreground text-[10px]">{b.label}</text>
          ))}
          {bars.map((b, i) => (
            <rect key={`hit-${b.week}`} x={x(i) - (W - L - R) / Math.max(1, bars.length - 1) / 2} y={T} height={H - T - B}
              width={(W - L - R) / Math.max(1, bars.length - 1)} fill="transparent" onMouseEnter={() => setHover(i)} />
          ))}
        </svg>
        {h && hover != null && (
          <div className="pointer-events-none absolute top-0 rounded-md border bg-popover px-2 py-1 text-xs shadow-sm"
            style={{ left: `${(x(hover) / W) * 100}%`, transform: hover > bars.length / 2 ? 'translateX(-105%)' : 'translateX(5%)' }}>
            <div className="font-medium">Week of {h.label}</div>
            <div>{h.reached} businesses reached</div>
            <div className="flex items-center gap-1"><span className={`inline-block size-2 rounded-full ${SWATCH.next}`} /> {h.next}% next step</div>
            <div className="flex items-center gap-1"><span className={`inline-block size-2 rounded-full ${SWATCH.meet}`} /> {h.meet}% meetings</div>
          </div>
        )}
      </div>
      <details className="text-xs">
        <summary className="cursor-pointer text-muted-foreground">Show as a table</summary>
        <table className="mt-1 w-full text-left">
          <thead><tr className="text-muted-foreground"><th className="font-normal">Week of</th><th className="font-normal">Reached</th><th className="font-normal">Next step</th><th className="font-normal">Meetings</th></tr></thead>
          <tbody>{bars.map((b) => <tr key={b.week}><td>{b.label}</td><td className="tabular-nums">{b.reached}</td><td className="tabular-nums">{b.next}%</td><td className="tabular-nums">{b.meet}%</td></tr>)}</tbody>
        </table>
      </details>
    </figure>
  )
}

// Sequential single-hue ramp (reference blue 100 → 650); dark mode steps run the other way so that
// "few answers" recedes into the dark surface.
const RAMP = [
  'bg-[#cde2fb] dark:bg-[#0d366b]', 'bg-[#86b6ef] dark:bg-[#1c5cab]', 'bg-[#3987e5] dark:bg-[#2a78d6]',
  'bg-[#256abf] dark:bg-[#5598e7]', 'bg-[#104281] dark:bg-[#9ec5f4]',
]
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

export function PickupHeatmap({ data }: { data: PickupTimes }) {
  const [hover, setHover] = useState<string | null>(null)
  const cells = heatmapCells(data.cells)
  const hours = [...new Set(cells.map((c) => c.hour))].sort((a, b) => a - b)
  const byKey = new Map(cells.map((c) => [`${c.dow}-${c.hour}`, c]))
  if (!data.dials) return <p className="rounded-md border border-dashed p-4 text-center text-xs text-muted-foreground">No first calls to {data.country} businesses in the last {data.days} days yet.</p>
  const h = hover ? byKey.get(hover) : undefined
  return (
    <figure className="space-y-2">
      <div className="overflow-x-auto">
        <div className="inline-grid gap-0.5 text-[10px]" style={{ gridTemplateColumns: `2.5rem repeat(${hours.length}, minmax(1.5rem, 1fr))` }}
          onMouseLeave={() => setHover(null)}>
          <span />
          {hours.map((hr) => <span key={hr} className="text-center text-muted-foreground">{hr}</span>)}
          {DAYS.map((d, i) => (
            <div key={d} className="contents">
              <span className="pr-1 text-right text-muted-foreground">{d}</span>
              {hours.map((hr) => {
                const c = byKey.get(`${i + 1}-${hr}`)
                return (
                  <span key={hr} onMouseEnter={() => setHover(`${i + 1}-${hr}`)} role="img"
                    aria-label={`${d} ${hr}:00: ${c?.dials ? `${c.reached} of ${c.dials} answered` : 'no calls'}`}
                    className={cn('h-5 rounded-[3px]', c?.step == null ? 'bg-muted' : RAMP[c.step], hover === `${i + 1}-${hr}` && 'ring-2 ring-foreground/60')} />
                )
              })}
            </div>
          ))}
        </div>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span className="flex items-center gap-1">Fewer answer {RAMP.map((r, i) => <span key={i} className={cn('inline-block h-3 w-4 rounded-[2px]', r)} />)} More answer <span className="ml-2 inline-block h-3 w-4 rounded-[2px] bg-muted" /> no calls</span>
        <span>{h ? `${DAYS[h.dow - 1]} ${h.hour}:00 — ${h.dials ? `${h.reached} of ${h.dials} first calls answered` : 'no calls yet'}` : `Local time · ${data.reached} of ${data.dials} first calls answered`}</span>
      </div>
      <details className="text-xs">
        <summary className="cursor-pointer text-muted-foreground">Show as a table</summary>
        <table className="mt-1 text-left">
          <thead><tr className="text-muted-foreground"><th className="pr-3 font-normal">Day</th><th className="pr-3 font-normal">Hour</th><th className="pr-3 font-normal">Answered</th><th className="font-normal">Calls</th></tr></thead>
          <tbody>{cells.filter((c) => c.dials).map((c) => <tr key={`${c.dow}-${c.hour}`}><td className="pr-3">{DAYS[c.dow - 1]}</td><td className="pr-3">{c.hour}:00</td><td className="pr-3 tabular-nums">{c.reached}</td><td className="tabular-nums">{c.dials}</td></tr>)}</tbody>
        </table>
      </details>
    </figure>
  )
}
