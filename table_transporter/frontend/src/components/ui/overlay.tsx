import { createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent, ReactNode } from 'react'
import { createPortal } from 'react-dom'

// --- Dialog: odak içeride döner, Esc kapatır, kapanınca odak geri verilir ---
const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

export interface DialogProps {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
  actions?: ReactNode
}

export function Dialog({ open, title, onClose, children, actions }: DialogProps) {
  const titleId = useId()
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const prev = document.activeElement as HTMLElement | null
    const first = ref.current?.querySelector<HTMLElement>(FOCUSABLE)
    ;(first ?? ref.current)?.focus()
    return () => prev?.focus?.()
  }, [open])

  if (!open) return null

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') {
      e.stopPropagation()
      onClose()
      return
    }
    if (e.key !== 'Tab' || !ref.current) return
    const items = Array.from(ref.current.querySelectorAll<HTMLElement>(FOCUSABLE))
    if (items.length === 0) {
      e.preventDefault()
      return
    }
    const first = items[0]
    const last = items[items.length - 1]
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault()
      last.focus()
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault()
      first.focus()
    }
  }

  return createPortal(
    <div className="dialog-backdrop">
      <div ref={ref} className="dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1} onKeyDown={onKeyDown}>
        <h2 id={titleId}>{title}</h2>
        <div>{children}</div>
        {actions && <div className="dialog-actions">{actions}</div>}
      </div>
    </div>,
    document.body,
  )
}

// --- Toast: aria-live bölgesi ---
type ToastTone = 'info' | 'success' | 'error'
interface ToastItem {
  id: number
  tone: ToastTone
  message: string
}
interface ToastApi {
  push: (message: string, tone?: ToastTone) => void
}

const ToastContext = createContext<ToastApi | null>(null)
let nextToastId = 1

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const dismiss = useCallback((id: number) => setItems((l) => l.filter((t) => t.id !== id)), [])
  const push = useCallback(
    (message: string, tone: ToastTone = 'info') => {
      const id = nextToastId++
      setItems((l) => [...l, { id, tone, message }])
      setTimeout(() => dismiss(id), tone === 'error' ? 10000 : 6000)
    },
    [dismiss],
  )
  const api = useMemo(() => ({ push }), [push])
  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toast-region" aria-live="polite" aria-relevant="additions">
        {items.map((t) => (
          <div key={t.id} className="toast" data-tone={t.tone}>
            <span>{t.message}</span>
            <button type="button" onClick={() => dismiss(t.id)}>Kapat</button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast, ToastProvider içinde kullanılmalı')
  return ctx
}
