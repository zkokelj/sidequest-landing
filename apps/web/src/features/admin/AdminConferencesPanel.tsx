import { FormEvent, useEffect, useMemo, useState } from 'react'
import { Plus } from 'lucide-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  listAllConferences,
  upsertAdminConference,
  type AdminConferenceDay,
  type ConferenceFromApi,
} from '../../api/admin'
import { AdminGeneratePeoplePanel } from './AdminGeneratePeoplePanel'
import { AdminScrapeSourcesPanel } from './AdminScrapeSourcesPanel'

const NEW_MARKER = '__new__'

function jsonStringify(meta: Record<string, unknown>): string {
  try {
    return JSON.stringify(meta, null, 2)
  } catch {
    return '{}'
  }
}

function makeBlankConference(): ConferenceFromApi {
  return {
    id: '',
    name: '',
    city: null,
    venue: null,
    start_date: null,
    end_date: null,
    timezone: null,
    is_active: false,
    meta: {},
    days: [],
  }
}

export function AdminConferencesPanel() {
  const queryClient = useQueryClient()
  const q = useQuery({
    queryKey: ['admin', 'conferences'],
    queryFn: listAllConferences,
  })

  const [selectedId, setSelectedId] = useState<string | null>(null)

  // Auto-select first conference once data lands.
  useEffect(() => {
    if (!selectedId && q.data && q.data.length) {
      setSelectedId(q.data[0].id)
    }
  }, [q.data, selectedId])

  const selected = useMemo<ConferenceFromApi | null>(() => {
    if (selectedId === NEW_MARKER) return makeBlankConference()
    return q.data?.find((c) => c.id === selectedId) ?? null
  }, [q.data, selectedId])

  const onCreated = (id: string) => {
    queryClient.invalidateQueries({ queryKey: ['admin', 'conferences'] })
    setSelectedId(id)
  }

  return (
    <div className="admin__panel admin__panel--two-col">
      <aside className="admin__sidebar">
        <div className="admin__sidebar-head">Conferences</div>
        {q.isLoading && <div className="admin__count">Loading…</div>}
        {q.data?.map((c) => (
          <button
            key={c.id}
            type="button"
            className={`admin__side-item ${c.id === selectedId ? 'is-active' : ''}`}
            onClick={() => setSelectedId(c.id)}
          >
            <div className="admin__side-name">
              {c.name}
              {!c.is_active && <span className="admin__side-badge">draft</span>}
            </div>
            <div className="admin__side-meta">{c.id}</div>
          </button>
        ))}
        <button
          type="button"
          className={`admin__side-item admin__side-new ${
            selectedId === NEW_MARKER ? 'is-active' : ''
          }`}
          onClick={() => setSelectedId(NEW_MARKER)}
        >
          <Plus size={14} /> New conference
        </button>
      </aside>

      <section className="admin__detail admin-edit-surface">
        {selected ? (
          <ConferenceEditor
            conference={selected}
            isCreate={selectedId === NEW_MARKER}
            onSaved={onCreated}
          />
        ) : (
          <div className="admin__empty">Select a conference to edit.</div>
        )}
      </section>
    </div>
  )
}

