import { useRef, useState } from 'react'
import { api } from '../api/client'

interface AskItem {
  task_id: number; video_code: string | null; title: string | null
  rating: number | null; poster_url: string | null; tags: string | null; actors: string | null
}

interface Msg {
  role: 'user' | 'assistant'
  content: string
  items?: AskItem[]
  query?: Record<string, unknown>
}

const EXAMPLES = ['8 分以上没看过的巨乳作品', 'ABC-123 是什么', '最近的高分新作']

export function AskOverlay() {
  const [open, setOpen] = useState(false)
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const bodyRef = useRef<HTMLDivElement>(null)

  const scrollBottom = () => {
    setTimeout(() => { if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight }, 60)
  }

  const ask = async (raw?: string) => {
    const q = (raw ?? input).trim()
    if (!q || busy) return
    setInput('')
    const history = msgs.slice(-6).map((m) => ({ role: m.role, content: m.content.slice(0, 200) }))
    setMsgs((p) => [...p, { role: 'user', content: q }])
    setBusy(true)
    try {
      const r = await api.aiAsk(q, history)
      setMsgs((p) => [...p, { role: 'assistant', content: `找到 ${r.total} 部`, items: r.items, query: r.query }])
    } catch (e) {
      setMsgs((p) => [...p, { role: 'assistant', content: `出错：${String((e as Error).message)}` }])
    }
    setBusy(false)
    scrollBottom()
  }

  if (!open) {
    return (
      <button onClick={() => setOpen(true)} aria-label="库内 AI 助手"
        style={{ position: 'fixed', right: 18, bottom: 18, zIndex: 999, width: 46, height: 46, borderRadius: 23, border: 'none', background: 'var(--gold, #d97706)', color: '#fff', fontSize: 15, fontWeight: 700, cursor: 'pointer', boxShadow: '0 4px 14px rgba(0,0,0,.25)' }}>
        AI
      </button>
    )
  }

  return (
    <div className="modal-pop" style={{ position: 'fixed', right: 18, bottom: 18, zIndex: 999, width: 380, maxWidth: 'calc(100vw - 24px)', height: 'min(560px, 75vh)', display: 'flex', flexDirection: 'column', background: 'var(--bg-page, #fff)', border: '1px solid var(--line, #e5e7eb)', borderRadius: 14, boxShadow: '0 8px 30px rgba(0,0,0,.25)', overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px', borderBottom: '1px solid var(--line, #e5e7eb)' }}>
        <span style={{ fontWeight: 700, fontSize: 13 }}>库内 AI 助手</span>
        <div style={{ display: 'flex', gap: 8 }}>
          {msgs.length > 0 && <button className="btn btn--ghost btn--sm" onClick={() => setMsgs([])}>清空</button>}
          <button onClick={() => setOpen(false)} style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 16 }} aria-label="关闭">✕</button>
        </div>
      </div>

      <div ref={bodyRef} style={{ flex: 1, overflow: 'auto', padding: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
        {msgs.length === 0 && (
          <div style={{ fontSize: 11, color: 'var(--t-mute)', textAlign: 'center', padding: '14px 0' }}>
            用自然语言和库对话：筛片、查作品、追问细化。
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'center', marginTop: 8 }}>
              {EXAMPLES.map((ex) => (
                <button key={ex} className="btn btn--ghost btn--sm" onClick={() => ask(ex)} style={{ fontSize: 10 }}>{ex}</button>
              ))}
            </div>
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: m.role === 'user' ? 'flex-end' : 'flex-start' }}>
            <div style={{
              maxWidth: '88%', padding: '7px 11px', borderRadius: 10, fontSize: 12, lineHeight: 1.5,
              background: m.role === 'user' ? 'var(--gold, #d97706)' : 'var(--bg-raised, #f3f4f6)',
              color: m.role === 'user' ? '#fff' : 'var(--t-body)',
            }}>
              {m.content}
            </div>
            {m.items && m.items.length > 0 && (
              <div style={{ width: '88%', display: 'flex', flexDirection: 'column', gap: 5, marginTop: 6 }}>
                {m.items.map((t) => (
                  <div key={t.task_id} style={{ display: 'flex', gap: 8, alignItems: 'center', border: '1px solid var(--line, #eee)', borderRadius: 8, padding: 6, fontSize: 11 }}>
                    {t.poster_url
                      ? <img src={t.poster_url} alt="" style={{ width: 26, height: 37, objectFit: 'cover', borderRadius: 4 }} loading="lazy" referrerPolicy="no-referrer"
                          onError={(e) => { e.currentTarget.style.visibility = 'hidden' }} />
                      : <div style={{ width: 26, height: 37, background: 'var(--bg-raised, #f3f4f6)', borderRadius: 4 }} />}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div>{t.video_code}{t.rating ? <span style={{ color: 'var(--gold, #d97706)' }}> {t.rating}</span> : null}</div>
                      <div style={{ color: 'var(--t-mute)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.title || ''}</div>
                    </div>
                  </div>
                ))}
              </div>
            )}
            {m.items && m.items.length === 0 && m.query && (
              <div style={{ fontSize: 10, color: 'var(--t-faint)', marginTop: 3 }}>（没有匹配，可换个说法或放松条件）</div>
            )}
          </div>
        ))}
        {busy && <div style={{ fontSize: 11, color: 'var(--t-mute)', textAlign: 'center' }}>思考中…</div>}
      </div>

      <div style={{ padding: 10, display: 'flex', gap: 8, borderTop: '1px solid var(--line, #e5e7eb)' }}>
        <input className="input" value={input} placeholder="继续对话…（追问会自动叠加条件）"
          onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') ask() }}
          style={{ flex: 1, fontSize: 12 }} />
        <button className="btn btn--gold btn--sm" onClick={() => ask()} disabled={busy}>发送</button>
      </div>
    </div>
  )
}
