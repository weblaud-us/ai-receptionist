/** Help → Guides: the practice help articles, with this person's own first. Open to every role. */
import { HelpArticlePage, HelpIndexPage, type HelpSection } from '@receptionist/shared/components/help-pages'
import { publicHelp, type HelpAudience } from '@receptionist/shared/lib/help'
import { useTenant } from '@/lib/tenant'

const SECTIONS: HelpSection[] = [{ registry: publicHelp }]

export default function Help() {
  const { ctx } = useTenant()
  const audience: HelpAudience[] = ctx ? [ctx.role] : []
  return <HelpIndexPage sections={SECTIONS} audience={audience} />
}

export function HelpArticle() {
  return <HelpArticlePage sections={SECTIONS} />
}
