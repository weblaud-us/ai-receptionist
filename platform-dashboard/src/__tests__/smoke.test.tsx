import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ErrorBoundary } from '@receptionist/shared/components/error-boundary'
import { failureText, pct } from '@/lib/sales'

afterEach(cleanup)

function Boom({ when }: { when: boolean }) {
  if (when) throw new Error('render failed')
  return <p>platform page</p>
}

describe('platform ErrorBoundary', () => {
  it('replaces a crashed page with a recoverable message', () => {
    const quiet = vi.spyOn(console, 'error').mockImplementation(() => {})
    let fail = true
    const Page = () => <Boom when={fail} />
    render(<ErrorBoundary scope="page"><Page /></ErrorBoundary>)
    expect(screen.getByRole('alert')).toBeTruthy()
    fail = false
    fireEvent.click(screen.getByText('Try again'))
    expect(screen.getByText('platform page')).toBeTruthy()
    quiet.mockRestore()
  })
})

describe('sales formatting', () => {
  it('formats rates and failure codes for people', () => {
    expect(pct(0.1234)).toBe('12.3%')
    expect(pct(null)).toBe('—')
    expect(failureText('some_new_code')).toBe('some new code')
  })
})

describe('demo leads formatting', () => {
  it('shows talk time and where a lead came from', async () => {
    const { sourceText, talkTime } = await import('@/lib/demo-leads')
    expect(talkTime(0)).toBe('—')
    expect(talkTime(45)).toBe('45s')
    expect(talkTime(185)).toBe('3m 05s')
    expect(sourceText({ utm: { source: 'google', medium: 'cpc', campaign: 'au-dental' }, referrer: '', page: '/' })).toBe('google / cpc · au-dental')
    expect(sourceText({ utm: {}, referrer: 'https://www.linkedin.com/feed/', page: '/' })).toBe('linkedin.com')
    expect(sourceText({ utm: {}, referrer: '', page: '/' })).toBe('Direct')
  })
})
