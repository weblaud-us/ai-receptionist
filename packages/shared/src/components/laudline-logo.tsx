import { cn } from '../lib/utils'
import { LAUDLINE_PATH, LAUDLINE_VIEWBOX } from './laudline-path'

/** The LaudLine wordmark in the current text colour. Size it by height, e.g. className="h-6". */
export function LaudlineLogo({ className, title = 'LaudLine' }: { className?: string; title?: string }) {
  return (
    <svg viewBox={LAUDLINE_VIEWBOX} fill="currentColor" role="img" aria-label={title} className={cn('w-auto', className)}>
      <path fillRule="evenodd" d={LAUDLINE_PATH} />
    </svg>
  )
}
