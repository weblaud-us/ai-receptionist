import { useState } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { Download, ExternalLink, FileCheck2, Loader2, Search, ShieldCheck, Trash2 } from 'lucide-react'
import { ConfirmDialog, ErrorBanner, Field, PageHeader } from '@receptionist/shared/components/blocks'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { Checkbox } from '@receptionist/shared/components/ui/checkbox'
import { Input } from '@receptionist/shared/components/ui/input'
import { Skeleton } from '@receptionist/shared/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@receptionist/shared/components/ui/table'
import { dateOnly } from '@receptionist/shared/lib/format'
import {
  exportSubject,
  lookupSubject,
  useEraseSubject,
  useLegal,
  useLegalMutations,
  usePrivacyRequests,
  type LegalStatus,
  type SubjectSummary,
} from '@/lib/compliance'

const KIND_LABEL: Record<string, string> = {
  contacts: 'Contact record',
  calls: 'Calls',
  appointments: 'Appointments',
  messages: 'Messages',
  leads: 'Job requests',
  conversations: 'WhatsApp chats',
  bookings: 'Bookings',
  guests: 'Guest records',
}

const errorText = (e: unknown) => (e instanceof Error ? e.message : 'Something went wrong')

function DpaCard({ legal }: { legal: LegalStatus }) {
  const { acceptDpa } = useLegalMutations()
  const [name, setName] = useState('')
  const [title, setTitle] = useState('')
  const [read, setRead] = useState(false)
  const dpa = legal.dpa
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FileCheck2 className="size-4" /> Data Processing Agreement
          {dpa.current ? <Badge>Accepted</Badge> : <Badge variant="destructive">Action needed</Badge>}
        </CardTitle>
        <CardDescription>
          You decide what happens to your callers’ data; {legal.entity} processes it for you. The DPA is the contract that
          says so — GDPR, UK GDPR and US state privacy laws expect one.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {dpa.current ? (
          <p>Version {dpa.accepted_version} accepted on {dateOnly(dpa.accepted_at)}.{' '}
            {dpa.url && <a className="underline" href={dpa.url} target="_blank" rel="noopener">Read it again</a>}
          </p>
        ) : (
          <>
            {dpa.accepted_version && <p>You accepted version {dpa.accepted_version}. Version {dpa.version} has changes.</p>}
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Your full name"><Input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} /></Field>
              <Field label="Your title"><Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Owner" maxLength={120} /></Field>
            </div>
            <label className="flex items-start gap-2">
              <Checkbox checked={read} onCheckedChange={(v) => setRead(v === true)} className="mt-0.5" />
              <span>
                I have read the{' '}
                {dpa.url ? <a className="underline" href={dpa.url} target="_blank" rel="noopener">Data Processing Agreement</a> : 'Data Processing Agreement'}{' '}
                (version {dpa.version}) and accept it on behalf of the business.
              </span>
            </label>
            <Button
              disabled={!read || name.trim().length < 2 || acceptDpa.isPending}
              onClick={() =>
                acceptDpa.mutate(
                  { version: dpa.version, signer_name: name.trim(), signer_title: title.trim() },
                  { onSuccess: () => toast.success('DPA accepted'), onError: (e) => toast.error(errorText(e)) },
                )
              }
            >
              {acceptDpa.isPending && <Loader2 className="animate-spin" />}
              Accept the DPA
            </Button>
          </>
        )}
      </CardContent>
    </Card>
  )
}

