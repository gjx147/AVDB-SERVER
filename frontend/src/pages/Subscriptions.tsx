import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { PageHead, Loading, Empty, ErrorEmpty } from '../components/States'
import { useStore } from '../store/useStore'

interface Subscription {
  id: number
  name: string
  sub_type: string  // actor | ranking | composite
  rank_type: string | null
  actor_id: number | null
  auto_add: boolean
  enabled: boolean
  check_interval_hours: number
  last_checked_at: string | null
  last_result: string | null
}

const TYPE_LABEL: Record<string, string> = {
  actor: '演员',
  ranking: '榜单',
  composite: '组合',
}

export function Subscriptions() {
  const [subs, setSubs] = useState<Subscription[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const toastOk = useStore((s) => s.toastOk)
  const toastErr = useStore((s) => s.toastErr)

  const load = () => {
    setSubs(null); setError(null)
    api.subscriptions.list(undefined).then((r: unknown) => {
      setSubs((r as Subscription[]) || [])
    }).catch((e) => { setError(String((e as Error).message)); setSubs([]) })
  }
  useEffect(() => { load() }, [])

  const toggle = async (s: Subscription) => {
    try {
      const r = await api.subscriptions.toggle(s.id)
      setSubs((prev) => prev ? prev.map((x) => x.id === s.id ? { ...x, enabled: r.enabled } : x) : prev)
      toastOk(r.enabled ? '已启用' : '已停用')
    } catch (e) { toastErr(String((e as Error).message)) }
  }
  const remove = async (s: Subscription) => {
    if (!confirm(`删除订阅「${s.name}」？`)) return
    try {
      await api.subscriptions.delete(s.id)
      setSubs((prev) => prev ? prev.filter((x) => x.id !== s.id) : prev)
      toastOk('已删除')
    } catch (e) { toastErr(String((e as Error).message)) }
  }

  const fmtTime = (t: string | null) => {
    if (!t) return '从未检查'
    try {
      const d = new Date(t)
      return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
    } catch { return t }
  }

  return (
    <div className="page">
      <PageHead eyebrow={`Subscriptions · ${subs?.length ?? 0} 条`} title={<>订<em>阅</em></>}
        sub="订阅演员或榜单，有新作自动入库。在演员库页面点击「订阅」按钮添加。">
      </PageHead>

      {error ? <ErrorEmpty message={error} onRetry={load} /> :
       subs === null ? <Loading /> : subs.length === 0 ? (
        <Empty icon="◌" title="暂无订阅" sub="前往演员库，点击演员卡片的「订阅」按钮即可添加。" />
      ) : (
        <div className="card">
          {subs.map((s) => (
            <div key={s.id} className="recent-item" style={{ alignItems: 'center' }}>
              <div style={{
                flex: 'none', width: 44, height: 44, borderRadius: 10,
                background: s.enabled ? 'var(--gold-wash)' : 'var(--bg-page)',
                color: s.enabled ? 'var(--gold)' : 'var(--t-faint)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 18, fontWeight: 700,
              }}>{TYPE_LABEL[s.sub_type]?.[0] || '?'}</div>
              <div className="recent-meta">
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span className="recent-code">{s.name}</span>
                  <span style={{
                    fontSize: 10, padding: '1px 7px', borderRadius: 4, fontWeight: 600,
                    background: s.sub_type === 'actor' ? 'rgba(74,138,90,.1)' : 'rgba(176,122,30,.1)',
                    color: s.sub_type === 'actor' ? 'var(--green)' : 'var(--amber)',
                  }}>{TYPE_LABEL[s.sub_type] || s.sub_type}</span>
                  {s.auto_add && <span style={{ fontSize: 10, color: 'var(--t-faint)' }}>自动入库</span>}
                </div>
                <div className="recent-title" style={{ WebkitLineClamp: 1 }}>
                  每 {s.check_interval_hours}h 检查 · {fmtTime(s.last_checked_at)}
                </div>
                {s.last_result && (
                  <div style={{ fontSize: 11, color: 'var(--t-faint)', marginTop: 2 }}>
                    {s.last_result.length > 80 ? s.last_result.slice(0, 80) + '…' : s.last_result}
                  </div>
                )}
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <button
                  onClick={() => toggle(s)}
                  className={`btn btn--sm ${s.enabled ? 'btn--ghost' : 'btn--gold'}`}
                  style={{ fontSize: 11 }}>{s.enabled ? '停用' : '启用'}</button>
                <button
                  onClick={() => remove(s)}
                  className="btn btn--sm btn--ghost"
                  style={{ fontSize: 11, color: 'var(--red)' }}>删除</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
