import { useEffect, useRef, useState } from 'react'
import { Send, Sparkles, User } from 'lucide-react'
import { PageContainer } from '../components/layout/PageContainer'
import { ChatChart } from '../components/assistant/ChatChart'
import { askAssistant, type AssistantChart, type AssistantScope } from '../services/api'

interface Message {
  role: 'user' | 'assistant'
  text: string
  sources?: string[]
  chart?: AssistantChart | null
}

/** Which application service each answer was built from. Shown under the reply
 *  so the figures are traceable to a page the user can go and check. */
const TOOL_LABELS: Record<string, string> = {
  forecast: 'Demand forecast',
  labor_cost: 'Cost & budget',
  punch: 'Punch & attendance',
  risk: 'Employee risk',
  recommendations: 'Recommendations',
  model_performance: 'Model performance',
  data_quality: 'Data quality',
  drift: 'Drift monitoring',
}

/** How many prior turns to send back for context. Enough for a follow-up to
 *  resolve, capped so a long session does not grow the request without bound. */
const HISTORY_TURNS = 8

const SUGGESTIONS = [
  'How should I staff tomorrow?',
  'Which location needs immediate attention?',
  'How can I reduce overtime this week?',
  'Show me a chart of the demand trend for the cafe',
  'How is our data quality?',
]

function hasScope(scope: AssistantScope | null): boolean {
  return !!scope && (!!scope.location_id || !!scope.department_id)
}

function scopeLabel(scope: AssistantScope): string {
  return [scope.location_name, scope.department_name].filter(Boolean).join(' · ')
}

/** Renders **bold** spans. Deliberately tiny - the assistant is instructed to
 *  emit only bold and `- ` bullets, so a markdown dependency would be overkill. */
function Bold({ text }: { text: string }) {
  return (
    <>
      {text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
        part.startsWith('**') && part.endsWith('**')
          ? <strong key={i} className="font-semibold text-[var(--color-text)]">{part.slice(2, -2)}</strong>
          : <span key={i}>{part}</span>,
      )}
    </>
  )
}

/** Turns the assistant's lead sentence + bullet lines into real markup, so a
 *  structured answer reads as a structured answer instead of one long blob. */
function Answer({ text }: { text: string }) {
  const blocks: React.ReactNode[] = []
  let bullets: string[] = []

  const flush = () => {
    if (!bullets.length) return
    blocks.push(
      <ul key={`ul-${blocks.length}`} className="my-1.5 flex flex-col gap-1">
        {bullets.map((b, i) => (
          <li key={i} className="flex gap-1.5">
            <span className="mt-[5px] h-1 w-1 shrink-0 rounded-full bg-[var(--color-primary)]" />
            <span><Bold text={b} /></span>
          </li>
        ))}
      </ul>,
    )
    bullets = []
  }

  for (const raw of text.split('\n')) {
    const line = raw.trim()
    if (!line) { flush(); continue }
    if (line.startsWith('- ') || line.startsWith('* ')) {
      bullets.push(line.slice(2).trim())
      continue
    }
    flush()
    blocks.push(<p key={`p-${blocks.length}`} className="my-0.5"><Bold text={line} /></p>)
  }
  flush()
  return <div>{blocks}</div>
}

