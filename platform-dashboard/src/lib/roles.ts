/**
 * Who may do what in the sales dialer (SALES_SUITE_PLAN.md Phase 2). The API enforces the same
 * ladder (core/auth/deps.require_sales); here it only hides what someone can't use.
 *
 *   viewer   look at everything
 *   caller   + work leads: power dialer, notes, tasks, wrap-up, do-not-call
 *   manager  + offers, AI, campaigns, imports, exports
 *   admin    + the team and compliance registries (platform admins)
 */
import { useAuth, type AuthUser } from '@receptionist/shared/auth/auth'

export type SalesLevel = 'viewer' | 'caller' | 'manager' | 'admin'

const RANK: Record<SalesLevel, number> = { viewer: 1, caller: 2, manager: 3, admin: 4 }

export function salesRank(user: Pick<AuthUser, 'is_platform_admin' | 'sales_role'> | null | undefined): number {
  if (!user) return 0
  if (user.is_platform_admin) return RANK.admin
  return user.sales_role ? RANK[user.sales_role] ?? 0 : 0
}

export const can = (user: Pick<AuthUser, 'is_platform_admin' | 'sales_role'> | null | undefined, level: SalesLevel) =>
  salesRank(user) >= RANK[level]

/** Sales team members who aren't platform admins see only the dialer (and their account). */
export const salesOnly = (user: Pick<AuthUser, 'is_platform_admin' | 'sales_role'> | null | undefined) =>
  !!user && !user.is_platform_admin && salesRank(user) > 0

export function useCan() {
  const { user } = useAuth()
  return (level: SalesLevel) => can(user, level)
}
