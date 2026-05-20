import { useMemo, useRef, useState } from 'react'
import { ChevronDown, Copy, Upload, X } from 'lucide-react'

import {
  bulkImportEvents,
  type BulkEventInput,
  type BulkEventsImportResponse,
} from '../../api/admin'

type Props = {
  conferenceId: string
  conferenceName: string
  onClose: () => void
  onApplied: () => void
}

type ConflictPolicy = 'upsert' | 'skip'

const EXAMPLE_JSON = `[
  {
    "title": "Opening Keynote",
    "description": "Crypto market outlook for Q4.",
    "starts_at": "2026-10-01T09:00:00+04:00",
    "ends_at": "2026-10-01T10:30:00+04:00",
    "venue": "Marina Bay Sands, Hall A",
    "tags": ["keynote", "macro"],
    "url": "https://example.com/keynote",
    "capacity": 500
  },
  {
    "id": "myagent:eth-summit-panel-1",
    "title": "DeFi Panel",
    "starts_at": "2026-10-01T11:00:00+04:00",
    "ends_at": "2026-10-01T12:00:00+04:00",
    "tags": ["defi"]
  }
]`

function parseEvents(raw: string): BulkEventInput[] {
  const parsed = JSON.parse(raw)
  // Accept either {events: [...]} or a bare array, since agents may emit either.
  if (Array.isArray(parsed)) return parsed
  if (parsed && Array.isArray(parsed.events)) return parsed.events
  throw new Error('JSON must be an array of events or {"events": [...]}')
}