export default function AIAssistant() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  // What site/department the conversation is currently about, as resolved by
  // the backend each turn - round-tripped on the next request so a follow-up
  // ("what about overtime there?") keeps working past a single hop.
  const [scope, setScope] = useState<AssistantScope | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, sending])

  const send = async (question: string) => {
    if (!question.trim() || sending) return
    // Captured before the new turn is appended - the backend expects the prior
    // turns only, with the question passed separately.
    const history = messages.slice(-HISTORY_TURNS).map((m) => ({ role: m.role, content: m.text }))
    setMessages((m) => [...m, { role: 'user', text: question }])
    setInput('')
    setSending(true)
    try {
      const result = await askAssistant(question, history, scope)
      setScope(result.scope ?? null)
      setMessages((m) => [...m, { role: 'assistant', text: result.answer, sources: result.tools_used, chart: result.chart }])
    } catch (err: any) {
      setMessages((m) => [...m, { role: 'assistant', text: `Sorry - something went wrong: ${err?.response?.data?.detail ?? String(err)}` }])
    } finally {
      setSending(false)
    }
  }

  const isEmpty = messages.length === 0

  return (
    <PageContainer title="AI Assistant" fill>
      <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] shadow-[var(--shadow-sm)]">
        <div className="flex-1 overflow-y-auto p-6">
          {isEmpty ? (
            <div className="flex h-full flex-col items-center justify-center gap-4 py-10 text-center">
              <div className="flex h-12 w-12 items-center justify-center rounded-full bg-[var(--color-primary)]/10 text-[var(--color-primary)]">
                <Sparkles size={22} />
              </div>
              <div>
                <div className="text-sm font-semibold text-[var(--color-text)]">How can I help?</div>
                <div className="mt-1 max-w-sm text-xs text-[var(--color-text-muted)]">
                  Ask about labor demand forecasts, staffing gaps, cost and budget, punch issues, or recommendations.
                </div>
              </div>
              <div className="mt-2 flex flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => send(s)}
                    className="rounded-full border border-[var(--color-border)] bg-[var(--color-surface-2)] px-3 py-1.5 text-xs text-[var(--color-text-muted)] transition-colors hover:border-[var(--color-primary)] hover:text-[var(--color-text)]"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="flex flex-col gap-5">
              {messages.map((m, i) => (
                <div key={i} className={`flex gap-3 ${m.role === 'user' ? 'flex-row-reverse' : ''}`}>
                  <div
                    className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${
                      m.role === 'user'
                        ? 'bg-[var(--color-surface-2)] text-[var(--color-text-muted)]'
                        : 'bg-[var(--color-primary)]/10 text-[var(--color-primary)]'
                    }`}
                  >
                    {m.role === 'user' ? <User size={14} /> : <Sparkles size={14} />}
                  </div>
                  <div
                    className={`rounded-2xl px-4 py-2.5 text-xs leading-relaxed ${m.chart ? 'max-w-[92%]' : 'max-w-[78%]'} ${
                      m.role === 'user'
                        ? 'rounded-tr-sm bg-[var(--color-primary)] text-white'
                        : 'rounded-tl-sm border border-[var(--color-border)] bg-[var(--color-surface-2)] text-[var(--color-text)]'
                    }`}
                  >
                    {m.role === 'user' ? (
                      <p className="whitespace-pre-wrap">{m.text}</p>
                    ) : (
                      <>
                        <Answer text={m.text} />
                        {m.chart && <ChatChart chart={m.chart} />}
                        {!!m.sources?.length && (
                          <div className="mt-2 flex flex-wrap items-center gap-1.5 border-t border-[var(--color-border)] pt-2">
                            <span className="text-[9px] uppercase tracking-[0.06em] text-[var(--color-text-muted)]">Sources</span>
                            {m.sources.map((s) => (
                              <span
                                key={s}
                                className="rounded-full border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-0.5 text-[9px] text-[var(--color-text-muted)]"
                              >
                                {TOOL_LABELS[s] ?? s}
                              </span>
                            ))}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                </div>
              ))}
              {sending && (
                <div className="flex gap-3">
                  <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[var(--color-primary)]/10 text-[var(--color-primary)]">
                    <Sparkles size={14} />
                  </div>
                  <div className="flex items-center gap-1.5 rounded-2xl rounded-tl-sm border border-[var(--color-border)] bg-[var(--color-surface-2)] px-4 py-3">
                    {[0, 1, 2].map((d) => (
                      <span
                        key={d}
                        className="h-1.5 w-1.5 rounded-full bg-[var(--color-text-muted)]"
                        style={{ animation: 'typing-bounce 1.2s ease-in-out infinite', animationDelay: `${d * 0.15}s` }}
                      />
                    ))}
                  </div>
                </div>
              )}
              <div ref={bottomRef} />
            </div>
          )}
        </div>

        <div className="border-t border-[var(--color-border)] bg-[var(--color-surface)] p-4">
          {hasScope(scope) && (
            <div className="mb-2 flex items-center gap-1.5">
              <span className="text-[10px] text-[var(--color-text-muted)]">Discussing:</span>
              <button
                onClick={() => setScope(null)}
                className="rounded-full border border-[var(--color-primary)] bg-[var(--color-primary)]/10 px-2 py-0.5 text-[10px] font-medium text-[var(--color-primary)]"
                title="Clear this scope"
              >
                {scopeLabel(scope!)} ✕
              </button>
            </div>
          )}
          <div className="flex items-end gap-2 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-2)] p-2 focus-within:border-[var(--color-primary)]">
            <textarea
              value={input}
              rows={1}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  send(input)
                }
              }}
              placeholder="Ask about staffing, forecasts, cost, or recommendations…"
              className="max-h-32 flex-1 resize-none bg-transparent px-2 py-1.5 text-xs text-[var(--color-text)] outline-none placeholder:text-[var(--color-text-muted)]"
            />
            <button
              onClick={() => send(input)}
              disabled={sending || !input.trim()}
              aria-label="Send message"
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[var(--color-primary)] text-white transition-colors hover:bg-[var(--color-primary-dark)] disabled:opacity-40"
            >
              <Send size={14} />
            </button>
          </div>
          <div className="mt-1.5 px-1 text-[10px] text-[var(--color-text-muted)]">
            Answers are grounded in this application's own data. Follow-up questions keep the context of the
            previous answer. Press Enter to send, Shift+Enter for a new line.
          </div>
        </div>
      </div>
    </PageContainer>
  )
}