function ConferenceEditor(props: {
  conference: ConferenceFromApi
  isCreate: boolean
  onSaved: (id: string) => void
}) {
  const c = props.conference

  const [id, setId] = useState(c.id)
  const [name, setName] = useState(c.name)
  const [city, setCity] = useState(c.city ?? '')
  const [venue, setVenue] = useState(c.venue ?? '')
  const [startDate, setStartDate] = useState(c.start_date ?? '')
  const [endDate, setEndDate] = useState(c.end_date ?? '')
  const [timezone, setTimezone] = useState(c.timezone ?? '')
  const [isActive, setIsActive] = useState(c.is_active)
  const [metaText, setMetaText] = useState(jsonStringify(c.meta))
  const [metaError, setMetaError] = useState<string | null>(null)
  const [days, setDays] = useState<AdminConferenceDay[]>(
    c.days.map((d) => ({
      day_num: d.num,
      dow: d.dow,
      date: d.date,
      enabled: d.enabled,
    })),
  )
  const [saveError, setSaveError] = useState<string | null>(null)

  useEffect(() => {
    // When the selected conference changes, reset all form fields.
    setId(c.id)
    setName(c.name)
    setCity(c.city ?? '')
    setVenue(c.venue ?? '')
    setStartDate(c.start_date ?? '')
    setEndDate(c.end_date ?? '')
    setTimezone(c.timezone ?? '')
    setIsActive(c.is_active)
    setMetaText(jsonStringify(c.meta))
    setMetaError(null)
    setDays(
      c.days.map((d) => ({
        day_num: d.num,
        dow: d.dow,
        date: d.date,
        enabled: d.enabled,
      })),
    )
    setSaveError(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [c.id, props.isCreate])

  const mut = useMutation({
    mutationFn: (body: Parameters<typeof upsertAdminConference>[0]) =>
      upsertAdminConference(body),
    onSuccess: (_data, vars) => {
      setSaveError(null)
      props.onSaved(vars.id)
    },
    onError: (e: Error) => setSaveError(e.message),
  })

  const submit = (e: FormEvent) => {
    e.preventDefault()
    let parsedMeta: Record<string, unknown> = {}
    try {
      parsedMeta = metaText.trim() ? JSON.parse(metaText) : {}
      setMetaError(null)
    } catch (err) {
      setMetaError(`Invalid JSON: ${(err as Error).message}`)
      return
    }
    if (!id.trim()) {
      setSaveError('Id is required.')
      return
    }
    mut.mutate({
      id: id.trim(),
      name: name.trim(),
      city: city.trim() || null,
      venue: venue.trim() || null,
      start_date: startDate || null,
      end_date: endDate || null,
      timezone: timezone.trim() || null,
      is_active: isActive,
      meta: parsedMeta,
      days,
    })
  }

  const toggleDay = (day_num: number) => {
    setDays((arr) =>
      arr.map((d) => (d.day_num === day_num ? { ...d, enabled: !d.enabled } : d)),
    )
  }

  return (
    <>
    <form className="admin__form" onSubmit={submit}>
      <div className="admin-drawer__row">
        <label className="admin__field">
          <span>Id {props.isCreate ? '(must be unique)' : '(immutable)'}</span>
          <input
            value={id}
            onChange={(e) => setId(e.target.value)}
            readOnly={!props.isCreate}
            disabled={!props.isCreate}
            placeholder="e.g. devcon7"
            required
          />
        </label>
        <label className="admin__field">
          <span>Name</span>
          <input required value={name} onChange={(e) => setName(e.target.value)} />
        </label>
      </div>

      <div className="admin-drawer__row">
        <label className="admin__field">
          <span>City</span>
          <input value={city} onChange={(e) => setCity(e.target.value)} />
        </label>
        <label className="admin__field">
          <span>Venue</span>
          <input value={venue} onChange={(e) => setVenue(e.target.value)} />
        </label>
      </div>

      <div className="admin-drawer__row">
        <label className="admin__field">
          <span>Start date</span>
          <input
            type="date"
            value={startDate ?? ''}
            onChange={(e) => setStartDate(e.target.value)}
          />
        </label>
        <label className="admin__field">
          <span>End date</span>
          <input
            type="date"
            value={endDate ?? ''}
            onChange={(e) => setEndDate(e.target.value)}
          />
        </label>
      </div>

      <label className="admin__field">
        <span>Timezone (IANA)</span>
        <input
          value={timezone}
          onChange={(e) => setTimezone(e.target.value)}
          placeholder="Asia/Dubai"
        />
      </label>

      <label className="admin-toggle">
        <input
          type="checkbox"
          checked={isActive}
          onChange={(e) => setIsActive(e.target.checked)}
        />
        <span className="admin-toggle__label">
          <strong>Active</strong>
          <em>
            {isActive
              ? 'Visible to users in the conference picker.'
              : 'Draft / hidden from the public picker.'}
          </em>
        </span>
      </label>

      <label className="admin__field">
        <span>Meta (JSON — gradient, meta_short, month)</span>
        <textarea
          rows={6}
          value={metaText}
          onChange={(e) => setMetaText(e.target.value)}
          spellCheck={false}
          style={{ fontFamily: 'var(--font-mono, monospace)', fontSize: 12 }}
        />
        {metaError && <span className="admin__error">{metaError}</span>}
      </label>

      <div className="admin__days">
        <div className="admin__days-head">
          Days
          <span className="admin__days-hint">Toggle which days are open for attendance</span>
        </div>
        <div className="admin__days-grid">
          {days.length === 0 && (
            <span className="admin__days-empty">
              No day rows yet — add them directly in the Supabase SQL editor for now.
            </span>
          )}
          {days.map((d) => (
            <label
              key={d.day_num}
              className={`admin__day-chip ${d.enabled ? 'is-on' : ''}`}
              title={d.date ?? ''}
            >
              <input
                type="checkbox"
                checked={d.enabled}
                onChange={() => toggleDay(d.day_num)}
              />
              <span>{d.dow}</span>
              <span className="admin__day-num">{d.day_num}</span>
            </label>
          ))}
        </div>
      </div>

      {saveError && <div className="admin__error">{saveError}</div>}

      <div className="admin-drawer__actions">
        <button
          type="submit"
          className="admin-btn admin-btn--primary"
          disabled={mut.isPending}
        >
          {mut.isPending
            ? 'Saving…'
            : props.isCreate
              ? 'Create conference'
              : 'Save changes'}
        </button>
      </div>

    </form>
    {!props.isCreate && c.id && (
      <>
        <AdminScrapeSourcesPanel conferenceId={c.id} />
        <AdminGeneratePeoplePanel conferenceId={c.id} />
      </>
    )}
    </>
  )
}
