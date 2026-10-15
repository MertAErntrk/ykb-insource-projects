import { createContext, useContext, useEffect, useMemo, useReducer } from 'react'
import type { ReactNode } from 'react'
import type { ConnectionInfo, CountResponse, Filter, Role, Rule, TableRef, TransferMode } from '../api/types.ts'

export const WIZARD_STORAGE_KEY = 'tt_wizard'

/** Bağlantı özeti: şifre ASLA burada tutulmaz. */
export interface WizardConnection {
  alias: string
  user: string
  info: ConnectionInfo
}
export interface TargetSetting {
  schema: string
  table: string
  mode: TransferMode
}
export interface WizardState {
  source: WizardConnection | null
  target: WizardConnection | null
  table: TableRef | null
  filters: Filter[]
  /** "Tablo 5 milyonun altında; tamamını taşıyacağım" kutusu. */
  fullTable: boolean
  /** Sayımda 5 milyonu aşan tablo ("ŞEMA.TABLO"); kutu bu tablo için kapalı kalır. */
  fullTableBlocked: string | null
  count: CountResponse | null
  rules: Rule[]
  /** Kolonlar adımında "Anonimleştirme yapacağım" kutusu. */
  anonymize: boolean
  targetSetting: TargetSetting | null
  jobId: string | null
}

export const initialWizardState: WizardState = {
  source: null, target: null, table: null, filters: [], fullTable: false, fullTableBlocked: null, count: null, rules: [], anonymize: false, targetSetting: null, jobId: null,
}

type Action =
  | { type: 'connection'; role: Role; value: WizardConnection | null }
  | { type: 'table'; table: TableRef | null }
  | { type: 'filters'; filters: Filter[] }
  | { type: 'fullTable'; value: boolean }
  | { type: 'fullTableBlocked'; value: string | null }
  | { type: 'count'; count: CountResponse | null }
  | { type: 'rules'; rules: Rule[] }
  | { type: 'anonymize'; value: boolean }
  | { type: 'targetSetting'; value: TargetSetting | null }
  | { type: 'jobId'; jobId: string | null }
  | { type: 'reset' }

function reducer(s: WizardState, a: Action): WizardState {
  switch (a.type) {
    case 'connection': return { ...s, [a.role]: a.value }
    // Tablo değişince ona bağlı her şey sıfırlanır.
    case 'table': return { ...s, table: a.table, filters: [], fullTable: false, count: null, rules: [], anonymize: false, targetSetting: null, jobId: null }
    case 'filters': return { ...s, filters: a.filters, count: null }
    case 'fullTable': return { ...s, fullTable: a.value, filters: [], count: null }
    case 'fullTableBlocked': return { ...s, fullTableBlocked: a.value }
    case 'count': return { ...s, count: a.count }
    case 'rules': return { ...s, rules: a.rules }
    case 'anonymize': return { ...s, anonymize: a.value }
    case 'targetSetting': return { ...s, targetSetting: a.value }
    case 'jobId': return { ...s, jobId: a.jobId }
    case 'reset': return initialWizardState
  }
}

/** sessionStorage metni: password/şifre anahtarları savunma amaçlı her durumda atılır. */
export function serializeWizard(state: WizardState): string {
  return JSON.stringify(state, (k, v) => (/pass|pwd|sifre/i.test(k) ? undefined : v))
}

function load(): WizardState {
  try {
    const raw = sessionStorage.getItem(WIZARD_STORAGE_KEY)
    return raw ? { ...initialWizardState, ...(JSON.parse(raw) as Partial<WizardState>) } : initialWizardState
  } catch {
    return initialWizardState
  }
}

interface WizardApi extends WizardState {
  setConnection: (role: Role, value: WizardConnection | null) => void
  setTable: (table: TableRef | null) => void
  setFilters: (filters: Filter[]) => void
  setFullTable: (value: boolean) => void
  setFullTableBlocked: (value: string | null) => void
  setCount: (count: CountResponse | null) => void
  setRules: (rules: Rule[]) => void
  setAnonymize: (value: boolean) => void
  setTargetSetting: (value: TargetSetting | null) => void
  setJobId: (jobId: string | null) => void
  reset: () => void
}

const WizardContext = createContext<WizardApi | null>(null)

export function WizardProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, undefined, load)
  useEffect(() => {
    try {
      sessionStorage.setItem(WIZARD_STORAGE_KEY, serializeWizard(state))
    } catch {
      // depolama kapalı: durum yalnız bellekte kalır
    }
  }, [state])
  const api = useMemo<WizardApi>(
    () => ({
      ...state,
      setConnection: (role, value) => dispatch({ type: 'connection', role, value }),
      setTable: (table) => dispatch({ type: 'table', table }),
      setFilters: (filters) => dispatch({ type: 'filters', filters }),
      setFullTable: (value) => dispatch({ type: 'fullTable', value }),
      setFullTableBlocked: (value) => dispatch({ type: 'fullTableBlocked', value }),
      setCount: (count) => dispatch({ type: 'count', count }),
      setRules: (rules) => dispatch({ type: 'rules', rules }),
      setAnonymize: (value) => dispatch({ type: 'anonymize', value }),
      setTargetSetting: (value) => dispatch({ type: 'targetSetting', value }),
      setJobId: (jobId) => dispatch({ type: 'jobId', jobId }),
      reset: () => dispatch({ type: 'reset' }),
    }),
    [state],
  )
  return <WizardContext.Provider value={api}>{children}</WizardContext.Provider>
}

export function useWizard(): WizardApi {
  const ctx = useContext(WizardContext)
  if (!ctx) throw new Error('useWizard, WizardProvider içinde kullanılmalı')
  return ctx
}
