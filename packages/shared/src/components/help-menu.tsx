/** The header's Help menu: replay the tour, bring back the checklist, the guides, and outside links. */
import { BookOpen, ExternalLink, LifeBuoy, ListChecks, Mail, Map as MapIcon, Radio } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { usePublicSite } from '../lib/site'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from './ui/dropdown-menu'

export function HelpMenu({ onTour, onShowChecklist }: {
  /** Start this person's tour again (hidden when there's none). */
  onTour?: () => void
  /** Show the getting-started list again (owners and admins). */
  onShowChecklist?: () => void
}) {
  const navigate = useNavigate()
  const site = usePublicSite().data
  const outside = !!site && !!(site.help_center_url || site.support_email || site.status_page_url)
  return (
    <DropdownMenu>
      <DropdownMenuTrigger data-tour="help-menu" aria-label="Help"
        className="inline-flex h-8 items-center gap-1.5 rounded-lg px-2 text-sm text-muted-foreground outline-none transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring data-[popup-open]:bg-muted">
        <LifeBuoy className="size-4" /> <span className="hidden sm:inline">Help</span>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        <DropdownMenuGroup>
          {onTour && <DropdownMenuItem onClick={onTour}><MapIcon /> Take the tour</DropdownMenuItem>}
          {onShowChecklist && <DropdownMenuItem onClick={onShowChecklist}><ListChecks /> Getting started checklist</DropdownMenuItem>}
          <DropdownMenuItem onClick={() => navigate('/help')}><BookOpen /> Guides</DropdownMenuItem>
        </DropdownMenuGroup>
        {outside && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              {site.help_center_url && (
                <DropdownMenuItem render={<a href={site.help_center_url} target="_blank" rel="noopener noreferrer" />}>
                  <ExternalLink /> Help centre
                </DropdownMenuItem>
              )}
              {site.support_email && (
                <DropdownMenuItem render={<a href={`mailto:${site.support_email}`} />}><Mail /> Contact support</DropdownMenuItem>
              )}
              {site.status_page_url && (
                <DropdownMenuItem render={<a href={site.status_page_url} target="_blank" rel="noopener noreferrer" />}>
                  <Radio /> Service status
                </DropdownMenuItem>
              )}
            </DropdownMenuGroup>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
