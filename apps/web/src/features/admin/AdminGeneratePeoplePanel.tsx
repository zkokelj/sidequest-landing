import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Sparkles, Trash2 } from 'lucide-react'

import {
  bulkDeleteConferenceSuggestions,
  generateConferencePeople,
  listConferenceSuggestions,
  type GeneratePeopleResult,
} from '../../api/admin'
import { EditableSuggestionRow } from './EditableSuggestionRow'

export function AdminGeneratePeoplePanel({ conferenceId }: { conferenceId: string }) {
  const queryClient = useQueryClient()

  const peopleKey = ['admin', 'conf-people', conferenceId]
  const peopleQ = useQuery({
    queryKey: peopleKey,
    queryFn: () => listConferenceSuggestions(conferenceId, { kind: 'people' }),
    enabled: !!conferenceId,
  })

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: peopleKey })
    // Public picker shares the underlying data — keep it fresh too.
    queryClient.invalidateQueries({
      queryKey: ['public', 'suggestions', conferenceId],
    })
  }

  const genMut = useMutation({
    mutationFn: () => generateConferencePeople(conferenceId),
    onSuccess: invalidateAll,
  })

  // Defaults to source='llm' — the common "wipe LLM rows and re-run" workflow.
  const deleteLlmMut = useMutation({
    mutationFn: () => bulkDeleteConferenceSuggestions(conferenceId, 'llm'),
    onSuccess: invalidateAll,
  })

  // Luma rows are scraper-replaceable — no confirm needed, the next scrape
  // recreates them. Useful for clearing noise after a bad scrape.
  const deleteLumaMut = useMutation({
    mutationFn: () => bulkDeleteConferenceSuggestions(conferenceId, 'luma'),
    onSuccess: invalidateAll,
  })

  // source='all' nukes Luma + manual + seed too. Behind a confirm() because
  // it's destructive and not what an admin clicking "Delete" usually wants.
  const deleteAllMut = useMutation({
    mutationFn: () => bulkDeleteConferenceSuggestions(conferenceId, 'all'),
    onSuccess: invalidateAll,
  })

  const result: GeneratePeopleResult | undefined = genMut.data
  const errMsg = (genMut.error as Error | null)?.message ?? null

  const counts = (peopleQ.data ?? []).reduce<Record<string, number>>((acc, p) => {
    const k = p.source ?? 'unknown'
    acc[k] = (acc[k] ?? 0) + 1
    return acc
  }, {})

  const onDeleteAll = () => {
    const total = peopleQ.data?.length ?? 0
    if (
      !confirm(
        `Delete ALL ${total} people (Luma + LLM + manual) for this conference?\n\n` +
          `This also removes their event links. Cannot be undone.`,
      )
    ) {
      return
    }
    deleteAllMut.mutate()
  }

  if (!conferenceId) return null

  return (
    <section className="admin-sources">
      <header className="admin-sources__head">
        <div>
          <div className="admin-sources__title">People for this conference</div>
          <div className="admin-sources__sub">
            Run the LLM to associate event descriptions with people and discover
            new attendees. Re-running is safe — confidence on existing links
            refreshes, no duplicates.
          </div>
        </div>
        <button
          type="button"
          className="admin-btn admin-btn--primary"
          onClick={() => genMut.mutate()}
          disabled={genMut.isPending}
          title="Generate people associations via LLM"
        >
          <Sparkles size={14} />
          {genMut.isPending ? 'Generating…' : 'Generate via LLM'}
        </button>
      </header>

      {result && (
        <div className={`admin-sources__run ${result.ok ? '' : 'is-error'}`}>
          {result.message}
          {result.tokens_used > 0 && (
            <> · <em>{result.tokens_used.toLocaleString()} tokens</em></>
          )}
        </div>
      )}
      {errMsg && <div className="admin-sources__run is-error">{errMsg}</div>}

      <div className="admin-sources__meta" style={{ padding: '8px 0' }}>
        Total people: <strong>{peopleQ.data?.length ?? 0}</strong>
        {Object.entries(counts).map(([src, n]) => (
          <span key={src}>
            {' · '}
            {src}: <strong>{n}</strong>
          </span>
        ))}
      </div>

      <div
        style={{
          display: 'flex',
          gap: 8,
          alignItems: 'center',
          flexWrap: 'wrap',
          marginBottom: 8,
        }}
      >
        <button
          type="button"
          className="admin-btn admin-btn--danger"
          onClick={() => deleteLlmMut.mutate()}
          disabled={deleteLlmMut.isPending || (counts.llm ?? 0) === 0}
          title="Wipe LLM-generated rows so the next run starts clean"
        >
          <Trash2 size={14} />
          {deleteLlmMut.isPending ? 'Deleting…' : `Delete LLM people (${counts.llm ?? 0})`}
        </button>
        <button
          type="button"
          className="admin-btn admin-btn--danger"
          onClick={() => deleteLumaMut.mutate()}
          disabled={deleteLumaMut.isPending || (counts.luma ?? 0) === 0}
          title="Wipe Luma-scraped rows. The next scrape will recreate them."
        >
          <Trash2 size={14} />
          {deleteLumaMut.isPending ? 'Deleting…' : `Delete Luma people (${counts.luma ?? 0})`}
        </button>
        <details>
          <summary
            style={{
              cursor: 'pointer',
              color: 'var(--fg-muted)',
              fontSize: 12,
              userSelect: 'none',
            }}
          >
            Danger zone
          </summary>
          <div style={{ marginTop: 6 }}>
            <button
              type="button"
              className="admin-btn admin-btn--danger"
              onClick={onDeleteAll}
              disabled={deleteAllMut.isPending || (peopleQ.data?.length ?? 0) === 0}
              title="Delete every person on this conference, regardless of source"
            >
              <Trash2 size={14} />
              {deleteAllMut.isPending ? 'Deleting…' : 'Delete ALL people'}
            </button>
          </div>
        </details>
      </div>

      <ul className="admin-sources__list">
        {peopleQ.isLoading && <li className="admin-sources__empty">Loading…</li>}
        {!peopleQ.isLoading && (peopleQ.data?.length ?? 0) === 0 && (
          <li className="admin-sources__empty">
            No people yet — run the generator or wait for the Luma scraper.
          </li>
        )}
        {peopleQ.data?.slice(0, 50).map((p) => (
          <EditableSuggestionRow
            key={p.id}
            person={{ id: p.id, name: p.name, role: p.role }}
            meta={
              <>
                id: <code>{p.id}</code> · source: <strong>{p.source ?? '—'}</strong>
              </>
            }
            invalidateKeys={[peopleKey, ['public', 'suggestions', conferenceId]]}
          />
        ))}
        {(peopleQ.data?.length ?? 0) > 50 && (
          <li className="admin-sources__empty">
            …showing 50 of {peopleQ.data?.length}.
          </li>
        )}
      </ul>
    </section>
  )
}
