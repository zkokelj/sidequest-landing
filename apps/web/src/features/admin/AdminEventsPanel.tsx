import { useMemo, useState } from 'react'
import { Lock, Unlock, Upload, Wrench, Plus, Pencil, Trash2 } from 'lucide-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  createAdminEvent,
  deleteAdminEvent,
  listAdminEvents,
  listAllConferences,
  setAdminEventLock,
  updateAdminEvent,
  type AdminEvent,
  type AdminEventCreate,
  type AdminEventUpdate,
} from '../../api/admin'
import { CONFERENCES } from '../../data/conferences'
import { AdminEventForm, type EventFormMode } from './AdminEventForm'
import { AdminEventsBulkImport } from './AdminEventsBulkImport'

type LockFilter = 'all' | 'locked' | 'unlocked'
type ManualFilter = 'all' | 'manual' | 'scraped'

function fmtDateTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString([], {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function AdminEventsPanel() {
  const [conferenceId, setConferenceId] = useState<string>('token2049')
  const [lockFilter, setLockFilter] = useState<LockFilter>('all')
  const [manualFilter, setManualFilter] = useState<ManualFilter>('all')
  const [query, setQuery] = useState('')
  const [formMode, setFormMode] = useState<EventFormMode | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [bulkOpen, setBulkOpen] = useState<boolean>(false)

  const queryClient = useQueryClient()

  // Conferences for the filter dropdown — same query key as AdminConferencesPanel
  // so adding/editing a conference there auto-refreshes this dropdown too.
  const confsQuery = useQuery({
    queryKey: ['admin', 'conferences'],
    queryFn: listAllConferences,
    staleTime: 30_000,
  })
  const conferences = confsQuery.data ?? CONFERENCES

  const params = useMemo(
    () => ({
      conference_id: conferenceId,
      locked: lockFilter === 'all' ? undefined : lockFilter === 'locked',
      is_manual: manualFilter === 'all' ? undefined : manualFilter === 'manual',
    }),
    [conferenceId, lockFilter, manualFilter],
  )

  const q = useQuery({
    queryKey: ['admin', 'events', params],
    queryFn: () => listAdminEvents(params),
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['admin', 'events'] })
    queryClient.invalidateQueries({ queryKey: ['events'] })
    queryClient.invalidateQueries({ queryKey: ['me', 'schedule'] })
  }

  const createMut = useMutation({
    mutationFn: (body: AdminEventCreate) => createAdminEvent(body),
    onSuccess: () => {
      invalidate()
      setFormMode(null)
      setFormError(null)
    },
    onError: (e: Error) => setFormError(e.message),
  })

  const updateMut = useMutation({
    mutationFn: (vars: { id: string; patch: AdminEventUpdate }) =>
      updateAdminEvent(vars.id, vars.patch),
    onSuccess: () => {
      invalidate()
      setFormMode(null)
      setFormError(null)
    },
    onError: (e: Error) => setFormError(e.message),
  })

  const deleteMut = useMutation({
    mutationFn: (id: string) => deleteAdminEvent(id),
    onSuccess: invalidate,
  })

  const lockMut = useMutation({
    mutationFn: (vars: { id: string; locked: boolean }) =>
      setAdminEventLock(vars.id, vars.locked),
    onSuccess: invalidate,
  })

  const rows = useMemo(() => {
    const all = q.data ?? []
    if (!query.trim()) return all
    const needle = query.toLowerCase()
    return all.filter(
      (e) =>
        e.title.toLowerCase().includes(needle) ||
        e.id.toLowerCase().includes(needle) ||
        (e.venue ?? '').toLowerCase().includes(needle),
    )
  }, [q.data, query])

  const onDelete = (e: AdminEvent) => {
    if (!confirm(`Delete event "${e.title}" (${e.id})? This is permanent.`)) return
    deleteMut.mutate(e.id)
  }

  return (
    <div className="admin__panel">
      <div className="admin__filters">
        <label className="admin__field">
          <span>Conference</span>
          <select value={conferenceId} onChange={(e) => setConferenceId(e.target.value)}>
            {conferences.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
                {'is_active' in c && c.is_active === false ? ' (draft)' : ''}
              </option>
            ))}
          </select>
        </label>

        <label className="admin__field">
          <span>Lock</span>
          <select value={lockFilter} onChange={(e) => setLockFilter(e.target.value as LockFilter)}>
            <option value="all">All</option>
            <option value="locked">Locked</option>
            <option value="unlocked">Unlocked</option>
          </select>
        </label>

        <label className="admin__field">
          <span>Origin</span>
          <select
            value={manualFilter}
            onChange={(e) => setManualFilter(e.target.value as ManualFilter)}
          >
            <option value="all">All</option>
            <option value="manual">Manual</option>
            <option value="scraped">Scraped / seed</option>
          </select>
        </label>

        <label className="admin__field admin__field--grow">
          <span>Search</span>
          <input
            placeholder="Title, id, or venue…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>

        <button
          type="button"
          className="admin-btn"
          onClick={() => setBulkOpen(true)}
          disabled={!conferenceId}
          title={
            conferenceId
              ? 'Bulk-import events from JSON for the selected conference'
              : 'Select a conference first'
          }
        >
          <Upload size={14} /> Import JSON
        </button>

        <button
          type="button"
          className="admin-btn admin-btn--primary"
          onClick={() => {
            setFormError(null)
            setFormMode({ kind: 'create', conferenceId })
          }}
        >
          <Plus size={14} /> New event
        </button>
      </div>

      <div className="admin__count">
        {q.isLoading ? 'Loading…' : `${rows.length} event${rows.length === 1 ? '' : 's'}`}
      </div>

      <div className="admin__table">
        <div className="admin__row admin__row--head">
          <div>Title / id</div>
          <div>Starts</div>
          <div>Venue</div>
          <div>Tags</div>
          <div>Flags</div>
          <div className="admin__actions-head">Actions</div>
        </div>
        {rows.map((e: AdminEvent) => (
          <div key={e.id} className="admin__row">
            <div className="admin__cell-main">
              <div className="admin__row-title">{e.title}</div>
              <div className="admin__row-id">{e.id}</div>
            </div>
            <div>{fmtDateTime(e.starts_at)}</div>
            <div>{e.venue ?? '—'}</div>
            <div className="admin__tags">
              {e.tags.map((t) => (
                <span key={t} className="admin__tag">
                  {t}
                </span>
              ))}
            </div>
            <div className="admin__flags">
              {e.is_manual && (
                <span className="admin__flag admin__flag--manual" title="Created by admin">
                  <Wrench size={12} /> manual
                </span>
              )}
              {e.locked ? (
                <span className="admin__flag admin__flag--locked" title="Scraper will skip this row">
                  <Lock size={12} /> locked
                </span>
              ) : (
                <span className="admin__flag admin__flag--unlocked" title="Scraper may overwrite">
                  <Unlock size={12} /> unlocked
                </span>
              )}
            </div>
            <div className="admin__actions">
              <button
                type="button"
                className="admin-btn admin-btn--icon"
                title="Edit"
                onClick={() => {
                  setFormError(null)
                  setFormMode({ kind: 'edit', event: e })
                }}
              >
                <Pencil size={14} />
              </button>
              <button
                type="button"
                className="admin-btn admin-btn--icon"
                title={e.locked ? 'Unlock (allow scraper)' : 'Lock (block scraper)'}
                onClick={() => lockMut.mutate({ id: e.id, locked: !e.locked })}
                disabled={lockMut.isPending}
              >
                {e.locked ? <Unlock size={14} /> : <Lock size={14} />}
              </button>
              <button
                type="button"
                className="admin-btn admin-btn--icon admin-btn--danger"
                title="Delete"
                onClick={() => onDelete(e)}
                disabled={deleteMut.isPending}
              >
                <Trash2 size={14} />
              </button>
            </div>
          </div>
        ))}
        {!q.isLoading && rows.length === 0 && (
          <div className="admin__empty">No events match these filters.</div>
        )}
      </div>

      {q.isError && (
        <div className="admin__error">Failed to load: {(q.error as Error).message}</div>
      )}

      {formMode && (
        <AdminEventForm
          mode={formMode}
          busy={createMut.isPending || updateMut.isPending}
          errorText={formError}
          onCancel={() => {
            setFormMode(null)
            setFormError(null)
          }}
          onSubmitCreate={(body) => createMut.mutate(body)}
          onSubmitEdit={(id, patch) => updateMut.mutate({ id, patch })}
        />
      )}

      {bulkOpen && (
        <AdminEventsBulkImport
          conferenceId={conferenceId}
          conferenceName={
            conferences.find((c) => c.id === conferenceId)?.name ?? conferenceId
          }
          onClose={() => setBulkOpen(false)}
          onApplied={invalidate}
        />
      )}
    </div>
  )
}
