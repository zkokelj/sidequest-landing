import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Ban,
  Calendar,
  CalendarDays,
  ChevronLeft,
  Clock,
  Compass,
  Info,
  Plus,
  Search,
  Star,
  User as UserIcon,
  X,
} from 'lucide-react'
import { useMutation } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'

import SymbolSVG from '../../assets/Symbol.svg'
import WhiteLogoSVG from '../../assets/White.svg'
import { CONFERENCES, type Conference } from '../../data/conferences'
import { GOALS_BY_ROLE, ROLES, TOPICS } from '../../data/roles'
import { SUGGESTIONS } from '../../data/suggestions'
import { curate } from '../../api/curate'
import { useConferences } from '../../hooks/useConferences'
import { useOnboarding } from '../../stores/onboardingStore'
import type { Attendance, OnboardingState, Role } from '../../types'

const TOTAL_STEPS = 10

const SQMark = ({ size = 100 }: { size?: number }) => (
  <img src={SymbolSVG} alt="SideQuest" style={{ width: size, height: size }} />
)

function Slider({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const setFromClientX = (clientX: number) => {
    const el = ref.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    const pct = Math.max(0, Math.min(100, ((clientX - rect.left) / rect.width) * 100))
    onChange(Math.round(pct))
  }
  return (
    <div
      ref={ref}
      className="slider-track"
      onPointerDown={(e) => {
        ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
        setFromClientX(e.clientX)
      }}
      onPointerMove={(e) => {
        if (e.buttons === 0) return
        setFromClientX(e.clientX)
      }}
    >
      <div className="slider-fill" style={{ width: `${value}%` }} />
      <div className="slider-thumb" style={{ left: `${value}%` }} />
    </div>
  )
}

function Header({
  step,
  total,
  onBack,
  onSkip,
  hideSkip,
  hideBack,
}: {
  step: number
  total: number
  onBack?: () => void
  onSkip?: () => void
  hideSkip?: boolean
  hideBack?: boolean
}) {
  return (
    <div className="scr__top">
      <button
        className="scr__back"
        onClick={onBack}
        disabled={hideBack}
        style={hideBack ? { visibility: 'hidden' } : undefined}
        aria-label="Back"
      >
        <ChevronLeft />
      </button>
      <div className="scr__progress">
        {Array.from({ length: total }).map((_, i) => (
          <span key={i} className={i < step - 1 ? 'done' : i === step - 1 ? 'cur' : ''} />
        ))}
      </div>
      <button
        className="scr__skip"
        onClick={onSkip}
        style={{ visibility: hideSkip ? 'hidden' : 'visible' }}
      >
        Skip
      </button>
    </div>
  )
}

export default function Onboarding() {
  const navigate = useNavigate()
  const store = useOnboarding()
  const [step, setStep] = useState(store.step)
  const state = store.state

  useEffect(() => {
    store.setStep(step)
  }, [step]) // eslint-disable-line react-hooks/exhaustive-deps

  const { conferences } = useConferences()
  const conf = useMemo(
    () =>
      conferences.find((c) => c.id === state.conferenceId) ??
      CONFERENCES.find((c) => c.id === state.conferenceId) ??
      conferences[0] ??
      CONFERENCES[0],
    [conferences, state.conferenceId],
  )

  const next = () => setStep((s) => s + 1)
  const back = () => setStep((s) => Math.max(0, s - 1))
  const skip = () => setStep((s) => s + 1)
  const goTo = (s: number) => setStep(s)

  const curateMutation = useMutation({
    mutationFn: (s: OnboardingState) => curate(s),
    onSuccess: (data) => {
      store.setCurated(data.curate_id, data.schedule)
    },
  })

  // Fire curation when entering the loading screen (step 11).
  useEffect(() => {
    if (step === 11) {
      curateMutation.mutate(state)
      const t = setTimeout(() => setStep(12), 3200)
      return () => clearTimeout(t)
    }
  }, [step]) // eslint-disable-line react-hooks/exhaustive-deps

  const onComplete = () => navigate('/paywall')

  return (
    <div className="sq-app">
      <div className="sq-frame">
        {step === 0 && <Welcome onStart={next} onSkip={() => setStep(12)} />}
        {step === 1 && (
          <ConferencePicker
            value={state.conferenceId}
            onChange={(v) => store.set({ conferenceId: v })}
            onBack={back}
            onNext={next}
            conferences={conferences}
          />
        )}
        {step === 2 && (
          <AttendanceStep
            conf={conf}
            value={state.attendance}
            onChange={(v) => store.set({ attendance: v })}
            onBack={back}
            onNext={() => setStep(state.attendance === 'full' ? 4 : 3)}
          />
        )}
        {step === 3 && (
          <DaysStep
            conf={conf}
            value={state.days}
            onChange={(v) => store.set({ days: v })}
            onBack={back}
            onNext={next}
          />
        )}
        {step === 4 && (
          <RoleStep
            value={state.role}
            onChange={(v) => store.set({ role: v, goals: [] })}
            onBack={back}
            onNext={next}
          />
        )}
        {step === 5 && (
          <GoalsStep
            role={state.role}
            value={state.goals}
            onChange={(v) => store.set({ goals: v })}
            onBack={back}
            onNext={next}
          />
        )}
        {step === 6 && (
          <TopicsStep
            value={state.topics}
            onChange={(v) => store.set({ topics: v })}
            onBack={back}
            onSkip={skip}
            onNext={next}
          />
        )}
        {step === 7 && (
          <ScheduleStep
            pace={state.pace}
            energy={state.energy}
            onChange={(p, e) => store.set({ pace: p, energy: e })}
            onBack={back}
            onSkip={skip}
            onNext={next}
          />
        )}
        {step === 8 && (
          <SocialStep
            value={state.social}
            onChange={(v) => store.set({ social: v })}
            onBack={back}
            onSkip={skip}
            onNext={next}
          />
        )}
        {step === 9 && (
          <MustHavesStep
            value={state.mustHaves}
            onChange={(v) => store.set({ mustHaves: v })}
            onBack={back}
            onSkip={skip}
            onNext={next}
          />
        )}
        {step === 10 && (
          <ReviewStep
            state={state}
            conf={conf}
            onBack={back}
            onEdit={goTo}
            onNext={() => setStep(11)}
          />
        )}
        {step === 11 && <Curating role={state.role} />}
        {step === 12 && (
          <Done conf={conf} onRestart={() => setStep(0)} onEnter={onComplete} />
        )}
      </div>
    </div>
  )
}

/* ---------- Step components ---------- */

function Welcome({ onStart, onSkip }: { onStart: () => void; onSkip: () => void }) {
  return (
    <div className="scr-welcome">
      <div className="scr-welcome__hero">
        <button className="scr-welcome__skip" onClick={onSkip}>
          Skip
        </button>
        <img src={WhiteLogoSVG} alt="SideQuest" className="scr-welcome__lockup" />
      </div>
      <div className="scr-welcome__panel">
        <Header step={1} total={TOTAL_STEPS} hideBack hideSkip />
        <div className="scr__step-label">Step 1 of 10</div>
        <h1 className="scr-welcome__title">
          Your <em>perfect</em> conference, planned in 90 seconds.
        </h1>
        <p className="scr-welcome__body">
          Tell us a little about you. We'll build a schedule of talks, side-events and people
          worth meeting — tuned to how you work the room.
        </p>
        <ul className="scr-welcome__bullets">
          <li>Talks matched to your goals</li>
          <li>Side-events for your style</li>
          <li>People worth meeting</li>
        </ul>
        <button className="btn-primary" style={{ marginTop: 'auto' }} onClick={onStart}>
          Get started
        </button>
      </div>
    </div>
  )
}

function ConferencePicker({
  value,
  onChange,
  onBack,
  onNext,
  conferences,
}: {
  value: string
  onChange: (v: string) => void
  onBack: () => void
  onNext: () => void
  conferences: Conference[]
}) {
  return (
    <div className="scr">
      <Header step={2} total={TOTAL_STEPS} onBack={onBack} hideSkip />
      <div className="scr__step-label">Step 2 of 10</div>
      <h1 className="scr__title">Which conference?</h1>
      <p className="scr__sub">We'll plan around the dates and venue.</p>
      {conferences.map((c) => (
        <button
          key={c.id}
          className={`conf-card${value === c.id ? ' active' : ''}`}
          onClick={() => onChange(c.id)}
        >
          <div className="conf-card__img" style={{ background: c.gradient }} />
          <div className="conf-card__body">
            <div className="conf-card__title">{c.name}</div>
            <div className="conf-card__meta">{c.meta}</div>
          </div>
          <div className="conf-card__radio" />
        </button>
      ))}
      <button className="btn-tertiary" style={{ alignSelf: 'flex-start' }}>
        + Search for another conference
      </button>
      <div className="scr__cta">
        <button className="btn-primary" onClick={onNext}>
          Continue
        </button>
      </div>
    </div>
  )
}

function AttendanceStep({
  conf,
  value,
  onChange,
  onBack,
  onNext,
}: {
  conf: Conference
  value: Attendance | null
  onChange: (v: Attendance) => void
  onBack: () => void
  onNext: () => void
}) {
  const opts: { id: Attendance; title: string; desc: string; Icon: typeof Calendar }[] = [
    { id: 'full', title: 'Full attendance', desc: 'All days — main stage and side events', Icon: Calendar },
    { id: 'partial', title: 'Partial — only some days', desc: "You'll pick which days next", Icon: CalendarDays },
    { id: 'side-only', title: 'Side events only', desc: 'No main pass — focus on networking', Icon: Ban },
  ]
  return (
    <div className="scr">
      <Header step={3} total={TOTAL_STEPS} onBack={onBack} hideSkip />
      <div className="scr__step-label">{conf.name} · Step 3 of 10</div>
      <h1 className="scr__title">How are you attending?</h1>
      <p className="scr__sub">Lets us tune the schedule to your time on the ground.</p>
      {opts.map((o) => (
        <button
          key={o.id}
          className={`opt${value === o.id ? ' active' : ''}`}
          onClick={() => onChange(o.id)}
        >
          <div className="opt__icon">
            <o.Icon />
          </div>
          <div className="opt__body">
            <div className="opt__title">{o.title}</div>
            <div className="opt__desc">{o.desc}</div>
          </div>
        </button>
      ))}
      <div className="scr__cta">
        <button className="btn-primary" disabled={!value} onClick={onNext}>
          Continue
        </button>
      </div>
    </div>
  )
}

function DaysStep({
  conf,
  value,
  onChange,
  onBack,
  onNext,
}: {
  conf: Conference
  value: number[]
  onChange: (v: number[]) => void
  onBack: () => void
  onNext: () => void
}) {
  const toggle = (n: number) => {
    onChange(value.includes(n) ? value.filter((x) => x !== n) : [...value, n])
  }
  const days = conf.days.length
    ? conf.days
    : [
        { dow: 'Mon', num: 1, enabled: true },
        { dow: 'Tue', num: 2, enabled: true },
        { dow: 'Wed', num: 3, enabled: true },
      ]
  const selected = days.filter((d) => value.includes(d.num))
  return (
    <div className="scr">
      <Header step={4} total={TOTAL_STEPS} onBack={onBack} hideSkip />
      <div className="scr__step-label">Step 4 of 10</div>
      <h1 className="scr__title">Which days are you in?</h1>
      <p className="scr__sub">Tap to select. We'll only suggest events on these days.</p>
      <div
        style={{
          fontSize: 11,
          fontWeight: 600,
          color: 'var(--fg-muted)',
          marginBottom: 8,
          letterSpacing: '0.05em',
        }}
      >
        {conf.month}
      </div>
      <div className="days-row">
        {days.map((d) => (
          <button
            key={d.num}
            className={`day${value.includes(d.num) ? ' active' : ''}${!d.enabled ? ' disabled' : ''}`}
            onClick={() => d.enabled && toggle(d.num)}
            disabled={!d.enabled}
          >
            <span className="day__dow">{d.dow}</span>
            <span className="day__num">{String(d.num).padStart(2, '0')}</span>
          </button>
        ))}
      </div>
      <div
        style={{
          fontSize: 12,
          color: 'var(--fg-muted)',
          padding: '12px 14px',
          background: 'var(--bg-surface)',
          borderRadius: 10,
          display: 'flex',
          gap: 8,
          alignItems: 'center',
        }}
      >
        <Info size={14} style={{ color: 'var(--fg-action)' }} />
        <span>
          {selected.length > 0 ? (
            <>
              You'll be in town for{' '}
              <strong style={{ color: 'var(--fg-default)' }}>
                {selected.length} day{selected.length > 1 ? 's' : ''}
              </strong>
              {' · '}
              {selected.map((s) => s.dow).join(' & ')}
            </>
          ) : (
            'Select at least one day'
          )}
        </span>
      </div>
      <div className="scr__cta">
        <button className="btn-primary" disabled={value.length === 0} onClick={onNext}>
          Continue
        </button>
      </div>
    </div>
  )
}

function RoleStep({
  value,
  onChange,
  onBack,
  onNext,
}: {
  value: Role | null
  onChange: (v: Role) => void
  onBack: () => void
  onNext: () => void
}) {
  return (
    <div className="scr">
      <Header step={5} total={TOTAL_STEPS} onBack={onBack} hideSkip />
      <div className="scr__step-label">Step 5 of 10</div>
      <h1 className="scr__title">Which best describes you?</h1>
      <p className="scr__sub">Pick one. You can always change it later in your profile.</p>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        {ROLES.map((r) => (
          <button
            key={r.id}
            className={`opt opt--vertical${value === r.id ? ' active' : ''}`}
            onClick={() => onChange(r.id)}
          >
            <div className="opt__icon">{r.emoji}</div>
            <div>
              <div className="opt__title">{r.label}</div>
              <div className="opt__desc">{r.desc}</div>
            </div>
          </button>
        ))}
      </div>
      <div className="scr__cta">
        <button className="btn-primary" disabled={!value} onClick={onNext}>
          Continue
        </button>
      </div>
    </div>
  )
}

function GoalsStep({
  role,
  value,
  onChange,
  onBack,
  onNext,
}: {
  role: Role | null
  value: string[]
  onChange: (v: string[]) => void
  onBack: () => void
  onNext: () => void
}) {
  const goals = role ? GOALS_BY_ROLE[role] : GOALS_BY_ROLE.founder
  const roleLabel = role ? ROLES.find((r) => r.id === role)!.label : 'you'
  // Self-heal: drop any persisted goal ids that don't belong to the current
  // role's goal list (happens when role was changed without clearing goals
  // in older builds).
  const validIds = useMemo(() => new Set(goals.map((g) => g.id)), [goals])
  const filtered = useMemo(() => value.filter((id) => validIds.has(id)), [value, validIds])
  useEffect(() => {
    if (filtered.length !== value.length) onChange(filtered)
  }, [filtered, value, onChange])
  const toggle = (id: string) => {
    if (filtered.includes(id)) {
      onChange(filtered.filter((x) => x !== id))
    } else if (filtered.length < 3) {
      onChange([...filtered, id])
    }
  }
  return (
    <div className="scr">
      <Header step={6} total={TOTAL_STEPS} onBack={onBack} hideSkip />
      <div className="scr__step-label">Step 6 of 10</div>
      <h1 className="scr__title">As a {roleLabel}, what are you here to do?</h1>
      <p className="scr__sub">Pick up to 3 — order matters, top one weighs most.</p>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 12 }}>
        {goals.map((g) => {
          const idx = filtered.indexOf(g.id)
          const active = idx >= 0
          return (
            <button
              key={g.id}
              className={`opt opt--vertical${active ? ' active' : ''}`}
              onClick={() => toggle(g.id)}
            >
              <div className="opt__icon">{g.emoji}</div>
              <div>
                <div className="opt__title">{g.label}</div>
                <div className="opt__desc">{g.desc}</div>
              </div>
            </button>
          )
        })}
      </div>
      <div style={{ fontSize: 12, color: 'var(--fg-muted)', textAlign: 'center' }}>
        {filtered.length} of 3 selected
      </div>
      <div className="scr__cta">
        <button className="btn-primary" disabled={filtered.length === 0} onClick={onNext}>
          Continue
        </button>
      </div>
    </div>
  )
}

