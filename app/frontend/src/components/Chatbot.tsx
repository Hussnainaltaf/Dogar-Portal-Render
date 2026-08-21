import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '@/lib/api'
import { printInFrame } from '@/lib/print'

/* ------------------------------------------------------------------ *
 * DtcBot — production-level floating assistant.
 * Faithful React port of the #dtcbot widget in v2 base.html
 * (markup 1162-1211, JS 1213-1600). Handles every server response
 * type: flow / ai / text / guide / help_menu / results / ocr_result /
 * action — including cards, chips, options, links, quick replies,
 * passport image upload, flow_state round-tripping, in-app navigation
 * and document printing.
 * ------------------------------------------------------------------ */

type Chip = { label: string; value: string }
type CardRow = [string, string]
type Card = { kind?: string; title?: string; subtitle?: string; rows?: CardRow[]; href?: string }
type LinkT = { label: string; href: string }
type ResultItem = { title?: string; subtitle?: string; href?: string; kind?: string; rows?: CardRow[] }

interface FlowState { flow: string | null; step: string | null; slots: Record<string, unknown> }

// The flow engine returns `print` as an object {tpl_id, record_id, title};
// legacy rule-brain responses may still send a plain URL string.
type PrintPayload = string | { tpl_id?: number | string; record_id?: number | string; title?: string }

// Build the print-preview URL for a template + record id.
function printUrl(tplId: number | string, recordId?: number | string): string {
  const rec = recordId != null && recordId !== '' ? `?record_id=${encodeURIComponent(String(recordId))}` : ''
  return `/api/documents/templates/${tplId}/print${rec}`
}

// Normalize any print payload (object or "print:<tpl>:<rec>" string or URL) → URL.
function resolvePrint(p: PrintPayload): string | null {
  if (!p) return null
  if (typeof p === 'object') {
    if (p.tpl_id == null) return null
    return printUrl(p.tpl_id, p.record_id)
  }
  // string forms
  if (p.startsWith('print:')) {
    const parts = p.split(':')          // print:<tpl>:<rec>
    if (parts.length >= 2) return printUrl(parts[1], parts[2])
    return null
  }
  return p // already a URL
}

interface BotResponse {
  type?: string
  greeting?: string
  text?: string
  reply?: string
  card?: Card
  cards?: Card[]
  chips?: Chip[]
  options?: Chip[]
  links?: LinkT[]
  quick_replies?: (string | Chip)[]
  results?: ResultItem[]
  fields?: Record<string, unknown>
  flow_state?: FlowState
  done?: boolean
  print?: PrintPayload
  open?: string
  url?: string
  navigate?: boolean
  verb?: string
  method?: string
  tools_used?: string[]
  candidate_id?: number
  // help_menu
  items?: { label: string; value?: string; href?: string }[]
}

interface HistoryTurn { role: 'user' | 'assistant'; content: string }

// A rendered message in the transcript.
interface Msg {
  id: number
  role: 'bot' | 'user'
  text?: string
  greeting?: string
  card?: Card
  cards?: Card[]
  chips?: Chip[]
  options?: Chip[]
  links?: LinkT[]
  quickReplies?: Chip[]
  results?: ResultItem[]
  fields?: Record<string, unknown>
  verb?: string
  toolsUsed?: string[]
  recordUrl?: string
  image?: string // user-uploaded image preview (data url)
}

const LOGO = '/static/img/logo-sm.png'
const EMPTY_FLOW: FlowState = { flow: null, step: null, slots: {} }

let _seq = 1
const nextId = () => _seq++

// Icon for a card by its kind — mirrors v2 cardHtml().
function cardIcon(kind?: string): string {
  switch (kind) {
    case 'demand': return 'fa-folder-open'
    case 'candidate': return 'fa-id-card'
    case 'client': return 'fa-building'
    case 'document': return 'fa-file-lines'
    default: return 'fa-circle-info'
  }
}

