import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { NewRelease, Actor } from '../api/types'
import { PageHead, Loading, Empty, ErrorEmpty } from '../components/States'
import { Icon } from '../components/Icons'
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
  const nav = useNavigate()
  const [subs, setSubs] = useState<Subscription[] | null>(null)
  const [releases, setReleases] = useState<NewRelease[] | null>(null)
  const [avatars, setAvatars] = useState<Map<number, string>>(new Map())  // actor_id → avatar_url
  const [error, setError] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)
  const toastOk = useStore((s) => s.toastOk)
  const toastErr = useStore((s) => s.toastErr)

  const load = () => {
    setSubs(null); setReleases(null); setError(null)
    api.subscriptions.list(undefined).then((r: unknown) => {
      setSubs((r as Subscription[]) || [])
    }).catch((e) => { setError(String((e as Error).message)); setSubs([]) })
    api.newReleases.list({ limit: 100 }).then((r) => setReleases(r.items || [])).catch(() => setReleases([]))
    // 演员订阅头像映射（一次拉全量演员，建 actor_id → avatar_url）
    api.actors.list(0, 500, true).then((list: Actor[]) => {
      const m = new Map<number, string>()
      for (const a of list) if (a.avatar_url) m.set(a.id, a.avatar_url)
      setAvatars(m)
    }).catch(() => {})
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
    if (!(await useStore.getState().confirm(`删除订阅「${s.name}」`, '删除后不再巡检该订阅，可重新创建。确定删除？'))) return
    try {
      await api.subscriptions.delete(s.id)
      setSubs((prev) => prev ? prev.filter((x) => x.id !== s.id) : prev)
      toastOk('已删除')
    } catch (e) { toastErr(String((e as Error).message)) }
  }

  const checkAll = async () => {
    setChecking(true)
    try {
      const r = await api.newReleases.checkAll()
      const res = r.result || {}
      toastOk(`巡检完成：${res.checked_actors || 0} 位演员，发现 ${res.total_new || 0} 部新作，已推送 ${res.total_pushed || 0} 部`)
      load()  // 刷新新作品列表
    } catch (e) { toastErr(String((e as Error).message)) }
    finally { setChecking(false) }
  }

  const markRead = async (nr: NewRelease) => {
    try {
      await api.newReleases.markRead(nr.id)
      setReleases((prev) => prev ? prev.map((x) => x.id === nr.id ? { ...x, is_read: true } : x) : prev)
    } catch (e) { toastErr(String((e as Error).message)) }
  }

  const addToLib = async (nr: NewRelease) => {
    try {
      const r = await api.newReleases.addToLibrary(nr.id)
      toastOk(r.task_id ? `已入库（task ${r.task_id}）` : '该作品已入库')
      setReleases((prev) => prev ? prev.map((x) => x.id === nr.id ? { ...x, added_to_library: true, is_read: true, task_id: r.task_id ?? x.task_id } : x) : prev)
    } catch (e) { toastErr(String((e as Error).message)) }
  }

  const fmtTime = (t: string | null) => {
    if (!t) return '从未检查'
    try {
      const d = new Date(t)
      return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
    } catch { return t }
  }

  const unreadCount = releases?.filter((r) => !r.is_read).length || 0
  // 封蜡揭幕：未读新作默认蒙纱+蜡封；首次 hover 蜡封碎裂、封面亮起（会话内保持）
  const [cracked, setCracked] = useState<Set<number>>(new Set())
  const crack = (id: number) => setCracked((prev) => new Set(prev).add(id))

  return (
    <div className="page">
      <PageHead eyebrow={`Subscriptions · ${subs?.length ?? 0} 条`} title={<>订<em>阅</em></>}
        sub="订阅演员，有新作自动通知/下载。巡检时与 Emby 媒体库比对，避免重复入库。">
        <button className="btn btn--ghost btn--sm" onClick={checkAll} disabled={checking}>
          <Icon.refresh />{checking ? '巡检中…' : '立即巡检全部'}
        </button>
      </PageHead>

      {/* 订阅列表：卡片式（演员订阅显示头像） */}
      {error ? <ErrorEmpty message={error} onRetry={load} /> :
       subs === null ? <Loading /> : subs.length === 0 ? (
        <Empty icon="◌" title="暂无订阅" sub="前往演员库，点击演员卡片的「订阅」按钮即可添加。" />
      ) : (
        <div className="sub-grid">
          {subs.map((s) => {
            const clickable = s.sub_type === 'actor' && s.actor_id
            const avatar = s.actor_id != null ? avatars.get(s.actor_id) : undefined
            return (
            <div key={s.id} className={`sub-card${s.enabled ? '' : ' off'}`}
              onClick={() => clickable && nav(`/actor/${s.actor_id}`)}
              role={clickable ? 'button' : undefined}
              tabIndex={clickable ? 0 : undefined}
              aria-label={clickable ? `查看演员 ${s.name}` : s.name}
              onKeyDown={clickable ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); nav(`/actor/${s.actor_id}`) } } : undefined}>
              <div className="sub-photo">
                {avatar ? (
                  <img src={avatar} alt={s.name} referrerPolicy="no-referrer" loading="lazy"
                    onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none' }} />
                ) : null}
                <div className="sub-ph">{avatar ? '' : TYPE_LABEL[s.sub_type]?.[0] || '?'}</div>
              </div>
              <div className="sub-body">
                <div className="sub-name">{s.name}</div>
                <div className="sub-meta">
                  <span className="chip chip-rose">{TYPE_LABEL[s.sub_type] || s.sub_type}</span>
                  {s.auto_add && <span className="chip chip-amber">自动下载</span>}
                </div>
                <div className="sub-check">每 {s.check_interval_hours}h 检查 · {fmtTime(s.last_checked_at)}</div>
                <div className="sub-actions">
                  <button
                    onClick={(e) => { e.stopPropagation(); toggle(s) }}
                    className={`btn btn--sm ${s.enabled ? 'btn--ghost' : 'btn--gold'}`}
                    style={{ fontSize: 11, flex: 1 }}>{s.enabled ? '停用' : '启用'}</button>
                  <button
                    onClick={(e) => { e.stopPropagation(); remove(s) }}
                    className="btn btn--sm btn--ghost"
                    style={{ fontSize: 11, color: 'var(--red)' }}>删除</button>
                </div>
              </div>
            </div>
            )
          })}
        </div>
      )}

      {/* 新作品列表 */}
      <div className="card" style={{ marginTop: 22 }}>
        <div className="card-head" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div className="card-title">
            <Icon.refresh /> 新作品发现
            {unreadCount > 0 && <span style={{ marginLeft: 8, fontSize: 11, color: 'var(--gold)' }}>{unreadCount} 未读</span>}
          </div>
          <button className="btn btn--ghost btn--sm" onClick={load}>刷新</button>
        </div>
        {releases === null ? <div style={{ color: 'var(--t-faint)', fontSize: 13, padding: 16 }}>加载中…</div> :
         releases.length === 0 ? (
          <div style={{ color: 'var(--t-faint)', fontSize: 13, padding: 16 }}>
            暂无新作品。点击上方「立即巡检全部」检查订阅演员的新作。
          </div>
        ) : (
          <div>
            {releases.map((nr) => {
              const sealed = !nr.is_read && !cracked.has(nr.id)
              return (
              <div key={nr.id} className="recent-item" style={{
                alignItems: 'center',
                opacity: nr.is_read ? 0.55 : 1,
                background: nr.is_read ? 'transparent' : 'var(--gold-wash)',
              }}>
                <div
                  className={`wax-wrap${sealed ? ' sealed ready' : ''}`}
                  style={{ position: 'relative', flex: 'none' }}
                  onPointerEnter={() => { if (sealed) crack(nr.id) }}
                  onClick={() => { if (sealed) crack(nr.id) }}
                >
                  <img
                    src={nr.cover_url || ''}
                    alt=""
                    referrerPolicy="no-referrer"
                    onError={(e) => { e.currentTarget.style.visibility = 'hidden' }}
                    style={{ width: 44, height: 60, borderRadius: 6, objectFit: 'cover', objectPosition: 'right center', background: 'var(--bg-page)', display: 'block' }}
                  />
                  {!nr.is_read && (
                    <span className={`wax-seal${cracked.has(nr.id) ? ' wax-crack' : ''}`} aria-hidden="true">♥</span>
                  )}
                </div>
                <div className="recent-meta">
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span className="recent-code">{nr.video_code}</span>
                    {nr.actor_name && <span style={{ fontSize: 11, color: 'var(--t-faint)' }}>{nr.actor_name}</span>}
                    {nr.added_to_library && <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 4, background: 'rgba(74,138,90,.15)', color: 'var(--green)' }}>已入库</span>}
                  </div>
                  <div className="recent-title" style={{ WebkitLineClamp: 1 }}>{nr.title || '—'}</div>
                  <div style={{ fontSize: 11, color: 'var(--t-faint)' }}>{fmtTime(nr.discovered_at)}</div>
                </div>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                  {!nr.is_read && (
                    <button onClick={() => markRead(nr)} className="btn btn--sm btn--ghost" style={{ fontSize: 11 }}>已读</button>
                  )}
                  {!nr.added_to_library && (
                    <button onClick={() => addToLib(nr)} className="btn btn--sm btn--gold" style={{ fontSize: 11 }}>入库</button>
                  )}
                  {nr.task_id && (
                    <button onClick={() => nav(`/task/${nr.task_id}`)} className="btn btn--sm btn--ghost" style={{ fontSize: 11 }}>详情</button>
                  )}
                </div>
              </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
