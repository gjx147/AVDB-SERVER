import { useEffect, useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { api, coverFileUrl } from '../api/client'
import type { DashboardStats, Task, MonthlyStat, DiskInfo } from '../api/types'
import { PageHead, Loading, Empty, ErrorEmpty } from '../components/States'
import { Icon } from '../components/Icons'

export function Dashboard() {
  const nav = useNavigate()
  const [stats, setStats] = useState<DashboardStats | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [recent, setRecent] = useState<Task[]>([])
  const [monthly, setMonthly] = useState<MonthlyStat[]>([])
  const [disk, setDisk] = useState<DiskInfo | null>(null)
  const [analytics, setAnalytics] = useState<{ top_actors: { name: string; count: number }[]; top_tags: { name: string; count: number }[]; rating_dist: { bucket: string; count: number }[] } | null>(null)

  const load = () => {
    setStats(null); setError(null)
    api.dashboard.stats().then(setStats).catch((e) => setError(String((e as Error).message)))
    api.dashboard.recent(8).then(setRecent).catch(() => {})
    api.dashboard.monthly().then(setMonthly).catch(() => {})
    api.system.disk().then(setDisk).catch(() => {})
    api.v2.analytics().then(setAnalytics).catch(() => {})
  }

  useEffect(() => { load() }, [])

  if (error) return <div className="page"><ErrorEmpty message={error} onRetry={load} /></div>
  if (!stats) return <div className="page"><Loading /></div>
  const maxCount = Math.max(...monthly.map((m) => m.count), 1)

  return (
    <div className="page">
      <PageHead eyebrow="Overview" title={<>仪表<em>盘</em></>}
        sub="影片库整体概览：采集进度、收藏趋势与近期完成的作品。">
        <button className="btn btn--ghost btn--sm" onClick={() => location.reload()}><Icon.refresh />刷新</button>
      </PageHead>

      <div className="stat-row">
        <Stat num={stats.total_tasks} unit="部" label="总作品" trend={`已入库 ${stats.visited_tasks}`} />
        <Stat num={stats.favorite_count} unit="部" label="已收藏" trend={`${stats.actor_count} 位演员`} />
        <Stat num={stats.pending_tasks} unit="条" label="待处理" trend={`${stats.total_magnets} 个磁力`} down />
        <Stat num={stats.failed_tasks} unit="条" label="失败任务" trend={stats.db_size_mb ? `数据库 ${stats.db_size_mb} MB` : '—'} down />
      </div>

      {/* 磁盘空间卡片 */}
      {disk && disk.data && !disk.data.error && (
        <div className="card" style={{ marginBottom: 24, padding: '16px 20px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--t-body)' }}>磁盘空间</span>
            <span style={{ fontFamily: 'var(--ff-mono)', fontSize: 11, color: disk.data.free_percent < 10 ? 'var(--red)' : 'var(--t-mute)' }}>
              {disk.data.free_gb} GB 可用 ({disk.data.free_percent}%)
            </span>
          </div>
          <div style={{ height: 8, borderRadius: 4, background: 'var(--bg-surface)', overflow: 'hidden' }}>
            <div style={{
              height: '100%', borderRadius: 4,
              width: `${100 - disk.data.free_percent}%`,
              background: disk.data.free_percent < 10 ? 'var(--red)' : disk.data.free_percent < 25 ? 'var(--gold)' : 'var(--green, #4caf50)',
              transition: 'width .4s',
            }} />
          </div>
          <div style={{ display: 'flex', gap: 20, marginTop: 8, fontSize: 11, color: 'var(--t-faint)', fontFamily: 'var(--ff-mono)' }}>
            <span>图片缓存 {disk.images_size_mb} MB ({disk.images_count} 张)</span>
            <span>数据库 {disk.db_size_mb} MB</span>
            <span>已用 {disk.data.used_gb} / {disk.data.total_gb} GB</span>
          </div>
        </div>
      )}

      <div className="dash-grid">
        <div className="panel">
          <div className="panel-head">
            <div className="panel-title">近 12 月<em> 采集量</em></div>
            <Link to="/crawl" className="panel-link">查看爬取控制台 →</Link>
          </div>
          <div className="panel-body">
            {monthly.length === 0 ? <Empty title="暂无数据" /> : (
              <div className="bar-chart">
                {monthly.slice().reverse().map((m) => (
                  <div className="bar-col" key={m.month} title={`${m.month}: ${m.count} 部`}>
                    <div className="bar" style={{ height: `${(m.count / maxCount) * 100}%` }} />
                    <div className="bar-x">{m.month.slice(5)}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="panel">
          <div className="panel-head">
            <div className="panel-title">最近<em> 完成</em></div>
            <Link to="/library" className="panel-link">全部 →</Link>
          </div>
          <div className="panel-body">
            {recent.length === 0 ? <Empty title="暂无已完成任务" /> : recent.map((t) => (
              <div className="recent-item" key={t.id} onClick={() => nav(`/task/${t.id}`)}
                tabIndex={0} role="button" aria-label={`查看 ${t.video_code || '作品'} 详情`}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); nav(`/task/${t.id}`) } }}>
                <img className="recent-thumb" src={coverFileUrl(t.id)} alt={`${t.video_code || '作品'} 封面`}
                  onError={(e) => { e.currentTarget.style.visibility = 'hidden' }} />
                <div className="recent-meta">
                  <div className="recent-code">{t.video_code || '—'}</div>
                  <div className="recent-title">{t.title || '未命名'}</div>
                  <div className="recent-tags">
                    {t.is_favorite ? <span className="chip chip-rose">收藏</span> : null}
                    <span className="chip chip-green">已入库</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* F14: 分析维度 —— Top 演员 / 标签 / 评分分布 */}
      {analytics && (
        <div className="dash-grid" style={{ marginTop: 24 }}>
          <div className="panel">
            <div className="panel-head"><div className="panel-title">Top <em>演员</em></div></div>
            <div className="panel-body">
              {analytics.top_actors.length === 0 ? <Empty title="暂无数据" /> : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {analytics.top_actors.slice(0, 8).map((a, i) => {
                    const max = analytics.top_actors[0]?.count || 1
                    return (
                      <div key={a.name} style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 12 }}>
                        <span style={{ width: 18, color: 'var(--t-faint)', fontFamily: 'var(--ff-mono)' }}>{i + 1}</span>
                        <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.name}</span>
                        <div style={{ width: 80, height: 6, borderRadius: 3, background: 'var(--bg-surface)' }}>
                          <div style={{ width: `${(a.count / max) * 100}%`, height: '100%', borderRadius: 3, background: 'var(--gold)' }} />
                        </div>
                        <span style={{ width: 24, textAlign: 'right', fontFamily: 'var(--ff-mono)', color: 'var(--t-mute)' }}>{a.count}</span>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </div>
          <div className="panel">
            <div className="panel-head"><div className="panel-title">热门<em> 标签</em></div></div>
            <div className="panel-body">
              {analytics.top_tags.length === 0 ? <Empty title="暂无标签" /> : (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {analytics.top_tags.slice(0, 20).map((t) => (
                    <span key={t.name} className="chip" style={{ fontSize: 11 }}>{t.name} <b style={{ color: 'var(--gold)' }}>{t.count}</b></span>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function Stat({ num, unit, label, trend, down }: { num: number; unit: string; label: string; trend: string; down?: boolean }) {
  return (
    <div className="stat">
      <div className="stat-num">{num.toLocaleString()} <small>{unit}</small></div>
      <div className="stat-label">{label}</div>
      <div className={`stat-trend${down ? ' down' : ''}`}>{trend}</div>
    </div>
  )
}