export function AdminEventsBulkImport(props: Props) {
  const [raw, setRaw] = useState<string>('')
  const [onConflict, setOnConflict] = useState<ConflictPolicy>('upsert')
  const [dryRun, setDryRun] = useState<boolean>(true)
  const [busy, setBusy] = useState<boolean>(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<BulkEventsImportResponse | null>(null)
  const [schemaOpen, setSchemaOpen] = useState<boolean>(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const parsedCount = useMemo(() => {
    if (!raw.trim()) return null
    try {
      return parseEvents(raw).length
    } catch {
      return null
    }
  }, [raw])

  const onFile = (file: File) => {
    const reader = new FileReader()
    reader.onload = () => {
      const text = typeof reader.result === 'string' ? reader.result : ''
      setRaw(text)
      setResult(null)
      setError(null)
    }
    reader.onerror = () => setError('Could not read file')
    reader.readAsText(file)
  }

  const submit = async () => {
    setError(null)
    setResult(null)
    let events: BulkEventInput[]
    try {
      events = parseEvents(raw)
    } catch (e) {
      setError(`Invalid JSON: ${e instanceof Error ? e.message : String(e)}`)
      return
    }
    if (events.length === 0) {
      setError('No events in payload')
      return
    }
    setBusy(true)
    try {
      const res = await bulkImportEvents(
        { conference_id: props.conferenceId, on_conflict: onConflict, events },
        { dryRun },
      )
      setResult(res)
      if (!dryRun && (res.inserted > 0 || res.updated > 0)) {
        props.onApplied()
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Import failed')
    } finally {
      setBusy(false)
    }
  }

  const copyExample = () => {
    navigator.clipboard.writeText(EXAMPLE_JSON).catch(() => {
      // best-effort; old browsers / non-secure context just ignore
    })
  }

  return (
    <div className="admin-drawer">
      <div className="admin-drawer__backdrop" onClick={props.onClose} />
      <div className="admin-drawer__panel admin-edit-surface">
        <header className="admin-drawer__head">
          <h2>Import events — {props.conferenceName}</h2>
          <button
            type="button"
            className="admin-drawer__close"
            aria-label="Close"
            onClick={props.onClose}
          >
            <X size={18} />
          </button>
        </header>

        <p style={{ fontSize: 13, color: 'var(--fg-muted)', margin: '0 0 12px' }}>
          Paste or upload JSON to bulk-create events for{' '}
          <strong>{props.conferenceName}</strong>. Existing events with matching
          IDs will be updated when conflict policy is "upsert". Locked rows are
          always skipped.
        </p>

        <button
          type="button"
          className="admin-btn"
          onClick={() => setSchemaOpen((v) => !v)}
          style={{ alignSelf: 'flex-start', marginBottom: 8 }}
        >
          <ChevronDown
            size={14}
            style={{
              transform: schemaOpen ? 'rotate(0deg)' : 'rotate(-90deg)',
              transition: 'transform 120ms',
            }}
          />
          {schemaOpen ? 'Hide' : 'Show'} JSON schema &amp; example
        </button>

        {schemaOpen && (
          <div
            style={{
              background: 'var(--bg-surface)',
              border: '1px solid var(--border-subtle)',
              borderRadius: 8,
              padding: 12,
              marginBottom: 12,
              fontSize: 12,
            }}
          >
            <div style={{ marginBottom: 8 }}>
              <strong>Per-event fields:</strong>
              <ul style={{ margin: '6px 0 0 18px', lineHeight: 1.6 }}>
                <li>
                  <code>id</code> <em>(optional)</em> — supply your own stable ID
                  (e.g. <code>myscraper:abc123</code>) or omit and the server
                  derives <code>import:{props.conferenceId}:&lt;hash&gt;</code> from
                  title + starts_at so re-imports update cleanly.
                </li>
                <li>
                  <code>title</code> <em>(required)</em>
                </li>
                <li>
                  <code>starts_at</code>, <code>ends_at</code>{' '}
                  <em>(required)</em> — ISO 8601 with timezone offset
                </li>
                <li>
                  <code>description</code>, <code>venue</code>, <code>url</code>{' '}
                  — strings or null
                </li>
                <li>
                  <code>tags</code> — array of strings
                </li>
                <li>
                  <code>capacity</code>, <code>attendees</code> — integers or null
                </li>
              </ul>
            </div>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                marginBottom: 4,
              }}
            >
              <strong>Example:</strong>
              <button
                type="button"
                className="admin-btn admin-btn--icon"
                onClick={copyExample}
                title="Copy example"
              >
                <Copy size={12} />
              </button>
            </div>
            <pre
              style={{
                background: 'var(--bg-canvas, #fafafa)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 6,
                padding: 10,
                fontSize: 11,
                lineHeight: 1.45,
                overflow: 'auto',
                margin: 0,
                maxHeight: 220,
              }}
            >
              {EXAMPLE_JSON}
            </pre>
          </div>
        )}

        <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
          <button
            type="button"
            className="admin-btn"
            onClick={() => fileInputRef.current?.click()}
          >
            <Upload size={14} /> Upload .json
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept="application/json,.json"
            style={{ display: 'none' }}
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) onFile(file)
              // Reset so re-selecting the same file fires onChange again.
              e.target.value = ''
            }}
          />
          {parsedCount !== null && (
            <span
              style={{
                alignSelf: 'center',
                fontSize: 12,
                color: 'var(--fg-muted)',
              }}
            >
              {parsedCount} event{parsedCount === 1 ? '' : 's'} parsed
            </span>
          )}
        </div>

        <label className="admin__field" style={{ marginBottom: 8 }}>
          <span>JSON payload</span>
          <textarea
            rows={10}
            value={raw}
            onChange={(e) => {
              setRaw(e.target.value)
              setResult(null)
              setError(null)
            }}
            placeholder='[ { "title": "...", "starts_at": "...", "ends_at": "..." } ]'
            spellCheck={false}
            style={{ fontFamily: 'var(--font-mono, monospace)', fontSize: 12 }}
          />
        </label>

        <div className="admin-drawer__row">
          <label className="admin__field">
            <span>On conflict</span>
            <select
              value={onConflict}
              onChange={(e) => setOnConflict(e.target.value as ConflictPolicy)}
            >
              <option value="upsert">Upsert — overwrite existing unlocked rows</option>
              <option value="skip">Skip — leave existing rows alone</option>
            </select>
          </label>

          <label
            className="admin__field"
            style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}
          >
            <input
              type="checkbox"
              checked={dryRun}
              onChange={(e) => setDryRun(e.target.checked)}
            />
            <span style={{ marginTop: 0 }}>
              Dry run (preview without writing)
            </span>
          </label>
        </div>

        {error && (
          <div className="admin__error" style={{ marginTop: 8 }}>
            {error}
          </div>
        )}

        {result && (
          <div
            style={{
              marginTop: 12,
              padding: 12,
              borderRadius: 8,
              background: result.dry_run
                ? 'rgba(75, 96, 184, 0.06)'
                : 'rgba(34, 139, 34, 0.06)',
              border: `1px solid ${
                result.dry_run ? 'var(--sq-blue-deep)' : 'rgba(34, 139, 34, 0.4)'
              }`,
            }}
          >
            <strong>
              {result.dry_run
                ? 'Dry-run preview (nothing written)'
                : 'Import complete'}
            </strong>
            <ul style={{ margin: '6px 0 0 18px', fontSize: 13, lineHeight: 1.6 }}>
              <li>
                <strong>{result.inserted}</strong> inserted
              </li>
              <li>
                <strong>{result.updated}</strong> updated
              </li>
              <li>
                <strong>{result.skipped_locked}</strong> skipped (locked)
              </li>
              <li>
                <strong>{result.skipped_conflict}</strong> skipped (conflict policy)
              </li>
              <li>
                <strong>{result.errors.length}</strong> errors
              </li>
            </ul>
            {result.errors.length > 0 && (
              <details style={{ marginTop: 8 }}>
                <summary style={{ cursor: 'pointer', fontSize: 12 }}>
                  Show {result.errors.length} error
                  {result.errors.length === 1 ? '' : 's'}
                </summary>
                <ul
                  style={{
                    margin: '6px 0 0 18px',
                    fontSize: 12,
                    fontFamily: 'var(--font-mono, monospace)',
                    color: 'var(--sq-red, #E62C5A)',
                  }}
                >
                  {result.errors.map((err) => (
                    <li key={`${err.index}-${err.id ?? ''}`}>
                      [#{err.index}] {err.id ? `${err.id}: ` : ''}
                      {err.message}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}

        <div className="admin-drawer__actions">
          <button type="button" className="admin-btn" onClick={props.onClose}>
            Close
          </button>
          <button
            type="button"
            className="admin-btn admin-btn--primary"
            onClick={submit}
            disabled={busy || raw.trim() === ''}
          >
            {busy
              ? 'Importing…'
              : dryRun
              ? 'Run dry-run'
              : 'Import for real'}
          </button>
        </div>
      </div>
    </div>
  )
}
