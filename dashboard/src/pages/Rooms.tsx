import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { Loader2, Pencil, Plus, Power, PowerOff, Wrench } from 'lucide-react'
import { ConfirmDialog, EmptyState, ErrorBanner, Field, PageHeader, TableSkeleton } from '@/components/app/blocks'
import { SimpleSelect } from '@/components/app/simple-select'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { money, titleCase } from '@/lib/format'
import { useRoomMutations, useRooms } from '@/lib/hooks'
import { BED_TYPES, ROOM_TYPES, type Room, type RoomAmenities, type RoomPricing } from '@/lib/types'

const ALL = 'all'

const AMENITY_LABELS: Record<keyof RoomAmenities, string> = {
  wifi: 'Wi-Fi',
  ac: 'Air conditioning',
  tv: 'TV',
  minibar: 'Minibar',
  balcony: 'Balcony',
  sea_view: 'Sea view',
  jacuzzi: 'Jacuzzi',
  kitchen: 'Kitchen',
  workspace: 'Workspace',
  breakfast_included: 'Breakfast included',
}

const BLANK: Omit<Room, 'id'> = {
  room_number: '',
  room_type: 'standard',
  bed_type: 'double',
  floor: 1,
  capacity: 2,
  size_sqft: null,
  description: '',
  amenities: {
    wifi: true, ac: true, tv: true, minibar: false, balcony: false,
    sea_view: false, jacuzzi: false, kitchen: false, workspace: false, breakfast_included: false,
  },
  pricing: {
    base_price_per_night: 4500,
    weekend_surcharge_pct: 0,
    peak_season_surcharge_pct: 0,
    discount_weekly_pct: 0,
    discount_monthly_pct: 0,
    tax_pct: 15,
  },
  images: [],
  is_active: true,
  is_maintenance: false,
}

