export type Role =
  | 'founder'
  | 'investor'
  | 'developer'
  | 'marketer'
  | 'corporate'
  | 'journalist'

export type Attendance = 'full' | 'partial' | 'side-only'

export type OnboardingState = {
  conferenceId: string
  attendance: Attendance | null
  days: number[]
  role: Role | null
  goals: string[]
  topics: string[]
  pace: number
  energy: number
  social: number
  mustHaves: string[]
}

export type Event = {
  id: string
  conference_id: string
  title: string
  description?: string
  start: string
  end: string
  venue?: string
  tags: string[]
  url?: string | null
  capacity?: number | null
  attendees?: number | null
  cover_url?: string | null
  tint_color?: string | null
  match?: number
  inSchedule?: boolean
}

export type CuratedItem = {
  event_id: string
  day: string
  start: string
  end: string
  rationale: string
  priority: 'must' | 'should' | 'maybe'
}

export type ChatMessage = {
  role: 'user' | 'assistant' | 'system'
  content: string
}
