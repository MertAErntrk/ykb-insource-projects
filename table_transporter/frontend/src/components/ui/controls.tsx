import { useEffect, useId, useMemo, useRef, useState } from 'react'
import type { ButtonHTMLAttributes, InputHTMLAttributes, KeyboardEvent, ReactNode } from 'react'
import { Spinner } from './display.tsx'

// --- Button ---
export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  size?: 'md' | 'sm'
  loading?: boolean
}

export function Button({ variant = 'secondary', size = 'md', loading = false, disabled, className, children, type = 'button', ...rest }: ButtonProps) {
  const cls = ['btn', `btn-${variant}`, size === 'sm' ? 'btn-sm' : '', className ?? ''].filter(Boolean).join(' ')
  return (
    <button {...rest} type={type} className={cls} disabled={disabled || loading} aria-busy={loading || undefined}>
      {loading && <Spinner />}
      {children}
    </button>
  )
}

// --- Field + metin alanları ---
export interface FieldProps {
  id: string
  label: string
  hint?: string
  error?: string | null
  children: ReactNode
}

/** Etiket + kontrol + ipucu + hata mesajı. Kontrol aria-describedby için describedBy() kullanır. */
export function Field({ id, label, hint, error, children }: FieldProps) {
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>{label}</label>
      {children}
      {hint && <p className="field-hint" id={`${id}-hint`}>{hint}</p>}
      {error && <p className="field-error" id={`${id}-error`}>{error}</p>}
    </div>
  )
}

export function describedBy(id: string, hint?: string, error?: string | null) {
  const ids = [hint ? `${id}-hint` : '', error ? `${id}-error` : ''].filter(Boolean).join(' ')
  return ids || undefined
}

interface TextFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'id'> {
  label: string
  hint?: string
  error?: string | null
}

export function TextField({ label, hint, error, className, ...rest }: TextFieldProps) {
  const id = useId()
  return (
    <Field id={id} label={label} hint={hint} error={error}>
      <input {...rest} id={id} className={`input ${className ?? ''}`} aria-invalid={error ? true : undefined} aria-describedby={describedBy(id, hint, error)} />
    </Field>
  )
}

export function PasswordField({ label, hint, error, className, ...rest }: TextFieldProps) {
  const id = useId()
  const [visible, setVisible] = useState(false)
  return (
    <Field id={id} label={label} hint={hint} error={error}>
      <div className="input-wrap">
        <input {...rest} id={id} type={visible ? 'text' : 'password'} className={`input ${className ?? ''}`} aria-invalid={error ? true : undefined} aria-describedby={describedBy(id, hint, error)} />
        <Button variant="ghost" size="sm" className="input-adorn" aria-pressed={visible} aria-label={visible ? 'Şifreyi gizle' : 'Şifreyi göster'} onClick={() => setVisible((v) => !v)}>
          {visible ? 'Gizle' : 'Göster'}
        </Button>
      </div>
    </Field>
  )
}

// --- SearchableSelect: ARIA 1.2 combobox (list autocomplete) ---
export interface SelectOption {
  value: string
  label: string
  description?: string
  disabled?: boolean
}

export interface SearchableSelectProps {
  label: string
  options: SelectOption[]
  value: string | null
  onChange: (value: string) => void
  placeholder?: string
  hint?: string
  error?: string | null
  emptyText?: string
  disabled?: boolean
  /** false: seçenekleri sunucu süzer (onQueryChange ile), istemci süzmez. */
  clientFilter?: boolean
  onQueryChange?: (query: string) => void
}

export function SearchableSelect({ label, options, value, onChange, placeholder, hint, error, emptyText = 'Eşleşen sonuç yok.', disabled, clientFilter = true, onQueryChange }: SearchableSelectProps) {
  const id = useId()
  const listId = `${id}-list`
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const listRef = useRef<HTMLUListElement>(null)

  const selected = options.find((o) => o.value === value)
  const shown = useMemo(() => {
    const q = query.trim().toLocaleLowerCase('tr')
    return clientFilter && q ? options.filter((o) => o.label.toLocaleLowerCase('tr').includes(q)) : options
  }, [options, query, clientFilter])

  useEffect(() => {
    if (open) listRef.current?.children[active]?.scrollIntoView?.({ block: 'nearest' })
  }, [open, active])

  const close = () => {
    setOpen(false)
    setQuery('')
  }
  const choose = (o: SelectOption | undefined) => {
    if (!o || o.disabled) return
    onChange(o.value)
    close()
  }
  const openList = () => {
    if (open) return
    setOpen(true)
    setActive(Math.max(0, shown.findIndex((o) => o.value === value)))
  }

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    const last = Math.max(shown.length - 1, 0)
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault()
        if (!open) openList()
        else setActive((a) => Math.min(a + 1, last))
        break
      case 'ArrowUp':
        e.preventDefault()
        if (!open) openList()
        else setActive((a) => Math.max(a - 1, 0))
        break
      case 'Home':
        if (open) { e.preventDefault(); setActive(0) }
        break
      case 'End':
        if (open) { e.preventDefault(); setActive(last) }
        break
      case 'Enter':
        if (open) { e.preventDefault(); choose(shown[active]) }
        break
      case 'Escape':
        if (open) { e.preventDefault(); close() }
        break
    }
  }

  return (
    <Field id={id} label={label} hint={hint} error={error}>
      <div className="combo">
        <input
          id={id}
          className="input"
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={open && shown[active] ? `${id}-opt-${active}` : undefined}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy(id, hint, error)}
          autoComplete="off"
          disabled={disabled}
          placeholder={placeholder}
          value={open ? query : (selected?.label ?? '')}
          onClick={openList}
          onBlur={close}
          onKeyDown={onKeyDown}
          onChange={(e) => {
            setQuery(e.target.value)
            setActive(0)
            setOpen(true)
            onQueryChange?.(e.target.value)
          }}
        />
        {open && (
          <ul id={listId} ref={listRef} role="listbox" aria-label={label} className="combo-list">
            {shown.length === 0 && <li className="combo-empty" role="presentation">{emptyText}</li>}
            {shown.map((o, i) => (
              <li
                key={o.value}
                id={`${id}-opt-${i}`}
                role="option"
                className="combo-option"
                aria-selected={o.value === value}
                aria-disabled={o.disabled || undefined}
                data-active={i === active}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => choose(o)}
                onMouseMove={() => setActive(i)}
              >
                {o.label}
                {o.description && <small>{o.description}</small>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Field>
  )
}
