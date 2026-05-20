import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Sparkles } from 'lucide-react'

import {
  generateConferencePeople,
  listConferenceSuggestions,
  type GeneratePeopleResult,
} from '../../api/admin'

export function AdminGeneratePeoplePanel({ conferenceId }: { conferenceId: string }) {
  const queryClient = useQueryClient()

  const peopleQ = useQuery({
    queryKey: ['admin', 'conf-people', conferenceId],
    queryFn: () => listConferenceSuggestions(conferenceId, { kind: 'people' }),
    enabled: !!conferenceId,
  })

  const genMut = useMutation({
    mutationFn: () => generateConferencePeople(conferenceId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'conf-people', conferenceId] })
    },
  })

  const result: GeneratePeopleResult | undefined = genMut.data
  const errMsg = (genMut.error as Error | null)?.message ?? null

  // Counts by source so admins can see "Luma:5, LLM:12, manual:2" at a glance.
  const counts = (peopleQ.data ?? []).reduce<Record<string, number>>((acc, p) => {
    const k = p.source ?? 'unknown'
    acc[k] = (acc[k] ?? 0) + 1
    return acc
  }, {})

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

      <ul className="admin-sources__list">
        {peopleQ.isLoading && <li className="admin-sources__empty">Loading…</li>}
        {!peopleQ.isLoading && (peopleQ.data?.length ?? 0) === 0 && (
          <li className="admin-sources__empty">
            No people yet — run the generator or wait for the Luma scraper.
          </li>
        )}
        {peopleQ.data?.slice(0, 30).map((p) => (
          <li key={p.id} className="admin-sources__row">
            <div className="admin-sources__row-main">
              <div className="admin-sources__url">
                {p.name}
                {p.role && <span style={{ opacity: 0.7 }}> — {p.role}</span>}
              </div>
              <div className="admin-sources__meta">
                id: <code>{p.id}</code> · source: <strong>{p.source ?? '—'}</strong>
              </div>
            </div>
          </li>
        ))}
        {(peopleQ.data?.length ?? 0) > 30 && (
          <li className="admin-sources__empty">
            …showing 30 of {peopleQ.data?.length}.
          </li>
        )}
      </ul>
    </section>
  )
}
