import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import { PageHead, Loading, ErrorEmpty, Empty } from '../components/States'
import { useStore } from '../store/useStore'

interface NotifyItem {
  id: number; event: string; title: string; body: string
  channel: string; ok: boolean; message: string; created_at: string
}

export function Notifications() {
  const [items, setItems] = useState<NotifyItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [events, setEvents] = useState<string[]>([])
  const [evt, setEvt] = useState('')
  const [dnd, setDnd] = useState({ dnd_start: '', dnd_end: '' })
  const toastOk = useStore((s) => s.toastOk)
  const toastErr = useStore((s) => s.toastErr)

  const load = useCallback(() => {
    api.notifications.list(evt || undefined).then((r) => setItems(r.items))
      .catch((e) => { setError(String((e as Error).message)); setItems([]) })
  }, [evt])
  useEffect(() => { load() }, [load])
  useEffect(() => {
    api.notifications.events().then((r) => setEvents(r.events)).catch(() => {})
    api.notifications.dnd().then(setDnd).catch(() => {})
  }, [])

  const test = async () => {
    try {
      const r = await api.notifications.test()
      const parts = Object.entries(r).filter(([, v]) => typeof v === 'boolean')
        .map(([k, v]) => `${k}: ${v ? '✓' : '✗'}`)
      toastOk(`测试结果  ${parts.join('  ')}`)
      load()
    } catch (e) { toastErr(String((e as Error).message)) }
  }

  const saveDnd = async () => {
    try { await api.notifications.setDnd(dnd); toastOk('免打扰时段已保存') }
    catch (e) { toastErr(String((e as Error).message)) }
  }

  return (
    <div className="page">
      <PageHead eyebrow="Notifications" title={<>通知<em>中心</em></>}
        sub="通知发送历史、各通道测试与免打扰时段。">
        <button className="btn btn--gold btn--sm" onClick={test}>发送测试通知</button>
      </PageHead>

      <div className="card" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', gap: 12, alignItems: 'end', flexWrap: 'wrap' }}>
          <div className="field" style={{ margin: 0 }}>
            <label>免打扰开始 (HH:MM)</label>
            <input className="input" value={dnd.dnd_start} placeholder="23:00"
              onChange={(e) => setDnd({ ...dnd, dnd_start: e.target.value })} style={{ width: 110 }} />
          </div>
          <div className="field" style={{ margin: 0 }}>
            <label>免打扰结束 (HH:MM)</label>
            <input className="input" value={dnd.dnd_end} placeholder="08:00"
              onChange={(e) => setDnd({ ...dnd, dnd_end: e.target.value })} style={{ width: 110 }} />
          </div>
          <button className="btn btn--ghost btn--sm" onClick={saveDnd}>保存时段</button>
          <div style={{ fontSize: 11, color: 'var(--t-mute)', paddingBottom: 6 }}>
            时段内新作/爬取告警等通知将被静默跳过（爬取日志仍正常记录）
          </div>
        </div>
      </div>

      <div className="card">
        {error ? <ErrorEmpty message={error} onRetry={load} /> :
         items === null ? <Loading /> :
         items.length === 0 ? (
          <Empty icon="&#x1F515;" title="暂无通知记录" sub="通知发送后会记录在这里。" />
        ) : (
          <>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10 }}>
              <select className="select" value={evt} onChange={(e) => setEvt(e.target.value)} aria-label="按事件筛选">
                <option value="">全部事件</option>
                {events.map((e) => <option key={e} value={e}>{e}</option>)}
              </select>
              <span style={{ fontSize: 11, color: 'var(--t-mute)' }}>共 {items.length} 条</span>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {items.map((n) => (
                <div key={n.id} style={{
                  display: 'flex', gap: 10, alignItems: 'center', padding: '8px 10px',
                  borderRadius: 8, background: n.ok ? 'transparent' : 'var(--bg-wash, #fff5f5)',
                  border: '1px solid var(--line, #eee)', fontSize: 13,
                }}>
                  <span style={{ flex: 'none', width: 74, fontWeight: 600, fontSize: 11, color: 'var(--t-mute)' }}>{n.event}</span>
                  <span style={{ flex: 'none', width: 52, fontSize: 11 }}>{n.channel}</span>
                  <span style={{ flex: 'none', color: n.ok ? 'var(--green, #059669)' : 'var(--red, #dc2626)' }}>{n.ok ? '✓' : '✗'}</span>
                  <span style={{ flex: '1 1 auto', minWidth: 0 }}>{n.title}</span>
                  <span style={{ flex: 'none', fontSize: 11, color: 'var(--t-faint, #999)' }}>{n.created_at?.slice(5, 19).replace('T', ' ')}</span>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
