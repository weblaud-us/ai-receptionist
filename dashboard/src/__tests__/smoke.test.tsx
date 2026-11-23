import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ErrorBoundary } from '@receptionist/shared/components/error-boundary'
import { regionFor } from '@/lib/region'
import { ROLE_LABEL } from '@/lib/tenant-format'

afterEach(cleanup)

function Boom({ when }: { when: boolean }) {
  if (when) throw new Error('render failed')
  return <p>page content</p>
}

describe('ErrorBoundary', () => {
  it('shows a way out instead of a white screen, and recovers', () => {
    const quiet = vi.spyOn(console, 'error').mockImplementation(() => {})
    let fail = true
    const Page = () => <Boom when={fail} />
    render(<ErrorBoundary scope="page"><Page /></ErrorBoundary>)
    expect(screen.getByRole('alert').textContent).toContain("This page couldn't load")
    fail = false
    fireEvent.click(screen.getByText('Try again'))
    expect(screen.getByText('page content')).toBeTruthy()
    quiet.mockRestore()
  })
})

describe('settings copy', () => {
  it('knows the dentist role and tells AU/UK businesses calls are transcribed, not recorded', () => {
    expect(ROLE_LABEL.dentist).toBe('Dentist')
    expect(regionFor('AU').callNote).toContain('transcribed')
    expect(regionFor('GB').callNote).toContain('transcribed')
    expect(regionFor('AU').emergency).toBe('000')
    expect(JSON.stringify(regionFor('US'))).not.toContain('announces recording')
  })
})