export default function Rooms() {
  const q = useRooms({ include_inactive: true })
  const { update, deactivate } = useRoomMutations()
  const [type, setType] = useState(ALL)
  const [state, setState] = useState(ALL)
  const [editing, setEditing] = useState<Room | 'new' | null>(null)
  const [toDeactivate, setToDeactivate] = useState<Room | null>(null)

  const rooms = useMemo(() => q.data?.rooms ?? [], [q.data])
  const filtered = rooms.filter(
    (r) =>
      (type === ALL || r.room_type === type) &&
      (state === ALL ||
        (state === 'active' && r.is_active && !r.is_maintenance) ||
        (state === 'maintenance' && r.is_active && r.is_maintenance) ||
        (state === 'inactive' && !r.is_active)),
  )

  const byType = useMemo(
    () =>
      ROOM_TYPES.map((t) => {
        const list = rooms.filter((r) => r.room_type === t && r.is_active)
        const prices = list.map((r) => r.pricing.base_price_per_night)
        return {
          type: t,
          count: list.length,
          maintenance: list.filter((r) => r.is_maintenance).length,
          min: prices.length ? Math.min(...prices) : 0,
          max: prices.length ? Math.max(...prices) : 0,
        }
      }),
    [rooms],
  )

  return (
    <>
      <PageHeader
        title="Rooms"
        description="Inventory and pricing. The AI receptionist quotes from exactly these records."
        actions={
          <Button onClick={() => setEditing('new')}>
            <Plus /> Add room
          </Button>
        }
      />

      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-5">
        {byType.map((t) => (
          <Card key={t.type} size="sm">
            <CardContent>
              <div className="text-sm text-muted-foreground">{titleCase(t.type)}</div>
              <div className="text-xl font-semibold">{t.count}</div>
              <div className="text-xs text-muted-foreground">
                {t.count
                  ? t.min === t.max
                    ? money(t.min)
                    : `${money(t.min)} – ${money(t.max)}`
                  : 'No rooms'}
                {t.maintenance ? ` · ${t.maintenance} in maintenance` : ''}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card className="gap-0 py-0">
        <div className="flex flex-wrap items-center gap-3 border-b p-4">
          <SimpleSelect
            value={type}
            onChange={setType}
            options={[{ value: ALL, label: 'All types' }, ...ROOM_TYPES.map((t) => ({ value: t, label: titleCase(t) }))]}
          />
          <SimpleSelect
            value={state}
            onChange={setState}
            options={[
              { value: ALL, label: 'All states' },
              { value: 'active', label: 'Available to sell' },
              { value: 'maintenance', label: 'Maintenance' },
              { value: 'inactive', label: 'Deactivated' },
            ]}
          />
          <span className="ml-auto text-sm text-muted-foreground">
            {filtered.length} of {rooms.length} rooms
          </span>
        </div>

        {q.error && (
          <div className="p-4">
            <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />
          </div>
        )}
        {q.isLoading ? (
          <TableSkeleton />
        ) : filtered.length === 0 ? (
          <EmptyState
            title={rooms.length === 0 ? 'No rooms yet' : 'No rooms match'}
            description={rooms.length === 0 ? 'Add a room, or seed 100 sample rooms with `python -m utils.seed_rooms`.' : undefined}
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Room</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Bed</TableHead>
                <TableHead>Floor</TableHead>
                <TableHead>Sleeps</TableHead>
                <TableHead className="text-right">Per night</TableHead>
                <TableHead>Perks</TableHead>
                <TableHead>State</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.map((r) => (
                <TableRow key={r.id} className={r.is_active ? '' : 'opacity-60'}>
                  <TableCell className="font-medium">{r.room_number}</TableCell>
                  <TableCell>{titleCase(r.room_type)}</TableCell>
                  <TableCell>{titleCase(r.bed_type)}</TableCell>
                  <TableCell>{r.floor}</TableCell>
                  <TableCell>{r.capacity}</TableCell>
                  <TableCell className="text-right">{money(r.pricing.base_price_per_night)}</TableCell>
                  <TableCell className="max-w-48 truncate text-xs text-muted-foreground">
                    {(Object.keys(AMENITY_LABELS) as (keyof RoomAmenities)[])
                      .filter((k) => r.amenities[k])
                      .map((k) => AMENITY_LABELS[k])
                      .join(', ')}
                  </TableCell>
                  <TableCell>
                    {!r.is_active ? (
                      <Badge variant="outline">Deactivated</Badge>
                    ) : r.is_maintenance ? (
                      <Badge className="border-0 bg-amber-100 text-amber-800 dark:bg-amber-500/20 dark:text-amber-300">Maintenance</Badge>
                    ) : (
                      <Badge className="border-0 bg-emerald-100 text-emerald-800 dark:bg-emerald-500/20 dark:text-emerald-300">Available</Badge>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-1">
                      <Button size="icon-sm" variant="ghost" aria-label="Edit" onClick={() => setEditing(r)}>
                        <Pencil />
                      </Button>
                      {r.is_active && (
                        <Button
                          size="icon-sm"
                          variant="ghost"
                          aria-label={r.is_maintenance ? 'End maintenance' : 'Start maintenance'}
                          title={r.is_maintenance ? 'Back in service' : 'Mark as maintenance'}
                          disabled={update.isPending}
                          onClick={() =>
                            update.mutate(
                              { id: r.id, is_maintenance: !r.is_maintenance },
                              { onSuccess: () => toast.success(`Room ${r.room_number} ${r.is_maintenance ? 'back in service' : 'in maintenance'}`) },
                            )
                          }
                        >
                          <Wrench />
                        </Button>
                      )}
                      {r.is_active ? (
                        <Button size="icon-sm" variant="ghost" aria-label="Deactivate" title="Deactivate" onClick={() => setToDeactivate(r)}>
                          <PowerOff />
                        </Button>
                      ) : (
                        <Button
                          size="icon-sm"
                          variant="ghost"
                          aria-label="Reactivate"
                          title="Reactivate"
                          onClick={() =>
                            update.mutate({ id: r.id, is_active: true }, { onSuccess: () => toast.success(`Room ${r.room_number} reactivated`) })
                          }
                        >
                          <Power />
                        </Button>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>

      <RoomDialog room={editing} onClose={() => setEditing(null)} existingNumbers={rooms.map((r) => r.room_number)} />

      <ConfirmDialog
        open={!!toDeactivate}
        onOpenChange={(o) => !o && setToDeactivate(null)}
        title={`Deactivate room ${toDeactivate?.room_number}?`}
        description="It stops being offered to guests and the AI. Rooms with upcoming bookings can't be deactivated. You can reactivate it any time."
        confirmLabel="Deactivate"
        destructive
        loading={deactivate.isPending}
        onConfirm={() =>
          toDeactivate &&
          deactivate.mutate(toDeactivate.id, {
            onSuccess: () => {
              toast.success(`Room ${toDeactivate.room_number} deactivated`)
              setToDeactivate(null)
            },
            onError: () => setToDeactivate(null),
          })
        }
      />
    </>
  )
}

function RoomDialog({
  room,
  onClose,
  existingNumbers,
}: {
  room: Room | 'new' | null
  onClose: () => void
  existingNumbers: string[]
}) {
  return (
    <Dialog open={!!room} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-2xl">
        {room && <RoomForm key={room === 'new' ? 'new' : room.id} room={room} onClose={onClose} existingNumbers={existingNumbers} />}
      </DialogContent>
    </Dialog>
  )
}

const num = (v: string) => (v === '' ? 0 : Number(v))

function RoomForm({
  room,
  onClose,
  existingNumbers,
}: {
  room: Room | 'new'
  onClose: () => void
  existingNumbers: string[]
}) {
  const isNew = room === 'new'
  const initial = isNew ? BLANK : room
  const { create, update } = useRoomMutations()
  const [f, setF] = useState<Omit<Room, 'id'>>(() => ({ ...initial, amenities: { ...initial.amenities }, pricing: { ...initial.pricing } }))
  const set = <K extends keyof typeof f>(k: K, v: (typeof f)[K]) => setF((p) => ({ ...p, [k]: v }))
  const setPrice = (k: keyof RoomPricing, v: string) => setF((p) => ({ ...p, pricing: { ...p.pricing, [k]: num(v) } }))

  const numberTaken =
    f.room_number.trim() !== '' &&
    existingNumbers.includes(f.room_number.trim()) &&
    (isNew || f.room_number.trim() !== room.room_number)
  const valid = f.room_number.trim() && !numberTaken && f.capacity >= 1 && f.pricing.base_price_per_night > 0
  const pending = create.isPending || update.isPending

  function save() {
    const body = { ...f, room_number: f.room_number.trim(), size_sqft: f.size_sqft || null }
    if (isNew) {
      create.mutate(body, {
        onSuccess: () => {
          toast.success(`Room ${body.room_number} added`)
          onClose()
        },
      })
    } else {
      update.mutate(
        {
          id: room.id,
          room_number: body.room_number,
          room_type: body.room_type,
          bed_type: body.bed_type,
          floor: body.floor,
          capacity: body.capacity,
          size_sqft: body.size_sqft,
          description: body.description,
          amenities: body.amenities,
          pricing: body.pricing,
          is_maintenance: body.is_maintenance,
          is_active: body.is_active,
        },
        {
          onSuccess: () => {
            toast.success(`Room ${body.room_number} saved`)
            onClose()
          },
        },
      )
    }
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>{isNew ? 'Add room' : `Edit room ${room.room_number}`}</DialogTitle>
        <DialogDescription>Changes apply immediately to availability and pricing quoted by the AI.</DialogDescription>
      </DialogHeader>

      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Room number" hint={numberTaken ? 'Already in use' : undefined}>
          <Input value={f.room_number} onChange={(e) => set('room_number', e.target.value)} aria-invalid={numberTaken} />
        </Field>
        <Field label="Type">
          <SimpleSelect
            className="w-full"
            value={f.room_type}
            onChange={(v) => set('room_type', v as Room['room_type'])}
            options={ROOM_TYPES.map((t) => ({ value: t, label: titleCase(t) }))}
          />
        </Field>
        <Field label="Bed">
          <SimpleSelect
            className="w-full"
            value={f.bed_type}
            onChange={(v) => set('bed_type', v as Room['bed_type'])}
            options={BED_TYPES.map((t) => ({ value: t, label: titleCase(t) }))}
          />
        </Field>
        <Field label="Floor">
          <Input type="number" value={f.floor} onChange={(e) => set('floor', num(e.target.value))} />
        </Field>
        <Field label="Max guests">
          <Input type="number" min={1} value={f.capacity} onChange={(e) => set('capacity', num(e.target.value))} />
        </Field>
        <Field label="Size (sq ft)">
          <Input type="number" value={f.size_sqft ?? ''} onChange={(e) => set('size_sqft', e.target.value === '' ? null : num(e.target.value))} />
        </Field>
      </div>
      <Field label="Description">
        <Textarea rows={2} value={f.description} onChange={(e) => set('description', e.target.value)} />
      </Field>

      <div>
        <h3 className="mb-2 text-sm font-semibold">Pricing</h3>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Base price / night">
            <Input type="number" min={0} value={f.pricing.base_price_per_night} onChange={(e) => setPrice('base_price_per_night', e.target.value)} />
          </Field>
          <Field label="Tax %">
            <Input type="number" min={0} value={f.pricing.tax_pct} onChange={(e) => setPrice('tax_pct', e.target.value)} />
          </Field>
          <Field label="Weekly discount % (7+ nights)">
            <Input type="number" min={0} value={f.pricing.discount_weekly_pct} onChange={(e) => setPrice('discount_weekly_pct', e.target.value)} />
          </Field>
          <Field label="Monthly discount % (30+ nights)">
            <Input type="number" min={0} value={f.pricing.discount_monthly_pct} onChange={(e) => setPrice('discount_monthly_pct', e.target.value)} />
          </Field>
          <Field label="Weekend surcharge %" hint="Stored, not yet applied to quotes">
            <Input type="number" min={0} value={f.pricing.weekend_surcharge_pct} onChange={(e) => setPrice('weekend_surcharge_pct', e.target.value)} />
          </Field>
          <Field label="Peak season surcharge %" hint="Stored, not yet applied to quotes">
            <Input type="number" min={0} value={f.pricing.peak_season_surcharge_pct} onChange={(e) => setPrice('peak_season_surcharge_pct', e.target.value)} />
          </Field>
        </div>
      </div>

      <div>
        <h3 className="mb-2 text-sm font-semibold">Amenities</h3>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {(Object.keys(AMENITY_LABELS) as (keyof RoomAmenities)[]).map((k) => (
            <label key={k} className="flex items-center justify-between gap-2 rounded-md border px-3 py-2 text-sm">
              {AMENITY_LABELS[k]}
              <Switch
                checked={f.amenities[k]}
                onCheckedChange={(v) => setF((p) => ({ ...p, amenities: { ...p.amenities, [k]: v } }))}
              />
            </label>
          ))}
        </div>
      </div>

      <div className="flex flex-wrap gap-4">
        <label className="flex items-center gap-2 text-sm">
          <Switch checked={f.is_maintenance} onCheckedChange={(v) => set('is_maintenance', v)} /> Under maintenance
        </label>
        {!isNew && (
          <label className="flex items-center gap-2 text-sm">
            <Switch checked={f.is_active} onCheckedChange={(v) => set('is_active', v)} /> Active
          </label>
        )}
      </div>

      <div className="sticky -bottom-4 z-10 -mx-4 -mb-4 flex justify-end gap-2 border-t bg-popover p-4">
        <Button variant="outline" onClick={onClose}>
          Cancel
        </Button>
        <Button disabled={!valid || pending} onClick={save}>
          {pending && <Loader2 className="animate-spin" />}
          {isNew ? 'Add room' : 'Save changes'}
        </Button>
      </div>
    </>
  )
}