// Light markdown → safe HTML. **bold**, `code`, newlines.
function fmtText(t: string): string {
  const esc = t
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
  return esc
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code class="px-1 py-0.5 bg-slate-100 rounded text-[12px]">$1</code>')
    .replace(/\n/g, '<br>')
}

// Normalize quick_replies (can be strings or {label,value}) into chips.
function normReplies(qr?: (string | Chip)[]): Chip[] {
  if (!qr) return []
  return qr.map((q) => (typeof q === 'string' ? { label: q, value: q } : q))
}

export default function Chatbot() {
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [typingLabel, setTypingLabel] = useState<string | null>(null)
  const [welcomed, setWelcomed] = useState(false)
  const flowRef = useRef<FlowState>({ ...EMPTY_FLOW })
  const historyRef = useRef<HistoryTurn[]>([])
  const scrollRef = useRef<HTMLDivElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const pushBot = useCallback((m: Omit<Msg, 'id' | 'role'>) => {
    setMessages((prev) => [...prev, { id: nextId(), role: 'bot', ...m }])
  }, [])

  const pushUser = useCallback((text: string, image?: string) => {
    setMessages((prev) => [...prev, { id: nextId(), role: 'user', text, image }])
  }, [])

  // Translate a raw server response into a transcript message + side effects.
  const renderResponse = useCallback((resp: BotResponse) => {
    // Round-trip flow state.
    if (resp.flow_state) flowRef.current = resp.flow_state
    if (resp.done) flowRef.current = { ...EMPTY_FLOW }

    const type = resp.type || 'text'
    const text = resp.text || resp.reply || ''

    const msg: Omit<Msg, 'id' | 'role'> = {
      greeting: resp.greeting,
      text,
      card: resp.card,
      cards: resp.cards,
      chips: resp.chips,
      options: resp.options,
      links: resp.links,
      quickReplies: normReplies(resp.quick_replies),
      results: resp.results,
    }

    if (type === 'action') {
      msg.verb = resp.verb
      msg.fields = resp.fields
      msg.recordUrl = resp.url
    }
    if (type === 'ocr_result') {
      msg.fields = resp.fields
    }
    if (type === 'ai') {
      msg.toolsUsed = resp.tools_used
    }
    if (type === 'help_menu' && resp.items) {
      msg.chips = resp.items.map((it) => ({ label: it.label, value: it.value || (it.href ? `open:${it.href}` : it.label) }))
    }

    pushBot(msg)

    // Side effects: print + auto navigation.
    if (resp.print) {
      const u = resolvePrint(resp.print)
      if (u) printInFrame(u)
    }
    if (resp.open) {
      navigate(resp.open)
    }
    if (type === 'action' && resp.navigate && resp.url) {
      // give the user a beat to read the confirmation, then navigate.
      setTimeout(() => navigate(resp.url!), 900)
    }
  }, [navigate, pushBot])

  const loadWelcome = useCallback(async () => {
    setTypingLabel('DtcBot')
    try {
      const { data } = await api.get<BotResponse>('/chatbot/welcome')
      renderResponse(data)
    } catch {
      pushBot({ text: "Hi! I'm DtcBot. How can I help?", chips: [
        { label: 'Add new demand', value: 'start:add_demand' },
        { label: 'Add new client', value: 'start:add_client' },
        { label: 'Find demand by file no.', value: 'start:find_demand' },
        { label: 'Find candidate by passport', value: 'start:find_candidate' },
        { label: 'Print a document', value: 'start:print_doc' },
      ] })
    } finally {
      setTypingLabel(null)
    }
  }, [renderResponse, pushBot])

  useEffect(() => {
    if (open && !welcomed) {
      setWelcomed(true)
      void loadWelcome()
    }
  }, [open, welcomed, loadWelcome])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, typingLabel])

  // v13 — mobile presentation flag. On phones the panel becomes a full-screen
  // sheet (see the "DTC BOT" section in index.css); `body.dtc-open` lets that
  // stylesheet lock page scroll behind the sheet and hide the FAB so it cannot
  // float over the composer. Purely presentational — no bot logic depends on
  // it — and the class is always removed on unmount.
  useEffect(() => {
    const cls = document.body.classList
    if (open) cls.add('dtc-open')
    else cls.remove('dtc-open')
    return () => cls.remove('dtc-open')
  }, [open])

  // Escape closes the assistant. Matches the rest of the app's overlays and
  // gives keyboard users a way out of the full-screen mobile sheet.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open])

  // Send a text message (or chip value) to the bot.
  const send = useCallback(async (raw: string, displayLabel?: string) => {
    const text = raw.trim()
    if (!text || busy) return

    // Handle pure client-side chip verbs.
    if (text === 'start:home') {
      flowRef.current = { ...EMPTY_FLOW }
    }
    if (text.startsWith('open:')) {
      const dest = text.slice(5)
      pushUser(displayLabel || dest)
      navigate(dest)
      return
    }

    pushUser(displayLabel || text)
    historyRef.current.push({ role: 'user', content: text })
    setInput('')
    setBusy(true)
    setTypingLabel('DtcBot')
    try {
      const { data } = await api.post<BotResponse>('/chatbot/message', {
        message: text,
        history: historyRef.current.slice(-16),
        flow_state: flowRef.current,
      })
      renderResponse(data)
      const replyText = data.text || data.reply || ''
      if (replyText) historyRef.current.push({ role: 'assistant', content: replyText })
    } catch {
      pushBot({ text: 'Sorry, I had trouble responding. Please try again.' })
    } finally {
      setBusy(false)
      setTypingLabel(null)
    }
  }, [busy, navigate, pushBot, pushUser, renderResponse])

  const onChip = useCallback((c: Chip) => {
    void send(c.value, c.label)
  }, [send])

  // Image / passport upload → OCR auto-create.
  const onUpload = useCallback(async (file: File) => {
    if (busy) return
    const previewUrl = URL.createObjectURL(file)
    pushUser(`📎 ${file.name}`, previewUrl)
    setBusy(true)
    setTypingLabel('Reading document')
    try {
      const fd = new FormData()
      fd.append('file', file)
      const { data } = await api.post<BotResponse>('/chatbot/upload?auto_create=true', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      renderResponse(data)
    } catch {
      pushBot({ text: "I couldn't read that document. Please try a clearer photo of the passport's data page." })
    } finally {
      setBusy(false)
      setTypingLabel(null)
    }
  }, [busy, pushBot, pushUser, renderResponse])

  const reset = useCallback(() => {
    flowRef.current = { ...EMPTY_FLOW }
    historyRef.current = []
    setMessages([])
    void loadWelcome()
  }, [loadWelcome])

  return (
    <div id="dtcbot-root">
      {/* Panel */}
      {open && (
        <section
          id="dtcbot-panel"
          role="dialog"
          aria-modal="false"
          aria-label="DtcBot assistant"
          className="fixed bottom-24 right-6 z-[80] w-[400px] max-w-[calc(100vw-2rem)] h-[600px] max-h-[80vh] bg-white border border-slate-200 rounded-2xl shadow-2xl flex flex-col overflow-hidden"
        >
          {/* Header — `dtc-head` carries the brand gradient (theme-aware) plus
              the safe-area top padding used by the phone sheet. */}
          <header className="dtc-head px-3 sm:px-4 py-3 text-white flex items-center gap-2.5">
            <span className="w-9 h-9 rounded-full bg-white flex items-center justify-center shrink-0 overflow-hidden">
              <img src={LOGO} alt="" className="w-7 h-7 object-contain" />
            </span>
            <div className="leading-tight flex-1 min-w-0">
              <div className="font-bold text-sm truncate">DtcBot Assistant</div>
              <div className="text-[11px] text-white/80 flex items-center gap-1.5 truncate">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 inline-block shrink-0" />
                Online · AI by HussnainTechVertex
              </div>
            </div>
            <button type="button" onClick={reset} title="Reset conversation" aria-label="Reset conversation"
              className="w-9 h-9 sm:w-8 sm:h-8 rounded-full hover:bg-white/20 active:bg-white/30 flex items-center justify-center shrink-0 transition">
              <i className="fa-solid fa-rotate-left text-xs" />
            </button>
            <button type="button" onClick={() => setOpen(false)} title="Close" aria-label="Close assistant"
              className="w-9 h-9 sm:w-8 sm:h-8 rounded-full hover:bg-white/20 active:bg-white/30 flex items-center justify-center shrink-0 transition">
              <i className="fa-solid fa-xmark" />
            </button>
          </header>

          {/* Messages */}
          <div ref={scrollRef} id="dtcbot-msgs" className="dtc-scroll flex-1 overflow-y-auto p-3 space-y-3 bg-slate-50" role="log" aria-live="polite" aria-label="Conversation">
            {messages.map((m) => (m.role === 'user'
              ? <UserBubble key={m.id} m={m} />
              : <BotBubble key={m.id} m={m} onChip={onChip} onPrint={(u) => printInFrame(u)} onOpen={(u) => navigate(u)} />
            ))}
            {typingLabel && (
              <div className="flex items-end gap-2" role="status">
                <span className="w-7 h-7 rounded-full bg-white border border-slate-200 flex items-center justify-center shrink-0 overflow-hidden">
                  <img src={LOGO} alt="" className="w-5 h-5 object-contain" />
                </span>
                <div className="bg-white border border-slate-200 px-3 py-2 rounded-2xl rounded-bl-sm text-slate-400 text-[13px]">
                  <i className="fa-solid fa-spinner fa-spin mr-1.5" />{typingLabel}…
                </div>
              </div>
            )}
          </div>

          {/* Composer */}
          <form
            id="dtcbot-form"
            onSubmit={(e) => { e.preventDefault(); void send(input) }}
            className="dtc-composer p-3 border-t border-slate-200 bg-white flex items-center gap-2"
          >
            <input
              ref={fileRef}
              type="file"
              accept="image/*,.pdf"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f) void onUpload(f)
                e.target.value = ''
              }}
            />
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              disabled={busy}
              title="Upload passport / document"
              aria-label="Upload passport or document"
              className="w-10 h-10 sm:w-9 sm:h-9 rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50 disabled:opacity-50 flex items-center justify-center shrink-0 transition"
            >
              <i className="fa-solid fa-paperclip text-sm" />
            </button>
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask DtcBot…"
              aria-label="Message DtcBot"
              autoComplete="off"
              className="flex-1 min-w-0 px-3 py-2 text-sm border border-slate-200 rounded-lg focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
            />
            <button
              type="submit"
              disabled={busy || !input.trim()}
              aria-label="Send message"
              className="w-10 h-10 sm:w-9 sm:h-9 rounded-lg brand-gradient text-white hover:opacity-95 disabled:opacity-50 flex items-center justify-center shrink-0 transition"
            >
              <i className="fa-solid fa-paper-plane text-sm" />
            </button>
          </form>
        </section>
      )}

      {/* FAB */}
      <button
        id="dtcbot-fab"
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="fixed bottom-6 right-6 z-[80] w-14 h-14 rounded-full bg-white border border-slate-200 hover:shadow-xl shadow-lg flex items-center justify-center transition group"
        title="DtcBot Assistant"
        aria-label={open ? 'Close DtcBot assistant' : 'Open DtcBot assistant'}
        aria-expanded={open}
        aria-controls="dtcbot-panel"
      >
        {open
          ? <i className="fa-solid fa-xmark text-xl text-slate-600" />
          : <>
              <img src={LOGO} alt="DtcBot" className="w-9 h-9 object-contain" />
              <span className="absolute top-1 right-1 w-3 h-3 rounded-full bg-emerald-500 border-2 border-white" />
            </>}
      </button>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* Sub-components                                                      */
