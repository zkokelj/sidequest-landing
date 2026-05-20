import { FormEvent, useEffect, useState } from 'react'
import { X } from 'lucide-react'

import type { AdminEvent, AdminEventCreate, AdminEventUpdate } from '../../api/admin'
import { AdminEventPeople } from './AdminEventPeople'

export type EventFormMode =
  | { kind: 'create'; conferenceId: string }
  | { kind: 'edit'; event: AdminEvent }

type Props = {
  mode: EventFormMode
  busy: boolean
  errorText: string | null
  onCancel: () => void
  onSubmitCreate: (body: AdminEventCreate) => void
  onSubmitEdit: (id: string, patch: AdminEventUpdate) => void
}

function toLocalInput(iso: string): string {
  // datetime-local needs `YYYY-MM-DDTHH:MM` — strip seconds and tz.
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function fromLocalInput(local: string): string {
  // Assume browser-local; emit ISO with timezone offset.
  if (!local) return ''
  return new Date(local).toISOString()
}

export function AdminEventForm(props: Props) {
  const isCreate = props.mode.kind === 'create'
  const seed = props.mode.kind === 'edit' ? props.mode.event : null
  const seedConfId = props.mode.kind === 'create' ? props.mode.conferenceId : seed!.conference_id

  const [id, setId] = useState(seed?.id ?? '')
  const [title, setTitle] = useState(seed?.title ?? '')
  const [conferenceId, setConferenceId] = useState(seedConfId)
  const [startsAt, setStartsAt] = useState(toLocalInput(seed?.starts_at ?? ''))
  const [endsAt, setEndsAt] = useState(toLocalInput(seed?.ends_at ?? ''))
  const [venue, setVenue] = useState(seed?.venue ?? '')
  const [description, setDescription] = useState(seed?.description ?? '')
  const [tagsStr, setTagsStr] = useState((seed?.tags ?? []).join(', '))
  const [url, setUrl] = useState(seed?.url ?? '')
  const [capacity, setCapacity] = useState(
    seed?.capacity != null ? String(seed.capacity) : '',
  )
  const [attendees, setAttendees] = useState(
    seed?.attendees != null ? String(seed.attendees) : '',
  )

  // Reset fields when the underlying mode changes (e.g. switching from edit to create)
  useEffect(() => {
    if (props.mode.kind === 'edit') {
      const e = props.mode.event
      setId(e.id)
      setTitle(e.title)
      setConferenceId(e.conference_id)
      setStartsAt(toLocalInput(e.starts_at))
      setEndsAt(toLocalInput(e.ends_at))
      setVenue(e.venue ?? '')
      setDescription(e.description ?? '')
      setTagsStr(e.tags.join(', '))
      setUrl(e.url ?? '')
      setCapacity(e.capacity != null ? String(e.capacity) : '')
      setAttendees(e.attendees != null ? String(e.attendees) : '')
    }
  }, [props.mode])

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const tags = tagsStr
      .split(',')
      .map((t) => t.trim())
      .filter(Boolean)
    const numOrNull = (s: string) => (s.trim() === '' ? null : Number(s))

    if (isCreate) {
      props.onSubmitCreate({
        id: id.trim(),
        conference_id: conferenceId.trim(),
        title: title.trim(),
        starts_at: fromLocalInput(startsAt),
        ends_at: fromLocalInput(endsAt),
        venue: venue.trim() || null,
        description: description.trim() || null,
        tags,
        url: url.trim() || null,
        capacity: numOrNull(capacity),
        attendees: numOrNull(attendees),
      })
    } else {
      props.onSubmitEdit(seed!.id, {
        title: title.trim(),
        conference_id: conferenceId.trim(),
        starts_at: fromLocalInput(startsAt),
        ends_at: fromLocalInput(endsAt),
        venue: venue.trim() || null,
        description: description.trim() || null,
        tags,
        url: url.trim() || null,
        capacity: numOrNull(capacity),
        attendees: numOrNull(attendees),
      })
    }
  }

  return (
    <div className="admin-drawer">
      <div className="admin-drawer__backdrop" onClick={props.onCancel} />
      <form className="admin-drawer__panel admin-edit-surface" onSubmit={submit}>
        <header className="admin-drawer__head">
          <h2>{isCreate ? 'New event' : 'Edit event'}</h2>
          <button
            type="button"
            className="admin-drawer__close"
            aria-label="Close"
            onClick={props.onCancel}
          >
            <X size={18} />
          </button>
        </header>

        {isCreate && (
          <label className="admin__field">
            <span>Event id</span>
            <input
              required
              value={id}
              onChange={(e) => setId(e.target.value)}
              placeholder="t2049-e13"
            />
          </label>
        )}

        <label className="admin__field">
          <span>Title</span>
          <input required value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>

        <label className="admin__field">
          <span>Conference id</span>
          <input
            required
            value={conferenceId}
            onChange={(e) => setConferenceId(e.target.value)}
          />
        </label>

        <div className="admin-drawer__row">
          <label className="admin__field">
            <span>Starts at</span>
            <input
              type="datetime-local"
              required
              value={startsAt}
              onChange={(e) => setStartsAt(e.target.value)}
            />
          </label>
          <label className="admin__field">
            <span>Ends at</span>
            <input
              type="datetime-local"
              required
              value={endsAt}
              onChange={(e) => setEndsAt(e.target.value)}
            />
          </label>
        </div>

        <label className="admin__field">
          <span>Venue</span>
          <input value={venue ?? ''} onChange={(e) => setVenue(e.target.value)} />
        </label>

        <label className="admin__field">
          <span>Description</span>
          <textarea
            rows={3}
            value={description ?? ''}
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>

        <label className="admin__field">
          <span>Tags (comma-separated)</span>
          <input value={tagsStr} onChange={(e) => setTagsStr(e.target.value)} />
        </label>

        <div className="admin-drawer__row">
          <label className="admin__field">
            <span>Capacity</span>
            <input
              type="number"
              value={capacity}
              onChange={(e) => setCapacity(e.target.value)}
            />
          </label>
          <label className="admin__field">
            <span>Attendees</span>
            <input
              type="number"
              value={attendees}
              onChange={(e) => setAttendees(e.target.value)}
            />
          </label>
        </div>

        <label className="admin__field">
          <span>URL</span>
          <input value={url ?? ''} onChange={(e) => setUrl(e.target.value)} />
        </label>

        {!isCreate && seed && (
          <AdminEventPeople eventId={seed.id} conferenceId={seed.conference_id} />
        )}

        {props.errorText && <div className="admin__error">{props.errorText}</div>}

        <div className="admin-drawer__actions">
          <button type="button" className="admin-btn" onClick={props.onCancel}>
            Cancel
          </button>
          <button type="submit" className="admin-btn admin-btn--primary" disabled={props.busy}>
            {props.busy ? 'Saving…' : isCreate ? 'Create' : 'Save changes'}
          </button>
        </div>
      </form>
    </div>
  )
}
