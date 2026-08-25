import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { NewRelease, Task } from '../api/types'
import { PageHead, Loading, Empty, ErrorEmpty } from '../components/States'
import { Icon } from '../components/Icons'
import { useStore } from '../store/useStore'

/** 订阅上新：所有演员订阅发现的新作品（原订阅页「新作品发现」，移出独立成页） */
export function NewReleases() {
  const nav = useNavigate()
  const [releases, setReleases] = useState<NewRelease[] | null>(null)
  const [unreadOnly, setUnreadOnly] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // F4 发布月历
  const [cal, setCal] = useState<{ month: string; days: Record<string, number> } | null>(null)
  const [selDay, setSelDay] = useState('')
  const [dayTasks, setDayTasks] = useState<Task[] | null>(null)
  const toastOk = useStore((s) => s.toastOk)
  const toastErr = useStore((s) => s.toastErr)

  const load = () => {
    setReleases(null)
    setError(null)
    api.newReleases.list({ limit: 200 }).then((r) => setReleases(r.items || [])).catch((e) => { setError(String((e as Error).message)); setReleases([]) })
  }
  useEffect(() => { load() }, [])
  useEffect(() => { api.releaseCalendar().then(setCal).catch(() => {}) }, [])
  const pickDay = async (d: string) => {
    setSelDay(d)
    setDayTasks(null)
    try {
      const r = await api.v2.tasks({ date_from: d, date_to: d, limit: 50, sort: 'rating' })
      setDayTasks(r.tasks)
    } catch { setDayTasks([]) }
  }

  // 封蜡揭幕：未读新作默认蒙纱+蜡封；首次 hover 蜡封碎裂、封面亮起（会话内保持）
  const [cracked, setCracked] = useState<Set<number>>(new Set())
  const crack = (id: number) => setCracked((prev) => new Set(prev).add(id))

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
    if (!t) return '—'
    try {
      const d = new Date(t)
      return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
    } catch { return t }
  }

  const unreadCount = releases?.filter((r) => !r.is_read).length || 0
  const shown = releases && unreadOnly ? releases.filter((r) => !r.is_read) : (releases || [])

  return (
    <div className="page">
      <PageHead eyebrow={`New Releases · ${unreadCount} 未读`} title={<>订阅<em>上新</em></>}
        sub="订阅的演员们带来的新作品——开启自动入库的，已经在自己走进来了。">
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--t-mute)', cursor: 'pointer', userSelect: 'none' }}>
          <input type="checkbox" checked={unreadOnly} onChange={(e) => setUnreadOnly(e.target.checked)} />
          只看未读
        </label>
        <button className="btn btn--ghost btn--sm" onClick={load}><Icon.refresh />刷新</button>
      </PageHead>

      {cal && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
            <div style={{ fontSize: 13, fontWeight: 600 }}>发布日历 · {cal.month}</div>
            <div style={{ fontSize: 11, color: 'var(--t-mute)' }}>本月共 {Object.values(cal.days).reduce((a, b) => a + b, 0)} 部入库作品（按发布日期）</div>
          </div>
          <MonthGrid month={cal.month} days={cal.days} selDay={selDay} onPick={pickDay} />
          {selDay && dayTasks && (
            <div style={{ marginTop: 10, borderTop: '1px solid var(--line, #eee)', paddingTop: 8 }}>
              <div style={{ fontSize: 12, color: 'var(--t-mute)', marginBottom: 6 }}>{selDay} 共 {dayTasks.length} 部</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 200, overflow: 'auto' }}>
                {dayTasks.map((t) => (
                  <div key={t.id} style={{ fontSize: 12, display: 'flex', gap: 8 }}>
                    <span style={{ color: 'var(--t-mute)', flex: 'none' }}>{t.video_code}</span>
                    <span style={{ flex: '1 1 auto', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.title || ''}</span>
                    {t.rating ? <span style={{ flex: 'none', color: 'var(--gold, #d97706)' }}>{t.rating}</span> : null}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      <div className="card">
        {error ? <ErrorEmpty message={error} onRetry={load} /> :
         releases === null ? <Loading /> :
         shown.length === 0 ? (
          <Empty icon="◌" title={unreadOnly ? '没有未读的新作' : '暂无新作品'}
            sub="订阅演员后系统自动巡检，新作品会出现在这里；也可到订阅页点「立即巡检全部」。" />
        ) : (
          <div>
            {releases && releases.length >= 200 && (
              <div style={{ fontSize: 11, color: 'var(--t-mute)', marginBottom: 8 }}>
                仅显示前 200 条（共 {releases.length} 条），更多请用影片库筛选
              </div>
            )}
            {shown.map((nr) => {
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
                    {nr.actor_name && (
                      <span style={{ fontSize: 11, color: 'var(--t-faint)', cursor: 'pointer' }}
                        onClick={() => nav(`/library?q=${encodeURIComponent(nr.actor_name || '')}`)}>{nr.actor_name}</span>
                    )}
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


function MonthGrid({ month, days, selDay, onPick }: {
  month: string
  days: Record<string, number>
  selDay: string
  onPick: (d: string) => void
}) {
  const [y, m] = month.split('-').map(Number)
  const first = new Date(y, m - 1, 1)
  const offset = first.getDay()
  const dim = new Date(y, m, 0).getDate()
  const cells = []
  for (let i = 0; i < offset; i++) cells.push(<div key={`e${i}`} style={{ width: 34, height: 26 }} />)
  for (let d = 1; d <= dim; d++) {
    const key = `${month}-${String(d).padStart(2, '0')}`
    const cnt = days[key] ?? 0
    cells.push(
      <div key={key} onClick={() => cnt > 0 && onPick(key)} title={cnt > 0 ? `${key} 共 ${cnt} 部` : key}
        style={{
          width: 34, height: 26, display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 11, borderRadius: 6, cursor: cnt > 0 ? 'pointer' : 'default',
          background: cnt > 5 ? 'var(--gold-wash, #fde68a)' : cnt > 0 ? 'var(--bg-raised, #f3f4f6)' : 'transparent',
          color: cnt > 0 ? '#7c5a00' : 'var(--t-faint, #999)',
          border: selDay === key ? '1px solid var(--gold, #d97706)' : '1px solid transparent',
        }}>
        {d}
      </div>
    )
  }
  return <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 34px)', gap: 4, justifyContent: 'center' }}>{cells}</div>
}
