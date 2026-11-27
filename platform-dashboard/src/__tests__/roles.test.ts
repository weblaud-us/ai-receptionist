import { describe, expect, it } from 'vitest'
import { can, salesOnly } from '@/lib/roles'

// The dashboard hides what the API (core/auth/deps.require_sales) would refuse: same ladder.
describe('sales roles', () => {
  it('climbs viewer → caller → manager → admin', () => {
    const viewer = { is_platform_admin: false, sales_role: 'viewer' as const }
    const caller = { is_platform_admin: false, sales_role: 'caller' as const }
    const manager = { is_platform_admin: false, sales_role: 'manager' as const }
    expect([can(viewer, 'viewer'), can(viewer, 'caller')]).toEqual([true, false])
    expect([can(caller, 'caller'), can(caller, 'manager')]).toEqual([true, false])
    expect([can(manager, 'manager'), can(manager, 'admin')]).toEqual([true, false])
    expect(can({ is_platform_admin: true, sales_role: null }, 'admin')).toBe(true)
  })

  it('gives nobody without a role anything', () => {
    expect(can({ is_platform_admin: false, sales_role: null }, 'viewer')).toBe(false)
    expect(can(null, 'viewer')).toBe(false)
    expect(salesOnly({ is_platform_admin: false, sales_role: 'caller' })).toBe(true)
    expect(salesOnly({ is_platform_admin: true, sales_role: null })).toBe(false)
  })
})