/* ------------------------------------------------------------------ */

function UserBubble({ m }: { m: Msg }) {
  return (
    <div className="flex justify-end">
      <div className="dtc-msg max-w-[80%] bg-blue-600 text-white px-3 py-2 rounded-2xl rounded-br-sm text-[13px] leading-relaxed break-words">
        {m.image && <img src={m.image} alt="" className="rounded-lg mb-1.5 max-h-40 object-cover" />}
        {m.text}
      </div>
    </div>
  )
}

function verbStyle(verb?: string): { bg: string; icon: string; label: string } {
  switch (verb) {
    case 'created': return { bg: 'bg-emerald-50 border-emerald-200 text-emerald-800', icon: 'fa-circle-plus text-emerald-600', label: 'Created' }
    case 'updated': return { bg: 'bg-blue-50 border-blue-200 text-blue-800', icon: 'fa-pen-to-square text-blue-600', label: 'Updated' }
    case 'assigned': return { bg: 'bg-violet-50 border-violet-200 text-violet-800', icon: 'fa-user-check text-violet-600', label: 'Assigned' }
    case 'done': return { bg: 'bg-emerald-50 border-emerald-200 text-emerald-800', icon: 'fa-circle-check text-emerald-600', label: 'Done' }
    default: return { bg: 'bg-slate-50 border-slate-200 text-slate-700', icon: 'fa-circle-info text-slate-500', label: 'Info' }
  }
}

