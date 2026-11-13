import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { cn } from '@/lib/utils'

export interface Option {
  value: string
  label: string
}

interface Props {
  value: string
  onChange: (value: string) => void
  options: Option[]
  placeholder?: string
  className?: string
  disabled?: boolean
  size?: 'sm' | 'default'
}

/** Thin wrapper over the Base UI select: a flat options list and a plain string value. */
export function SimpleSelect({ value, onChange, options, placeholder, className, disabled, size }: Props) {
  return (
    <Select
      value={value}
      onValueChange={(v) => onChange(v ?? '')}
      items={options}
      disabled={disabled}
    >
      <SelectTrigger className={cn('min-w-32', className)} size={size}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {options.map((o) => (
          <SelectItem key={o.value} value={o.value}>
            {o.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