function TopicsStep({
  value,
  onChange,
  onBack,
  onSkip,
  onNext,
}: {
  value: string[]
  onChange: (v: string[]) => void
  onBack: () => void
  onSkip: () => void
  onNext: () => void
}) {
  const toggle = (t: string) => {
    onChange(value.includes(t) ? value.filter((x) => x !== t) : [...value, t])
  }
  return (
    <div className="scr">
      <Header step={7} total={TOTAL_STEPS} onBack={onBack} onSkip={onSkip} />
      <div className="scr__step-label">Step 7 of 10</div>
      <h1 className="scr__title">What topics interest you?</h1>
      <p className="scr__sub">Tap any number. We'll surface talks and people across these.</p>
      <div className="chip-grid">
        {TOPICS.map((t) => (
          <button
            key={t}
            className={`chip${value.includes(t) ? ' active' : ''}`}
            onClick={() => toggle(t)}
          >
            {t}
          </button>
        ))}
      </div>
      <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>{value.length} selected</div>
      <div className="scr__cta">
        <button className="btn-primary" onClick={onNext}>
          Continue{value.length > 0 ? ` (${value.length})` : ''}
        </button>
      </div>
    </div>
  )
}

function ScheduleStep({
  pace,
  energy,
  onChange,
  onBack,
  onSkip,
  onNext,
}: {
  pace: number
  energy: number
  onChange: (p: number, e: number) => void
  onBack: () => void
  onSkip: () => void
  onNext: () => void
}) {
  const paceLabel = pace < 25 ? 'Relaxed' : pace < 55 ? 'Selective' : pace < 80 ? 'Balanced' : 'Packed'
  const energyLabel = energy < 35 ? 'Early bird' : energy < 70 ? 'Mid-day' : 'Night owl'
  const eventsPerDay = Math.round(3 + (pace / 100) * 6)
  const startTime = energy < 35 ? '8:00 AM' : energy < 70 ? '10:00 AM' : '12:00 PM'
  return (
    <div className="scr">
      <Header step={8} total={TOTAL_STEPS} onBack={onBack} onSkip={onSkip} />
      <div className="scr__step-label">Step 8 of 10</div>
      <h1 className="scr__title">How do you like to do conferences?</h1>
      <p className="scr__sub">Tune the pace. We'll match.</p>
      <div className="slider-block">
        <div className="slider-block__label">
          <span>Pace</span>
          <strong>{paceLabel}</strong>
        </div>
        <Slider value={pace} onChange={(v) => onChange(v, energy)} />
        <div className="slider-block__caps">
          <span>Relaxed (3–4 / day)</span>
          <span>Packed (8+)</span>
        </div>
      </div>
      <div className="slider-block">
        <div className="slider-block__label">
          <span>Energy</span>
          <strong>{energyLabel}</strong>
        </div>
        <Slider value={energy} onChange={(v) => onChange(pace, v)} />
        <div className="slider-block__caps">
          <span>🌅 Mornings</span>
          <span>Late nights 🌙</span>
        </div>
      </div>
      <div
        style={{
          marginTop: 8,
          padding: 14,
          borderRadius: 12,
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-subtle)',
          fontSize: 13,
          lineHeight: 1.5,
        }}
      >
        <span style={{ color: 'var(--fg-muted)' }}>We'll plan you </span>
        <strong>~{eventsPerDay} events per day</strong>
        <span style={{ color: 'var(--fg-muted)' }}>, starting around </span>
        <strong>{startTime}</strong>.
      </div>
      <div className="scr__cta">
        <button className="btn-primary" onClick={onNext}>
          Continue
        </button>
      </div>
    </div>
  )
}

