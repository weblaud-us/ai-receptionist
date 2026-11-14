import { Plus, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import type { HoursEntry } from '@/lib/tenant-types'

const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

/**
 * Weekly opening hours. Each day can be closed, or have one or more intervals (split shifts).
 * A close time earlier than the open time means the interval runs past midnight.
 */
export function HoursEditor({
  value,
  onChange,
  disabled,
}: {
  value: HoursEntry[]
  onChange: (next: HoursEntry[]) => void
  disabled?: boolean
}) {
  const byDay = DAYS.map((_, dow) => value.filter((h) => h.dow === dow))

  const setDay = (dow: number, rows: HoursEntry[]) =>
    onChange([...value.filter((h) => h.dow !== dow), ...rows].sort((a, b) => a.dow - b.dow || a.open.localeCompare(b.open)))

  return (
    <div className="divide-y rounded-lg border">
      {DAYS.map((day, dow) => {
        const rows = byDay[dow]
        const open = rows.length > 0
        return (
          <div key={day} className="flex flex-wrap items-center gap-3 px-3 py-2">
            <div className="flex w-36 items-center gap-2">
              <Switch
                checked={open}
                disabled={disabled}
                onCheckedChange={(on) => setDay(dow, on ? [{ dow, open: '09:00', close: '17:00' }] : [])}
                aria-label={`${day} open`}
              />
              <span className="text-sm font-medium">{day}</span>
            </div>
            {!open && <span className="text-sm text-muted-foreground">Closed</span>}
            <div className="flex flex-1 flex-wrap items-center gap-2">
              {rows.map((h, i) => (
                <div key={i} className="flex items-center gap-1.5">
                  <Input
                    type="time"
                    className="w-28"
                    value={h.open}
                    disabled={disabled}
                    onChange={(e) => setDay(dow, rows.map((r, j) => (j === i ? { ...r, open: e.target.value } : r)))}
                  />
                  <span className="text-muted-foreground">–</span>
                  <Input
                    type="time"
                    className="w-28"
                    value={h.close === '24:00' ? '23:59' : h.close}
                    disabled={disabled}
                    onChange={(e) => setDay(dow, rows.map((r, j) => (j === i ? { ...r, close: e.target.value } : r)))}
                  />
                  {rows.length > 1 && (
                    <Button
                      size="icon-xs"
                      variant="ghost"
                      disabled={disabled}
                      aria-label="Remove interval"
                      onClick={() => setDay(dow, rows.filter((_, j) => j !== i))}
                    >
                      <X />
                    </Button>
                  )}
                  {h.close <= h.open && h.close !== '00:00' && <span className="text-xs text-muted-foreground">(next day)</span>}
                </div>
              ))}
              {open && (
                <Button
                  size="xs"
                  variant="ghost"
                  disabled={disabled}
                  onClick={() => setDay(dow, [...rows, { dow, open: '13:00', close: '17:00' }])}
                >
                  <Plus /> Split
                </Button>
              )}
            </div>
            {open && dow > 0 && (
              <Button
                size="xs"
                variant="link"
                disabled={disabled}
                onClick={() => setDay(dow, byDay[dow - 1].map((r) => ({ ...r, dow })))}
              >
                Same as {DAYS[dow - 1].slice(0, 3)}
              </Button>
            )}
          </div>
        )
      })}
    </div>
  )
}
