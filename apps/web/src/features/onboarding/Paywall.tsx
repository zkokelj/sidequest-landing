import { useMemo, useRef, useState } from 'react'
import { Check, Lock, Zap } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

import { createCheckout } from '../../api/checkout'
import SymbolSVG from '../../assets/Symbol.svg'
import { EventCard } from '../../components/EventCard'
import type { SeedEvent } from '../../data/seedEvents'
import { useEvents } from '../../hooks/useEvents'
import { useOnboarding } from '../../stores/onboardingStore'

const FALLBACK_PREVIEW_LIMIT = 6

export default function Paywall() {
  const navigate = useNavigate()
  const { events } = useEvents()
  const curatedSchedule = useOnboarding((s) => s.curatedSchedule)
  const conferenceId = useOnboarding((s) => s.state.conferenceId)
  const [payState, setPayState] = useState<'idle' | 'loading' | 'error'>('idle')
  const [payError, setPayError] = useState<string | null>(null)
  const paywallEnabled = import.meta.env.VITE_PAYWALL_ENABLED === 'true'

  // Build the schedule preview shown on the left:
  // - If the LLM curated a schedule, show those events.
  // - Else show the first N events of the active conference as a basic
  //   teaser, so the paywall always has content.
  const scheduled: SeedEvent[] = useMemo(() => {
    const sortByStart = (a: SeedEvent, b: SeedEvent) =>
      a.day - b.day || a.start.localeCompare(b.start)

    const curatedIds = new Set((curatedSchedule ?? []).map((c) => c.event_id))
    if (curatedIds.size) {
      return events
        .filter((e) => curatedIds.has(e.id))
        .map((e) => ({ ...e, inSchedule: true }))
        .sort(sortByStart)
    }
    return events
      .slice()
      .sort(sortByStart)
      .slice(0, FALLBACK_PREVIEW_LIMIT)
      .map((e) => ({ ...e, inSchedule: true }))
  }, [events, curatedSchedule])

  // Group preview events by their day-of-month so the rendering works for any
  // conference (not just hardcoded April 29/30).
  const grouped = useMemo(() => {
    const map = new Map<number, SeedEvent[]>()
    for (const e of scheduled) {
      const arr = map.get(e.day) ?? []
      arr.push(e)
      map.set(e.day, arr)
    }
    return Array.from(map.entries()).sort(([a], [b]) => a - b)
  }, [scheduled])

  const dayLabel = (firstStart: string, day: number) => {
    const d = new Date(firstStart)
    if (Number.isNaN(d.getTime())) return `Day ${day}`
    return d.toLocaleDateString(undefined, {
      weekday: 'long',
      month: 'long',
      day: 'numeric',
    })
  }

  // Teaser blur: first few events crisp, rest progressively blurred so the
  // preview *shows* there's a full schedule without giving it away for free.
  const teaserBlurPx = (flatIndex: number): number => {
    if (flatIndex <= 2) return 0
    if (flatIndex === 3) return 2
    if (flatIndex === 4) return 4
    if (flatIndex === 5) return 6
    return 8
  }
  const teaserOpacity = (flatIndex: number): number => {
    if (flatIndex <= 2) return 1
    if (flatIndex === 3) return 0.92
    if (flatIndex === 4) return 0.78
    if (flatIndex === 5) return 0.65
    return 0.5
  }

  const scrollRef = useRef<HTMLDivElement>(null)
  const [progress, setProgress] = useState(0)
  const [stage, setStage] = useState<'hidden' | 'peek' | 'expanded'>('hidden')

  const handleScroll = () => {
    const el = scrollRef.current
    if (!el) return
    const max = el.scrollHeight - el.clientHeight
    if (max <= 0) return
    const p = Math.min(Math.max(el.scrollTop / max, 0), 1)
    setProgress(p)
    setStage((s) => {
      if (p > 0.55) return 'expanded'
      if (s === 'expanded') return 'expanded'
      if (p > 0.01) return 'peek'
      return s
    })
  }

  const onPay = async () => {
    if (payState === 'loading') return
    if (!paywallEnabled) {
      navigate('/paywall/thanks')
      return
    }
    setPayState('loading')
    setPayError(null)
    try {
      const { checkout_url } = await createCheckout(conferenceId)
      window.location.href = checkout_url
    } catch (err) {
      setPayState('error')
      setPayError(err instanceof Error ? err.message : 'Could not start checkout')
    }
  }

  return (
    <div className="sq-app">
      <div className="sq-frame">
        <div className="paywall" data-stage={stage} role="dialog" aria-label="Unlock SideQuest">
          <div ref={scrollRef} className="paywall__behind" onScroll={handleScroll}>
            <div
              className="paywall__behind-inner"
              style={{
                filter: `blur(${(progress * 10).toFixed(2)}px)`,
                opacity: 1 - progress * 0.3,
              }}
            >
              <h1 className="paywall__schedule-title">Your schedule</h1>
              {grouped.length === 0 && (
                <div className="paywall__day paywall__day--secondary">
                  Loading your preview…
                </div>
              )}
              {(() => {
                let flat = -1
                return grouped.map(([day, items], i) => (
                  <div key={day}>
                    <div
                      className={`paywall__day${i > 0 ? ' paywall__day--secondary' : ''}`}
                    >
                      <span className="paywall__day-dot" />
                      {dayLabel(items[0].start, day)} · {items.length} event
                      {items.length === 1 ? '' : 's'}
                    </div>
                    <div className="paywall__events">
                      {items.map((e) => {
                        flat += 1
                        const blur = teaserBlurPx(flat)
                        const opacity = teaserOpacity(flat)
                        return (
                          <div
                            key={e.id}
                            className="paywall__event-wrap"
                            style={{
                              filter: blur ? `blur(${blur}px)` : undefined,
                              opacity,
                              userSelect: blur >= 4 ? 'none' : undefined,
                              pointerEvents: blur >= 4 ? 'none' : undefined,
                            }}
                            aria-hidden={blur >= 4 ? 'true' : undefined}
                          >
                            <EventCard event={e} />
                          </div>
                        )
                      })}
                    </div>
                  </div>
                ))
              })()}
              <div className="paywall__behind-spacer" />
            </div>
          </div>

          <div className="paywall__sheet">
            <div className="paywall__handle" />
            <div className="paywall__brand">
              <span className="paywall__lock">
                <Lock size={14} />
              </span>
              <img src={SymbolSVG} alt="" className="paywall__mark" />
            </div>
            <h2 className="paywall__heading">Unlock your full SideQuest</h2>
            <p className="paywall__sub">
              One-time payment. Full access to your TOKEN2049 plan, the AI agent, and every event.
            </p>
            <ul className="paywall__features">
              <li>
                <span className="paywall__feat-icon">
                  <Zap size={14} />
                </span>
                Full personalized schedule across all days
              </li>
              <li>
                <span className="paywall__feat-icon">
                  <Zap size={14} />
                </span>
                AI agent with chat, routing &amp; dress codes
              </li>
              <li>
                <span className="paywall__feat-icon paywall__feat-icon--check">
                  <Check size={14} />
                </span>
                Add/remove events, see all 384 events
              </li>
            </ul>
            <div className="paywall__price">
              <span className="paywall__currency">$</span>
              <span className="paywall__amount">9.99</span>
            </div>
            <div className="paywall__price-sub">One-time · Lifetime access for this conference</div>
            <button
              className="paywall__cta"
              type="button"
              onClick={onPay}
              disabled={payState === 'loading'}
              aria-busy={payState === 'loading'}
            >
              <svg
                width="16"
                height="20"
                viewBox="0 0 16 20"
                aria-hidden="true"
                className="paywall__cta-apple"
              >
                <path
                  fill="currentColor"
                  d="M13.07 10.65c-.02-2.18 1.78-3.23 1.86-3.28-1.02-1.49-2.6-1.69-3.16-1.71-1.34-.13-2.62.79-3.3.79-.7 0-1.74-.77-2.86-.75-1.47.02-2.83.85-3.59 2.16-1.53 2.65-.39 6.57 1.1 8.72.73 1.05 1.6 2.23 2.74 2.19 1.1-.04 1.51-.71 2.84-.71 1.32 0 1.7.71 2.86.69 1.18-.02 1.93-1.07 2.65-2.13.84-1.22 1.18-2.4 1.2-2.46-.03-.01-2.3-.88-2.32-3.5zM10.92 4.04c.6-.74 1.01-1.75.9-2.77-.87.04-1.93.58-2.56 1.31-.56.65-1.05 1.69-.92 2.69.97.07 1.97-.49 2.58-1.23z"
                />
              </svg>
              <span>
                {payState === 'loading' ? (
                  'Opening checkout…'
                ) : (
                  <>
                    Buy with&nbsp;<span className="paywall__cta-pay">Pay</span>
                  </>
                )}
              </span>
            </button>
            <button
              className="paywall__alt"
              type="button"
              onClick={onPay}
              disabled={payState === 'loading'}
            >
              Other payment methods
            </button>
            {payError && (
              <div className="paywall__footer" role="alert" style={{ color: '#E62C5A' }}>
                {payError}
              </div>
            )}
            {!payError && (
              <div className="paywall__footer">
                Secure checkout · Cancel anytime · Powered by Polar
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
