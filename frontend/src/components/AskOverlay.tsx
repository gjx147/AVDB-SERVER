import { useRef, useState } from 'react'
import { api } from '../api/client'

interface AskItem {
  task_id: number; video_code: string | null; title: string | null
  rating: number | null; poster_url: string | null; tags: string | null; actors: string | null
}

interface ConfirmCard {
  token: string; tool: string; tool_cn: string
  args: Record<string, unknown>; preview: string; reason?: string
}

interface Msg {
  role: 'user' | 'assistant'
  content: string
  items?: AskItem[]
  confirm?: ConfirmCard
  done?: string
}

const EXAMPLES = ['8 分以上没看过的巨乳作品', '库里有几部作品？', '查看订阅列表', '巡检一下系统']

export function AskOverlay() {
  const [open, setOpen] = useState(false)
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const bodyRef = useRef<HTMLDivElement>(null)

  const scrollBottom = () => {
    setTimeout(() => { if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight }, 60)
  }

  const [listening, setListening] = useState(false)
  const recRef = useRef<{ stop: () => void } | null>(null)

  const toggleVoice = () => {
    const SR = (window as unknown as { webkitSpeechRecognition?: unknown; SpeechRecognition?: unknown }).webkitSpeechRecognition
      || (window as unknown as { SpeechRecognition?: unknown }).SpeechRecognition
    if (!SR) { alert('当前浏览器不支持语音输入（需 Chrome/Edge）'); return }
    if (listening) { recRef.current?.stop(); setListening(false); return }
    try {
      const rec = new (SR as new () => { lang: string; interimResults: boolean; onresult: (e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void; onend: () => void; onerror: () => void; start: () => void; stop: () => void })()
      rec.lang = 'zh-CN'
      rec.interimResults = true
      rec.onresult = (e) => {
        let t = ''
        for (let i = 0; i < e.results.length; i++) t += e.results[i][0].transcript
        setInput(t)
      }
      rec.onend = () => setListening(false)
      rec.onerror = () => setListening(false)
      rec.start()
      recRef.current = rec
      setListening(true)
    } catch { alert('语音识别启动失败') }
  }

  const ask = async (raw?: string) => {
    const q = (raw ?? input).trim()
    if (!q || busy) return
    setInput('')
    const history = msgs.slice(-8).map((m) => ({ role: m.role, content: m.content.slice(0, 200) }))
    setMsgs((p) => [...p, { role: 'user', content: q }])
    setBusy(true)
    try {
      const r = await api.agentChat([...history, { role: 'user', content: q }])
      if (r.type === 'confirm') {
        setMsgs((p) => [...p, {
          role: 'assistant', content: `需要确认：${r.tool_cn || r.tool || ''}`,
          confirm: { token: r.token || '', tool: r.tool || '', tool_cn: r.tool_cn || r.tool || '', args: r.args || {}, preview: r.preview || '', reason: r.reason },
        }])
      } else {
        setMsgs((p) => [...p, { role: 'assistant', content: r.content || '', items: (r.items || []) as AskItem[] }])
      }
    } catch (e) {
      setMsgs((p) => [...p, { role: 'assistant', content: `出错：${String((e as Error).message)}` }])
    }
    setBusy(false)
    scrollBottom()
  }

  const confirm = async (i: number) => {
    const c = msgs[i].confirm
    if (!c || busy) return
    setBusy(true)
    try {
      const r = await api.agentConfirm(c.token)
      const ok = r.result?.ok
      setMsgs((p) => p.map((m, idx) => idx === i
        ? { ...m, confirm: undefined, content: ok ? `✅ ${r.result?.message || '已执行'}` : `❌ ${r.result?.message || '执行失败'}` }
        : m))
    } catch (e) {
      setMsgs((p) => p.map((m, idx) => idx === i ? { ...m, confirm: undefined, content: `❌ ${String((e as Error).message)}` } : m))
    }
    setBusy(false)
  }

  const cancel = (i: number) => {
    setMsgs((p) => p.map((m, idx) => idx === i ? { ...m, confirm: undefined, content: '已取消' } : m))
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
    <div className="modal-pop" style={{ position: 'fixed', right: 18, bottom: 18, zIndex: 999, width: 400, maxWidth: 'calc(100vw - 24px)', height: 'min(600px, 78vh)', display: 'flex', flexDirection: 'column', background: 'var(--bg-page, #fff)', border: '1px solid var(--line, #e5e7eb)', borderRadius: 14, boxShadow: '0 8px 30px rgba(0,0,0,.25)', overflow: 'hidden' }}>
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
            和助手对话，完成库内所有操作：检索、订阅、规则、配置、巡检……
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
              maxWidth: '88%', padding: '7px 11px', borderRadius: 10, fontSize: 12, lineHeight: 1.5, whiteSpace: 'pre-wrap',
              background: m.role === 'user' ? 'var(--gold, #d97706)' : 'var(--bg-raised, #f3f4f6)',
              color: m.role === 'user' ? '#fff' : 'var(--t-body)',
            }}>
              {m.content}
            </div>
            {m.confirm && (
              <div style={{ width: '88%', marginTop: 6, border: '1px solid var(--line, #e5e7eb)', borderRadius: 10, padding: 10, fontSize: 11, background: 'var(--bg-page, #fff)' }}>
                <div style={{ fontWeight: 700, marginBottom: 6 }}>⚠️ 确认执行{m.confirm.reason ? `（${m.confirm.reason}）` : ''}</div>
                <div style={{ color: 'var(--t-mute)', overflowWrap: 'anywhere' }}>{m.confirm.preview}</div>
                <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                  <button className="btn btn--gold btn--sm" onClick={() => confirm(i)} disabled={busy}>执行</button>
                  <button className="btn btn--ghost btn--sm" onClick={() => cancel(i)} disabled={busy}>取消</button>
                </div>
              </div>
            )}
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
          </div>
        ))}
        {busy && <div style={{ fontSize: 11, color: 'var(--t-mute)', textAlign: 'center' }}>思考中…</div>}
      </div>

      <div style={{ padding: 10, display: 'flex', gap: 8, borderTop: '1px solid var(--line, #e5e7eb)' }}>
        <button onClick={toggleVoice} disabled={busy} title={listening ? '停止录音' : '语音输入'}
          style={{ border: '1px solid var(--line, #e5e7eb)', background: listening ? 'var(--red, #dc2626)' : 'var(--bg-page, #fff)', color: listening ? '#fff' : 'var(--t-body)', borderRadius: 8, padding: '0 10px', fontSize: 13, cursor: 'pointer' }}>
          {listening ? '⏹' : '🎤'}
        </button>
        <input className="input" value={input} placeholder={listening ? '正在听…' : '检索、订阅、规则、配置、巡检都能聊…'}
          onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') ask() }}
          style={{ flex: 1, fontSize: 12 }} />
        <button className="btn btn--gold btn--sm" onClick={() => ask()} disabled={busy}>发送</button>
      </div>
    </div>
  )
}
