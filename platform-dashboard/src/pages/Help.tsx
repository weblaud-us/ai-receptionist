/** Help → Guides: the team's own guides first, then what practice owners see in their dashboard. */
import { useAuth } from '@receptionist/shared/auth/auth'
import { HelpArticlePage, HelpIndexPage, type HelpSection } from '@receptionist/shared/components/help-pages'
import { publicHelp, type HelpAudience } from '@receptionist/shared/lib/help'
import { internalHelp } from '@/lib/help-internal'

const SECTIONS: HelpSection[] = [
  { title: 'For the team', registry: internalHelp },
  { title: 'What practice owners see', registry: publicHelp },
]

export default function Help() {
  const { user } = useAuth()
  const audience: HelpAudience[] = user?.is_platform_admin ? ['platform'] : ['sales']
  return <HelpIndexPage sections={SECTIONS} audience={audience} description="Guides for the LaudLine team, and the ones practices read." />
}

export function HelpArticle() {
  return <HelpArticlePage sections={SECTIONS} />
}