function CardView({ card, onOpen }: { card: Card; onOpen: (u: string) => void }) {
  return (
    <div className="mt-2 border border-slate-200 rounded-xl overflow-hidden bg-white">
      <div className="px-3 py-2 bg-slate-50 border-b border-slate-200 flex items-center gap-2">
        <i className={`fa-solid ${cardIcon(card.kind)} text-blue-600`} />
        <div className="font-semibold text-[13px] text-slate-800 flex-1 min-w-0 truncate">{card.title}</div>
      </div>
      {card.subtitle && <div className="px-3 pt-2 text-[12px] text-slate-500">{card.subtitle}</div>}
      {!!card.rows?.length && (
        <table className="w-full text-[12px]">
          <tbody>
            {card.rows.map(([k, v], i) => (
              <tr key={i} className="border-b border-slate-100 last:border-0">
                <td className="px-3 py-1.5 text-slate-500 align-top whitespace-nowrap w-28">{k}</td>
                <td className="px-3 py-1.5 text-slate-800 font-medium break-words">{v}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {card.href && (
        <div className="px-3 py-2 border-t border-slate-100">
          <button onClick={() => onOpen(card.href!)} className="text-[12px] font-semibold text-blue-600 hover:text-blue-700">
            Open <i className="fa-solid fa-arrow-right ml-1 text-[10px]" />
          </button>
        </div>
      )}
    </div>
  )
}

function FieldGrid({ fields }: { fields: Record<string, unknown> }) {
  const entries = Object.entries(fields).filter(([, v]) => v !== null && v !== undefined && v !== '')
  if (!entries.length) return null
  return (
    <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1.5 text-[12px] bg-slate-50 border border-slate-200 rounded-xl p-2.5">
      {entries.map(([k, v]) => (
        <div key={k} className="min-w-0">
          <div className="text-[10px] uppercase tracking-wide text-slate-400">{k.replace(/_/g, ' ')}</div>
          <div className="text-slate-800 font-medium truncate">{String(v)}</div>
        </div>
      ))}
    </div>
  )
}

function BotBubble({ m, onChip, onPrint, onOpen }: {
  m: Msg
  onChip: (c: Chip) => void
  onPrint: (u: string) => void
  onOpen: (u: string) => void
}) {
  const vs = m.verb ? verbStyle(m.verb) : null
  return (
    <div className="flex items-start gap-2">
      <span className="w-7 h-7 rounded-full bg-white border border-slate-200 flex items-center justify-center shrink-0 overflow-hidden mt-0.5">
        <img src={LOGO} alt="" className="w-5 h-5 object-contain" />
      </span>
      <div className="dtc-msg max-w-[85%] flex-1 min-w-0">
        {/* Action verb banner */}
        {vs && (
          <div className={`mb-2 px-3 py-1.5 rounded-lg border text-[12px] font-semibold inline-flex items-center gap-2 ${vs.bg}`}>
            <i className={`fa-solid ${vs.icon}`} /> {vs.label}
          </div>
        )}

        <div className="bg-white border border-slate-200 text-slate-700 px-3 py-2 rounded-2xl rounded-bl-sm text-[13px] leading-relaxed">
          {m.greeting && <div className="font-semibold text-slate-800 mb-0.5">{m.greeting}</div>}
          {m.text && <div dangerouslySetInnerHTML={{ __html: fmtText(m.text) }} />}

          {/* Single card */}
          {m.card && <CardView card={m.card} onOpen={onOpen} />}

          {/* Multiple cards / results */}
          {m.cards?.map((c, i) => <CardView key={i} card={c} onOpen={onOpen} />)}
          {m.results?.map((r, i) => (
            <CardView key={`r${i}`} card={{ kind: r.kind, title: r.title, subtitle: r.subtitle, rows: r.rows, href: r.href }} onOpen={onOpen} />
          ))}

          {/* OCR / action field grid */}
          {m.fields && <FieldGrid fields={m.fields} />}

          {/* Open-record button for action responses */}
          {m.recordUrl && (
            <button onClick={() => onOpen(m.recordUrl!)} className="mt-2 px-3 py-1.5 rounded-lg bg-blue-600 text-white text-[12px] font-semibold hover:bg-blue-700">
              <i className="fa-solid fa-arrow-up-right-from-square mr-1.5 text-[10px]" /> Open Record
            </button>
          )}

          {/* Plain links */}
          {!!m.links?.length && (
            <div className="mt-2 flex flex-col gap-1">
              {m.links.map((l, i) => (
                <button key={i} onClick={() => onOpen(l.href)} className="text-left text-[12px] font-semibold text-blue-600 hover:text-blue-700">
                  <i className="fa-solid fa-link mr-1.5 text-[10px]" />{l.label}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Options (vertical list) */}
        {!!m.options?.length && (
          <div className="mt-2 flex flex-col gap-1.5">
            {m.options.map((o, i) => (
              <button
                key={i}
                type="button"
                onClick={() => onChip(o)}
                className="dtc-chip text-left px-3 py-2 rounded-lg border border-slate-200 bg-white hover:border-blue-400 hover:bg-blue-50 text-[12px] text-slate-700 font-medium transition"
              >
                {o.label}
              </button>
            ))}
          </div>
        )}

        {/* Chips + quick replies (pills) */}
        {(!!m.chips?.length || !!m.quickReplies?.length) && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {[...(m.chips || []), ...(m.quickReplies || [])].map((c, i) => {
              const isPrint = c.value.startsWith('print:')
              const printTarget = isPrint ? resolvePrint(c.value) : null
              return (
                <button
                  key={i}
                  type="button"
                  onClick={() => (printTarget ? onPrint(printTarget) : onChip(c))}
                  className="dtc-chip px-3 py-1.5 rounded-full bg-blue-50 border border-blue-200 text-blue-700 text-[12px] font-medium hover:bg-blue-100 active:bg-blue-200 transition"
                >
                  {isPrint && <i className="fa-solid fa-print mr-1.5 text-[10px]" />}{c.label}
                </button>
              )
            })}
          </div>
        )}

        {/* AI tools-used footnote */}
        {!!m.toolsUsed?.length && (
          <div className="mt-1.5 text-[10px] text-slate-400">
            <i className="fa-solid fa-wrench mr-1" />{m.toolsUsed.join(', ')}
          </div>
        )}
      </div>
    </div>
  )
}