function DocumentsCard({ legal }: { legal: LegalStatus }) {
  const { acceptDocuments } = useLegalMutations()
  return (
    <Card>
      <CardHeader>
        <CardTitle>Documents</CardTitle>
        <CardDescription>The current versions of everything you agreed to.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {legal.outstanding.length > 0 && (
          <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3">
            <p>We’ve updated: {legal.outstanding.map((k) => legal.documents.find((d) => d.key === k)?.title ?? k).join(', ')}.</p>
            <Button
              size="sm"
              className="mt-2"
              disabled={acceptDocuments.isPending}
              onClick={() => acceptDocuments.mutate(legal.outstanding, { onSuccess: () => toast.success('Thanks — recorded') })}
            >
              I’ve read and accept the updates
            </Button>
          </div>
        )}
        <ul className="divide-y">
          {legal.documents.map((d) => (
            <li key={d.key} className="flex items-center justify-between py-2">
              <span>{d.title} <span className="text-muted-foreground">· {d.version}</span></span>
              {d.url ? (
                <a href={d.url} target="_blank" rel="noopener" className="inline-flex items-center gap-1 underline">
                  Open <ExternalLink className="size-3" />
                </a>
              ) : (
                <span className="text-xs text-muted-foreground">Not published yet</span>
              )}
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}

function SubjectRequests({ legal }: { legal: LegalStatus }) {
  const [phone, setPhone] = useState('')
  const [received, setReceived] = useState('')
  const [summary, setSummary] = useState<SubjectSummary | null>(null)
  const [looking, setLooking] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [eraseOpen, setEraseOpen] = useState(false)
  const [confirm, setConfirm] = useState('')
  const [includeFuture, setIncludeFuture] = useState(false)
  const [suppress, setSuppress] = useState(true)
  const erase = useEraseSubject()
  const receivedIso = received ? new Date(received).toISOString() : undefined

  const lookup = async () => {
    setLooking(true)
    try {
      setSummary(await lookupSubject(phone.trim()))
    } catch (e) {
      setSummary(null)
      toast.error(errorText(e))
    } finally {
      setLooking(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><ShieldCheck className="size-4" /> A caller asks for their data</CardTitle>
        <CardDescription>
          When someone asks what you hold about them, or asks you to delete it, find them by phone number here. You have{' '}
          {legal.dsr_deadline_days} days to answer under GDPR — note the date the request reached you.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-[1fr_12rem_auto] sm:items-end">
          <Field label="Their phone number">
            <Input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+1 512 555 0123" inputMode="tel"
              onKeyDown={(e) => e.key === 'Enter' && void lookup()} />
          </Field>
          <Field label="Request received on">
            <Input type="date" value={received} onChange={(e) => setReceived(e.target.value)} />
          </Field>
          <Button onClick={() => void lookup()} disabled={phone.trim().length < 7 || looking}>
            {looking ? <Loader2 className="animate-spin" /> : <Search />} Find
          </Button>
        </div>

        {summary && (
          <div className="rounded-lg border p-4">
            {summary.total === 0 ? (
              <p className="text-sm">We hold nothing for {summary.phone}. You can tell them so.</p>
            ) : (
              <>
                <p className="text-sm font-medium">For {summary.phone} we hold:</p>
                <ul className="mt-2 grid gap-1 text-sm sm:grid-cols-2">
                  {Object.entries(summary.counts).filter(([, n]) => n > 0).map(([k, n]) => (
                    <li key={k}>{KIND_LABEL[k] ?? k}: <span className="tabular-nums">{n}</span></li>
                  ))}
                  {summary.recordings > 0 && <li>Call recordings: <span className="tabular-nums">{summary.recordings}</span></li>}
                </ul>
                {summary.future_appointments > 0 && (
                  <p className="mt-2 text-sm text-amber-600 dark:text-amber-400">
                    {summary.future_appointments} upcoming appointment(s). Cancel or move them with the customer before deleting.
                  </p>
                )}
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button
                    variant="outline"
                    disabled={exporting}
                    onClick={async () => {
                      setExporting(true)
                      try {
                        await exportSubject(phone.trim(), receivedIso)
                        toast.success('Downloaded. Send the file to the person securely.')
                      } catch (e) {
                        toast.error(errorText(e))
                      } finally {
                        setExporting(false)
                      }
                    }}
                  >
                    {exporting ? <Loader2 className="animate-spin" /> : <Download />} Download their data
                  </Button>
                  <Button variant="destructive" onClick={() => { setConfirm(''); setEraseOpen(true) }}>
                    <Trash2 /> Delete their data
                  </Button>
                </div>
              </>
            )}
          </div>
        )}

        <ConfirmDialog
          open={eraseOpen}
          onOpenChange={setEraseOpen}
          destructive
          title={`Delete everything about ${summary?.phone ?? ''}?`}
          description="Contact, messages and chats are deleted. Calls keep their date and length for your invoices, without transcript, recording or number. This can’t be undone."
          confirmLabel="Delete permanently"
          loading={erase.isPending}
          onConfirm={() =>
            erase.mutate(
              { phone: phone.trim(), confirm: confirm.trim(), include_future: includeFuture, suppress, received_at: receivedIso },
              {
                onSuccess: () => {
                  toast.success('Deleted. The request is in the log below.')
                  setEraseOpen(false)
                  setSummary(null)
                  setPhone('')
                },
                onError: (e) => toast.error(errorText(e)),
              },
            )
          }
        >
          <div className="space-y-3 text-sm">
            <Field label="Type the number again to confirm"><Input value={confirm} onChange={(e) => setConfirm(e.target.value)} inputMode="tel" /></Field>
            {(summary?.future_appointments ?? 0) > 0 && (
              <label className="flex items-start gap-2">
                <Checkbox checked={includeFuture} onCheckedChange={(v) => setIncludeFuture(v === true)} className="mt-0.5" />
                Also remove their details from upcoming appointments
              </label>
            )}
            <label className="flex items-start gap-2">
              <Checkbox checked={suppress} onCheckedChange={(v) => setSuppress(v === true)} className="mt-0.5" />
              Keep the number on a do-not-contact list so we never text or call it again (recommended)
            </label>
          </div>
        </ConfirmDialog>
      </CardContent>
    </Card>
  )
}

function RequestLog() {
  const q = usePrivacyRequests()
  const rows = q.data?.requests ?? []
  return (
    <Card>
      <CardHeader>
        <CardTitle>Request log</CardTitle>
        <CardDescription>Keep this as your record that each request was answered in time. Numbers are masked.</CardDescription>
      </CardHeader>
      <CardContent>
        {q.error && <ErrorBanner error={q.error} />}
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">No requests yet.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow><TableHead>Type</TableHead><TableHead>Number</TableHead><TableHead>Received</TableHead><TableHead>Due</TableHead><TableHead>Done</TableHead></TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={r.id}>
                  <TableCell>{r.kind === 'access' ? 'Copy of data' : 'Deletion'}</TableCell>
                  <TableCell className="tabular-nums">{r.phone_masked}</TableCell>
                  <TableCell>{dateOnly(r.received_at)}</TableCell>
                  <TableCell>{dateOnly(r.due_at)}</TableCell>
                  <TableCell>{dateOnly(r.completed_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}

/** Phase 6: the business's legal side — DPA, documents, retention, caller data requests, sub-processors. */
export default function Privacy() {
  const q = useLegal()
  const legal = q.data
  return (
    <>
      <PageHeader title="Legal & privacy" description="Your agreements with us, how long call data is kept, and how to answer a caller’s data request." />
      {q.error && <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />}
      {!legal && !q.error && <Skeleton className="h-96 w-full" />}
      {legal && (
        <div className="space-y-4">
          <div className="grid gap-4 lg:grid-cols-2">
            <DpaCard legal={legal} />
            <DocumentsCard legal={legal} />
          </div>
          <Card>
            <CardHeader>
              <CardTitle>How long we keep call data</CardTitle>
              <CardDescription>
                Change these under <Link className="underline" to="/assistant">Assistant</Link>. Shorter is safer: keep only what you use.
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-3 text-sm sm:grid-cols-3">
              <div><div className="text-muted-foreground">Transcripts</div><div className="text-lg font-medium">{legal.retention.transcript_days} days</div></div>
              <div>
                <div className="text-muted-foreground">Recordings</div>
                <div className="text-lg font-medium">{legal.retention.recording_enabled ? `${legal.retention.recording_days} days` : 'Not recorded'}</div>
              </div>
              <div><div className="text-muted-foreground">After you cancel</div><div className="text-lg font-medium">{legal.retention.after_cancellation_days} days, then deleted</div></div>
            </CardContent>
          </Card>
          <SubjectRequests legal={legal} />
          <RequestLog />
          <Card>
            <CardHeader>
              <CardTitle>Sub-processors</CardTitle>
              <CardDescription>
                Companies that process call data for us. We give 30 days’ notice before adding one.
                {legal.privacy_contact && <> Questions: <a className="underline" href={`mailto:${legal.privacy_contact}`}>{legal.privacy_contact}</a>.</>}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader><TableRow><TableHead>Company</TableHead><TableHead>What for</TableHead><TableHead className="hidden md:table-cell">Where</TableHead><TableHead className="hidden lg:table-cell">When</TableHead></TableRow></TableHeader>
                <TableBody>
                  {legal.subprocessors.map((s) => (
                    <TableRow key={s.name}>
                      <TableCell className="font-medium">{s.name}</TableCell>
                      <TableCell>{s.purpose}</TableCell>
                      <TableCell className="hidden md:table-cell">{s.location}</TableCell>
                      <TableCell className="hidden text-muted-foreground lg:table-cell">{s.when}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </div>
      )}
    </>
  )
}