function SocialStep({
  value,
  onChange,
  onBack,
  onSkip,
  onNext,
}: {
  value: number
  onChange: (v: number) => void
  onBack: () => void
  onSkip: () => void
  onNext: () => void
}) {
  const styleTitle = value < 35 ? 'Quality conversations' : value < 70 ? 'Mix it up' : 'Big rooms'
  const styleDesc =
    value < 35
      ? "We'll prioritize founder dinners, smaller meetups, and 1:1 intro requests over big mixers."
      : value < 70
      ? "A balance of mixers and intimate dinners — we'll alternate."
      : "We'll lean into big mixers, parties, and conference-wide socials."
  return (
    <div className="scr">
      <Header step={9} total={TOTAL_STEPS} onBack={onBack} onSkip={onSkip} />
      <div className="scr__step-label">Step 9 of 10</div>
      <h1 className="scr__title">How do you like to network?</h1>
      <p className="scr__sub">Quality vs. quantity. Move the slider — no wrong answer.</p>
      <div style={{ marginTop: 8 }}>
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            marginBottom: 16,
            fontSize: 12,
            color: 'var(--fg-muted)',
          }}
        >
          <div style={{ textAlign: 'center', maxWidth: 80 }}>
            <div style={{ fontSize: 28, marginBottom: 4 }}>🪴</div>Deep, fewer
          </div>
          <div style={{ textAlign: 'center', maxWidth: 80 }}>
            <div style={{ fontSize: 28, marginBottom: 4 }}>🎉</div>Big rooms
          </div>
        </div>
        <Slider value={value} onChange={onChange} />
        <div
          style={{
            marginTop: 24,
            padding: 16,
            borderRadius: 14,
            background: 'var(--bg-surface)',
            border: '1px solid var(--border-subtle)',
          }}
        >
          <div
            style={{
              fontSize: 11,
              fontWeight: 700,
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
              color: 'var(--fg-muted)',
              marginBottom: 8,
            }}
          >
            Your style
          </div>
          <div
            style={{
              fontFamily: 'var(--font-display)',
              fontWeight: 700,
              fontSize: 18,
              lineHeight: 1.2,
              marginBottom: 6,
            }}
          >
            {styleTitle}
          </div>
          <div style={{ fontSize: 13, color: 'var(--fg-muted)', lineHeight: 1.5 }}>{styleDesc}</div>
        </div>
      </div>
      <div className="scr__cta">
        <button className="btn-primary" onClick={onNext}>
          Continue
        </button>
      </div>
    </div>
  )
}

