import type { ReactNode } from 'react'
import { Button, Stepper } from '../ui/index.ts'

export const WIZARD_STEPS = [
  { id: 'connect', label: 'Bağlantı' },
  { id: 'table', label: 'Tablo' },
  { id: 'columns', label: 'Kolonlar ve kurallar' },
  { id: 'target', label: 'Hedef' },
  { id: 'summary', label: 'Özet ve onay' },
] as const
export type WizardStepId = (typeof WIZARD_STEPS)[number]['id']

export interface WizardLayoutProps {
  step: WizardStepId
  title: string
  description?: string
  children: ReactNode
  onBack?: () => void
  onNext?: () => void
  nextLabel?: string
  nextDisabled?: boolean
  nextLoading?: boolean
}

/** Adım göstergesi + başlık + içerik + alt İleri/Geri çubuğu. Geri yalnız onBack verilirse görünür. */
export function WizardLayout({ step, title, description, children, onBack, onNext, nextLabel = 'İleri', nextDisabled, nextLoading }: WizardLayoutProps) {
  return (
    <div className="wizard">
      <Stepper steps={[...WIZARD_STEPS]} current={step} />
      <div className="wizard-head">
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      <section aria-label={title}>{children}</section>
      <div className="wizard-footer">
        {onBack ? <Button onClick={onBack}>Geri</Button> : <span />}
        {onNext && <Button variant="primary" onClick={onNext} disabled={nextDisabled} loading={nextLoading}>{nextLabel}</Button>}
      </div>
    </div>
  )
}
