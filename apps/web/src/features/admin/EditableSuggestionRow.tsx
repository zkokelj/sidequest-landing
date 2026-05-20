import { FormEvent, ReactNode, useState } from 'react'
import { Check, Pencil, X as XIcon } from 'lucide-react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { patchSuggestion, type AdminSuggestion } from '../../api/admin'

type Props = {
  /** The underlying conference_suggestions row. */
  person: { id: string; name: string; role: string | null }
  /** Sub-line text shown below the name when not editing (e.g. "source: luma"). */
  meta?: ReactNode
  /** Optional trailing controls (e.g. a remove-from-event X button). */
  trailing?: ReactNode
  /** React-Query keys to invalidate after a successful save. */
  invalidateKeys?: unknown[][]
}

/**
 * One person row with inline name/role editing.
 *
 * Used in both AdminGeneratePeoplePanel (per-conference list) and
 * AdminEventPeople (per-event list) so the affordances are identical.
 *
 * Pencil → editing mode (name + role inputs); Check saves, X cancels.
 * Save is optimistic-feeling because React Query invalidates the parent
 * lists after success, but we don't preemptively mutate the cache here.
 */
export function EditableSuggestionRow(props: Props) {
  const { person, meta, trailing, invalidateKeys = [] } = props
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(person.name)
  const [role, setRole] = useState(person.role ?? '')
  const [opError, setOpError] = useState<string | null>(null)

  const mut = useMutation({
    mutationFn: () =>
      patchSuggestion(person.id, {
        name: name.trim(),
        // Empty string explicitly clears role on the server.
        role: role.trim() === '' ? '' : role.trim(),
      }),
    onSuccess: (_data: AdminSuggestion) => {
      setEditing(false)
      setOpError(null)
      for (const k of invalidateKeys) queryClient.invalidateQueries({ queryKey: k })
    },
    onError: (e: Error) => setOpError(e.message),
  })

  const startEdit = () => {
    setName(person.name)
    setRole(person.role ?? '')
    setOpError(null)
    setEditing(true)
  }

  const cancel = () => {
    setEditing(false)
    setOpError(null)
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!name.trim()) {
      setOpError('Name is required.')
      return
    }
    mut.mutate()
  }

  if (editing) {
    return (
      <li className="admin-sources__row">
        <form
          onSubmit={submit}
          className="admin-sources__row-main"
          style={{ display: 'flex', flexDirection: 'column', gap: 4 }}
        >
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Name"
            autoFocus
          />
          <input
            type="text"
            value={role}
            onChange={(e) => setRole(e.target.value)}
            placeholder="Role (optional)"
          />
          {opError && <span className="admin__error">{opError}</span>}
        </form>
        <button
          type="button"
          className="admin-btn admin-btn--icon admin-btn--primary"
          title="Save"
          onClick={() => submit({ preventDefault: () => undefined } as FormEvent)}
          disabled={mut.isPending || !name.trim()}
        >
          <Check size={14} />
        </button>
        <button
          type="button"
          className="admin-btn admin-btn--icon"
          title="Cancel"
          onClick={cancel}
          disabled={mut.isPending}
        >
          <XIcon size={14} />
        </button>
      </li>
    )
  }

  return (
    <li className="admin-sources__row">
      <div className="admin-sources__row-main">
        <div className="admin-sources__url">
          {person.name}
          {person.role && <span style={{ opacity: 0.7 }}> — {person.role}</span>}
        </div>
        {meta && <div className="admin-sources__meta">{meta}</div>}
      </div>
      <button
        type="button"
        className="admin-btn admin-btn--icon"
        title="Edit name + role"
        onClick={startEdit}
      >
        <Pencil size={14} />
      </button>
      {trailing}
    </li>
  )
}
