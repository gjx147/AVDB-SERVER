import { useState } from 'react'
import { api } from '../api/client'

interface AskItem {
  task_id: number; video_code: string | null; title: string | null
  rating: number | null; poster_url: string | null; tags: string | null; actors: string | null
}

export function AskOverlay() {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState<{ query: Record<string, unknown>; total: number; items: AskItem[] } | null>(null)
  const [err, setErr] = useState('')
  const ask = async () => {
    const question = q.trim()
    if (!question || busy) return
    setBusy(true); setErr('')
    try {
      const r = await api.aiAsk(question)
      setRes(r)
    } catch (e) { setErr(String((e as Error).message)) }
    setBusy(false)
  }
  if (!open) {
    return (
      <button onClick={() => setOpen(true)} aria-label="库内 AI 问答"
        style={{ position: 'fixed', right: 18, bottom: 18, zIndex: 999, width: 46, height: 46, borderRadius: 23, border: 'none', background: 'var(--gold, #d97706)', color: '#fff', fontSize: 15, fontWeight: 700, cursor: 'pointer', boxShadow: '0 4px 14px rgba(0,0,0,.25)' }}>
        AI
      </button>
    )
  }
  return (
    <div style={{ position: 'fixed', right: 18, bottom: 18, zIndex: 999, width: 360, maxWidth: 'calc(100vw - 36px)', maxHeight: '70vh', display: 'flex', flexDirection: 'column', background: 'var(--bg-page, #fff)', border: '1px solid var(--line, #e5e7eb)', borderRadius: 14, boxShadow: '0 8px 30px rgba(0,0,0,.25)', overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px', borderBottom: '1px solid var(--line, #e5e7eb)' }}>
        <span style={{ fontWeight: 700, fontSize: 13 }}>库内 AI 问答</span>
        <button onClick={() => setOpen(false)} style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 16 }} aria-label="关闭">✕</button>
      </div>
      <div style={{ padding: 10, display: 'flex', gap: 8 }}>
        <input className="input" value={q} placeholder="例如：8 分以上没看过的巨乳作品"
          onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') ask() }}
          style={{ flex: 1, fontSize: 12 }} />
        <button className="btn btn--gold btn--sm" onClick={ask} disabled={busy}>{busy ? '查询中…' : '问'}</button>
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: '0 10px 10px' }}>
        {err && <div style={{ fontSize: 12, color: 'var(--red, #dc2626)', margin: '6px 0' }}>{err}</div>}
        {res && (
          <>
            <div style={{ fontSize: 11, color: 'var(--t-mute)', margin: '6px 0' }}>找到 {res.total} 部</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {res.items.map((t) => (
                <div key={t.task_id} style={{ display: 'flex', gap: 8, alignItems: 'center', border: '1px solid var(--line, #eee)', borderRadius: 8, padding: 6, fontSize: 12 }}>
                  {t.poster_url
                    ? <img src={t.poster_url} alt="" style={{ width: 30, height: 42, objectFit: 'cover', borderRadius: 4 }} loading="lazy" referrerPolicy="no-referrer" />
                    : <div style={{ width: 30, height: 42, background: 'var(--bg-raised, #f3f4f6)', borderRadius: 4 }} />}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div>{t.video_code}{t.rating ? <span style={{ color: 'var(--gold, #d97706)' }}> {t.rating}</span> : null}</div>
                    <div style={{ color: 'var(--t-mute)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.title || ''}</div>
                  </div>
                </div>
              ))}
            </div>
            <details style={{ marginTop: 8, fontSize: 10, color: 'var(--t-faint)' }}>
              <summary>筛选条件</summary>
              <pre style={{ whiteSpace: 'pre-wrap', margin: 4 }}>{JSON.stringify(res.query, null, 2)}</pre>
            </details>
          </>
        )}
      </div>
    </div>
  )
}
