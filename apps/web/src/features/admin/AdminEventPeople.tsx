import { FormEvent, useMemo, useState } from 'react'
import { Plus, X } from 'lucide-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  attachEventPerson,
  detachEventPerson,
  listConferenceSuggestions,
  listEventPeople,
  type AdminSuggestion,
} from '../../api/admin'

/**
 * "People on this event" — list current links, search/pick existing suggestions
 * to attach, and inline-create a new person.
 *
 * Renders only in event-edit mode; `eventId` and `conferenceId` are required.
 */
export function AdminEventPeople(props: {
  eventId: string
  conferenceId: string
}) {
  const { eventId, conferenceId } = props
  const queryClient = useQueryClient()

  const peopleOnEventQ = useQuery({
    queryKey: ['admin', 'event-people', eventId],
    queryFn: () => listEventPeople(eventId),
    enabled: !!eventId,
  })

  const confPeopleQ = useQuery({
    queryKey: ['admin', 'conf-people', conferenceId],
    queryFn: () => listConferenceSuggestions(conferenceId, { kind: 'people' }),
    enabled: !!conferenceId,
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['admin', 'event-people', eventId] })
    queryClient.invalidateQueries({ queryKey: ['admin', 'conf-people', conferenceId] })
  }

  const [searchInput, setSearchInput] = useState('')
  const [newName, setNewName] = useState('')
  const [newRole, setNewRole] = useState('')
  const [opError, setOpError] = useState<string | null>(null)

  const attachMut = useMutation({
    mutationFn: (body: { suggestion_id?: string; name?: string; role?: string | null }) =>
      attachEventPerson(eventId, body),
    onSuccess: () => {
      setSearchInput('')
      setNewName('')
      setNewRole('')
      setOpError(null)
      invalidate()
    },
    onError: (e: Error) => setOpError(e.message),
  })

  const detachMut = useMutation({
    mutationFn: (suggestionId: string) => detachEventPerson(eventId, suggestionId),
    onSuccess: invalidate,
    onError: (e: Error) => setOpError(e.message),
  })

  const linkedIds = useMemo(
    () => new Set((peopleOnEventQ.data ?? []).map((p) => p.suggestion_id)),
    [peopleOnEventQ.data],
  )

  // Candidate picker results: not-already-linked, name/role match search query.
  // Cap to 8 — anything more drowns the form. Admin can refine via search.
  const candidates: AdminSuggestion[] = useMemo(() => {
    const all = confPeopleQ.data ?? []
    const q = searchInput.trim().toLowerCase()
    const filtered = all.filter((p) => {
      if (linkedIds.has(p.id)) return false
      if (!q) return true
      const hay = `${p.name} ${p.role ?? ''}`.toLowerCase()
      return hay.includes(q)
    })
    return filtered.slice(0, 8)
  }, [confPeopleQ.data, linkedIds, searchInput])

  const submitNew = (e: FormEvent) => {
    e.preventDefault()
    const name = newName.trim()
    if (!name) return
    attachMut.mutate({ name, role: newRole.trim() || null })
  }

  return (
    <section className="admin__field admin-event-people">
      <span>People on this event</span>

      <ul className="admin-sources__list" style={{ marginTop: 6 }}>
        {peopleOnEventQ.isLoading && (
          <li className="admin-sources__empty">Loading…</li>
        )}
        {!peopleOnEventQ.isLoading && (peopleOnEventQ.data?.length ?? 0) === 0 && (
          <li className="admin-sources__empty">
            No one tagged yet — search or add a new person below.
          </li>
        )}
        {peopleOnEventQ.data?.map((p) => (
          <li key={p.suggestion_id} className="admin-sources__row">
            <div className="admin-sources__row-main">
              <div className="admin-sources__url">
                {p.name}
                {p.role && <span style={{ opacity: 0.7 }}> — {p.role}</span>}
              </div>
              <div className="admin-sources__meta">
                via <strong>{p.link_source}</strong>
                {p.confidence != null && (
                  <> · confidence: <strong>{p.confidence.toFixed(2)}</strong></>
                )}
              </div>
            </div>
            <button
              type="button"
              className="admin-btn admin-btn--icon admin-btn--danger"
              title="Remove from this event"
              onClick={() => detachMut.mutate(p.suggestion_id)}
              disabled={detachMut.isPending}
            >
              <X size={14} />
            </button>
          </li>
        ))}
      </ul>

      <div className="admin-drawer__row" style={{ marginTop: 8 }}>
        <label className="admin__field" style={{ flex: 1 }}>
          <span>Find existing person</span>
          <input
            type="text"
            placeholder="Search name or role…"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
          />
        </label>
      </div>

      {candidates.length > 0 && (
        <ul className="admin-sources__list" style={{ marginTop: 4 }}>
          {candidates.map((c) => (
            <li key={c.id} className="admin-sources__row">
              <div className="admin-sources__row-main">
                <div className="admin-sources__url">
                  {c.name}
                  {c.role && <span style={{ opacity: 0.7 }}> — {c.role}</span>}
                </div>
                <div className="admin-sources__meta">
                  source: <strong>{c.source ?? '—'}</strong>
                </div>
              </div>
              <button
                type="button"
                className="admin-btn"
                onClick={() => attachMut.mutate({ suggestion_id: c.id })}
                disabled={attachMut.isPending}
              >
                <Plus size={14} /> Add
              </button>
            </li>
          ))}
        </ul>
      )}

      <form className="admin-sources__add" onSubmit={submitNew} style={{ marginTop: 8 }}>
        <input
          type="text"
          placeholder="New person name"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
        />
        <input
          type="text"
          placeholder="Role (optional)"
          value={newRole}
          onChange={(e) => setNewRole(e.target.value)}
        />
        <button
          type="submit"
          className="admin-btn admin-btn--primary"
          disabled={attachMut.isPending || !newName.trim()}
        >
          <Plus size={14} />
          {attachMut.isPending ? 'Adding…' : 'Create + add'}
        </button>
      </form>

      {opError && <div className="admin__error">{opError}</div>}
    </section>
  )
}
