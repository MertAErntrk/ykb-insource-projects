import { useId } from 'react'
import type { TransferMode } from '../../api/types.ts'
import { SearchableSelect, TextField } from '../ui/index.ts'
import type { SelectOption } from '../ui/index.ts'
import './wizard.css'
import './summary.css'

export const IDENT_RE = /^[A-Z][A-Z0-9_$#]*$/

/** Yeni tablo adı için varsayılan: <KAYNAK>_ANON_<yyyymmdd>; sığmazsa kaynak kısmı kırpılır, tarih korunur. */
export function defaultTableName(source: string, maxLen: number, now = new Date()): string {
  const d = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}${String(now.getDate()).padStart(2, '0')}`
  const suffix = `_ANON_${d}`
  return `${source.toUpperCase().slice(0, Math.max(1, maxLen - suffix.length))}${suffix}`.slice(0, maxLen)
}

export function identError(name: string, maxLen: number): string | null {
  if (!name) return 'Tablo adı boş olamaz.'
  if (!IDENT_RE.test(name)) return 'Ad harfle başlamalı; yalnız A-Z, 0-9, _, $ ve # içerebilir.'
  if (name.length > maxLen) return `Ad en çok ${maxLen.toLocaleString('tr-TR')} karakter olabilir.`
  return null
}

export interface TargetFormProps {
  mode: TransferMode
  onModeChange: (m: TransferMode) => void
  userSchema: string
  maxLen: number
  name: string
  onNameChange: (n: string) => void
  nameError: string | null
  nameChecking: boolean
  schemas: SelectOption[]
  schema: string | null
  onSchemaChange: (s: string) => void
  schemaError: string | null
  tables: SelectOption[]
  tableName: string | null
  onTableChange: (t: string) => void
  onTableQuery: (q: string) => void
  tableError: string | null
}

export function TargetForm(p: TargetFormProps) {
  const uid = useId()
  return (
    <div className="conn-stack">
      <fieldset className="mode-group">
        <legend>Hedef türü</legend>
        <label className="mode-card">
          <input type="radio" name={`${uid}-mode`} className="check" checked={p.mode === 'create'} onChange={() => p.onModeChange('create')} />
          <span><strong>Yeni tablo oluştur</strong><small>Hedefte yeni bir tablo açılır ve satırlar oraya yazılır.</small></span>
        </label>
        <label className="mode-card">
          <input type="radio" name={`${uid}-mode`} className="check" checked={p.mode === 'append'} onChange={() => p.onModeChange('append')} />
          <span><strong>Mevcut tabloya ekle</strong><small>Satırlar var olan bir hedef tablonun sonuna eklenir.</small></span>
        </label>
      </fieldset>

      {p.mode === 'create' ? (
        <div className="panel">
          <TextField label="Şema" value={p.userSchema} readOnly hint="Yeni tablo yalnız kendi şemanıza oluşturulur" />
          <TextField
            label="Tablo adı"
            value={p.name}
            onChange={(e) => p.onNameChange(e.target.value.toUpperCase())}
            error={p.nameError}
            hint={p.nameChecking ? 'Ad denetleniyor...' : `En çok ${p.maxLen.toLocaleString('tr-TR')} karakter; büyük harfe çevrilir.`}
            autoComplete="off"
            spellCheck={false}
          />
          <p className="target-note">
            Kolon tipleri ve NOT NULL kopyalanır; DEFAULT, IDENTITY, sanal kolon, PK/index ve trigger kopyalanmaz. Özetle (HMAC) kolonlar VARCHAR2(64) olur. Tablo 7 gün sonra temizlik listesine girer.
          </p>
        </div>
      ) : (
        <div className="panel">
          <SearchableSelect label="Hedef şema" options={p.schemas} value={p.schema} onChange={p.onSchemaChange} placeholder="Şema seçin" error={p.schemaError} />
          <SearchableSelect
            label="Hedef tablo"
            options={p.tables}
            value={p.tableName}
            onChange={p.onTableChange}
            placeholder={p.schema ? 'Tablo seçin' : 'Önce şema seçin'}
            disabled={!p.schema}
            clientFilter={false}
            onQueryChange={p.onTableQuery}
            error={p.tableError}
          />
          <p className="target-note">Ekleme tek işlemde yapılır; hata olursa hiçbir satır eklenmez.</p>
        </div>
      )}
    </div>
  )
}
