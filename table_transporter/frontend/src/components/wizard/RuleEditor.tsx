import type { ColumnInfo } from '../../api/types.ts'
import { Badge, Button, DataTable } from '../ui/index.ts'
import type { Column } from '../ui/index.ts'
import { formatType } from './ruleSuggest.ts'

interface RuleEditorProps {
  columns: ColumnInfo[]
  /** Taşınacak (işaretli) kolon adları. */
  moved: Set<string>
  onToggle: (name: string) => void
  onToggleAll: () => void
}

/** "Taşınacak kolonlar" paneli: onay kutusu, ad, veri tipi, anahtar/zorunlu işaretleri. Desteklenmeyen tip pasif + neden. */
export function RuleEditor({ columns, moved, onToggle, onToggleAll }: RuleEditorProps) {
  const supported = columns.filter((c) => c.supported)
  const allMoved = supported.length > 0 && supported.every((c) => moved.has(c.name))

  const cols: Column<ColumnInfo>[] = [
    {
      key: 'move',
      header: 'Taşı',
      render: (c) => (
        <input
          type="checkbox"
          className="check"
          aria-label={`Taşı: ${c.name}`}
          checked={c.supported && moved.has(c.name)}
          disabled={!c.supported}
          onChange={() => onToggle(c.name)}
        />
      ),
    },
    {
      key: 'name',
      header: 'Kolon',
      render: (c) => (
        <div className="col-name">
          <code>{c.name}</code>
          {!c.supported && <span className="rule-desc">{c.unsupported_reason ?? 'Bu kolon tipi desteklenmiyor; taşınamaz.'}</span>}
        </div>
      ),
    },
    { key: 'type', header: 'Veri tipi', render: (c) => <span className="col-type">{formatType(c)}</span> },
    {
      key: 'flags',
      header: 'İşaretler',
      render: (c) => (
        <span className="col-badges">
          {c.is_pk_or_unique && <Badge tone="info">PK/UNIQUE</Badge>}
          {!c.nullable && <Badge>NOT NULL</Badge>}
          {!c.supported && <Badge tone="warn">Desteklenmiyor</Badge>}
        </span>
      ),
    },
  ]

  return (
    <section className="col-panel" aria-labelledby="move-title">
      <div className="col-panel-head">
        <h2 id="move-title">Taşınacak kolonlar</h2>
        <div className="toolbar-group">
          <span role="status" aria-live="polite">
            {moved.size.toLocaleString('tr-TR')} / {columns.length.toLocaleString('tr-TR')} kolon taşınacak
          </span>
          <Button onClick={onToggleAll} disabled={supported.length === 0}>{allMoved ? 'Tümünü kaldır' : 'Tümünü seç'}</Button>
        </div>
      </div>
      <DataTable columns={cols} rows={columns} rowKey={(c) => c.name} caption="Taşınacak kolonlar" maxHeight="50vh" />
    </section>
  )
}