function MustHavesStep({
  value,
  onChange,
  onBack,
  onSkip,
  onNext,
}: {
  value: string[]
  onChange: (v: string[]) => void
  onBack: () => void
  onSkip: () => void
  onNext: () => void
}) {
  const [tab, setTab] = useState<'people' | 'companies' | 'speakers'>('people')
  const [query, setQuery] = useState('')
  const add = (id: string) => {
    if (!value.includes(id)) onChange([...value, id])
  }
  const remove = (id: string) => onChange(value.filter((x) => x !== id))
  const idToLabel = (id: string) => {
    const s = SUGGESTIONS.find((x) => x.id === id)
    return s ? s.name : id
  }
  const idToGrad = (id: string) => SUGGESTIONS.find((x) => x.id === id)?.grad
  const visibleSuggestions = SUGGESTIONS.filter(
    (s) =>
      s.kind === tab &&
      !value.includes(s.id) &&
      (s.name.toLowerCase().includes(query.toLowerCase()) ||
        s.role.toLowerCase().includes(query.toLowerCase())),
  )
  return (
    <div className="scr">
      <Header step={10} total={TOTAL_STEPS} onBack={onBack} onSkip={onSkip} />
      <div className="scr__step-label">Step 10 of 10</div>
      <h1 className="scr__title">Anyone you want to meet?</h1>
      <p className="scr__sub">
        People, speakers, or companies. We'll keep an eye out and ping you when paths cross.
      </p>
      <div className="seg-tabs">
        {(['people', 'companies', 'speakers'] as const).map((t) => (
          <button key={t} className={tab === t ? 'on' : ''} onClick={() => setTab(t)}>
            {t[0].toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>
      <div className="search">
        <Search />
        <input
          placeholder={`Search ${tab}…`}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>
      {value.length > 0 && (
        <div className="pill-list">
          {value.map((v) => (
            <span key={v} className="pill">
              <span className="av" style={idToGrad(v) ? { background: idToGrad(v) } : undefined} />
              {idToLabel(v)}
              <button className="x" onClick={() => remove(v)}>
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      )}
      <div
        style={{
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: '0.06em',
          textTransform: 'uppercase',
          color: 'var(--fg-subtle)',
          margin: '8px 0',
        }}
      >
        Suggested
      </div>
      <div className="suggest-list">
        {visibleSuggestions.map((s) => (
          <button key={s.id} className="suggest" onClick={() => add(s.id)}>
            <div className="suggest__av" style={s.grad ? { background: s.grad } : undefined}>
              {s.initials}
            </div>
            <div className="suggest__body">
              <div className="suggest__name">{s.name}</div>
              <div className="suggest__role">{s.role}</div>
            </div>
            <div className="suggest__add">
              <Plus />
            </div>
          </button>
        ))}
      </div>
      <div className="scr__cta">
        <button className="btn-primary" onClick={onNext}>
          Build my schedule
        </button>
        <button className="btn-tertiary" onClick={onSkip}>
          Skip — no specific names
        </button>
      </div>
    </div>
  )
}

function ReviewStep({
  state,
  conf,
  onBack,
  onEdit,
  onNext,
}: {
  state: OnboardingState
  conf: Conference
  onBack: () => void
  onEdit: (step: number) => void
  onNext: () => void
}) {
  const dayLabels = conf.days.filter((d) => state.days.includes(d.num)).map((d) => d.dow)
  const daysVal =
    state.attendance === 'full' ? 'All days' : dayLabels.length ? dayLabels.join(' & ') : '—'
  const role = state.role ? ROLES.find((r) => r.id === state.role)!.label : '—'
  const goals = state.goals
    .map((g) => GOALS_BY_ROLE[state.role || 'founder'].find((x) => x.id === g)?.label)
    .filter(Boolean)
    .join(', ')
  const paceLabel =
    state.pace < 25
      ? 'Relaxed'
      : state.pace < 55
      ? 'Selective'
      : state.pace < 80
      ? 'Balanced'
      : 'Packed'
  const energyLabel = state.energy < 35 ? 'Mornings' : state.energy < 70 ? 'Mid-day' : 'Nights'
  const socialLabel = state.social < 35 ? 'Quality' : state.social < 70 ? 'Mixed' : 'Big rooms'

  const rows = [
    { Icon: Calendar, label: 'Conference · Days', val: `${conf.name} · ${daysVal}`, step: 1 },
    { Icon: UserIcon, label: 'You', val: `${role}${goals ? ` · ${goals}` : ''}`, step: 4 },
    { Icon: Compass, label: 'Topics', val: state.topics.length ? state.topics.join(', ') : '—', step: 6 },
    { Icon: Clock, label: 'Style', val: `${paceLabel} · ${energyLabel} · ${socialLabel}`, step: 7 },
    {
      Icon: Star,
      label: 'Must-haves',
      val: state.mustHaves.length
        ? `${state.mustHaves.length} ${state.mustHaves.length === 1 ? 'person' : 'people'}`
        : '—',
      step: 9,
    },
  ]

  return (
    <div className="scr">
      <Header step={10} total={TOTAL_STEPS} onBack={onBack} hideSkip />
      <div className="scr__step-label">Last step</div>
      <h1 className="scr__title">Look good?</h1>
      <p className="scr__sub">Tap any line to tweak before we build your plan.</p>
      <div className="review-list">
        {rows.map((r) => (
          <button key={r.label} className="review-row" onClick={() => onEdit(r.step)}>
            <div className="review-row__icon">
              <r.Icon />
            </div>
            <div className="review-row__body">
              <div className="review-row__label">{r.label}</div>
              <div className="review-row__val">{r.val}</div>
            </div>
            <span className="review-row__edit">Edit</span>
          </button>
        ))}
      </div>
      <div className="scr__cta">
        <button className="btn-primary" onClick={onNext}>
          Curate my schedule
        </button>
      </div>
    </div>
  )
}

function Curating({ role }: { role: Role | null }) {
  const [phase, setPhase] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setPhase((p) => Math.min(3, p + 1)), 800)
    return () => clearInterval(t)
  }, [])
  const steps = ['Pulled 384 events', 'Matched to your goals', 'Routing your days', 'Picking people to meet']
  const roleLabel = role ? ROLES.find((r) => r.id === role)!.label.toLowerCase() : 'professional'
  return (
    <div className="scr-load">
      <div className="scr-load__mark">
        <SQMark size={96} />
      </div>
      <div className="scr-load__title">Building your schedule</div>
      <div className="scr-load__sub">
        A few seconds. We're picking the right talks for a {roleLabel}.
      </div>
      <ul className="scr-load__steps">
        {steps.map((s, i) => (
          <li key={s} className={`scr-load__step ${i < phase ? 'done' : i === phase ? 'cur' : ''}`}>
            <span className="dot" />
            {s}
          </li>
        ))}
      </ul>
    </div>
  )
}

function Done({
  conf,
  onRestart,
  onEnter,
}: {
  conf: Conference
  onRestart: () => void
  onEnter: () => void
}) {
  return (
    <div className="scr" style={{ paddingTop: 56 }}>
      <div style={{ marginBottom: 24 }}>
        <SQMark size={56} />
      </div>
      <h1
        className="scr-done__title"
        style={{
          fontFamily: 'var(--font-display)',
          fontWeight: 700,
          fontSize: 30,
          lineHeight: 1.1,
          letterSpacing: '-0.02em',
          marginBottom: 14,
        }}
      >
        Your <em>{conf.name.split(' ')[0]}</em> is ready.
      </h1>
      <p className="scr__sub" style={{ marginBottom: 24 }}>
        21 events across 2 days. 6 people on watch. Tweak anytime.
      </p>
      <div className="scr-stats">
        <div className="scr-stat">
          <div className="scr-stat__num">21</div>
          <div className="scr-stat__lbl">events curated</div>
        </div>
        <div className="scr-stat">
          <div className="scr-stat__num">6</div>
          <div className="scr-stat__lbl">people on watch</div>
        </div>
        <div className="scr-next">
          <div className="scr-next__lbl">Up next</div>
          <div className="scr-next__title">Stable Summit IV — 9:00 AM</div>
          <div className="scr-next__sub">Sheraton · 3 founders to meet</div>
        </div>
      </div>
      <div className="scr__cta">
        <button className="btn-primary" onClick={onEnter}>
          See my schedule
        </button>
        <button className="btn-tertiary" onClick={onRestart}>
          Restart onboarding
        </button>
      </div>
    </div>
  )
}
