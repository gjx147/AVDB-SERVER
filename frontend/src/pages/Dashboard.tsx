import { useEffect, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { DashboardStats, Task, MonthlyStat, DiskInfo } from '../api/types'
import { RecentCarousel } from '../components/RecentCarousel'
import { PageHead, Loading, Empty, ErrorEmpty } from '../components/States'
import { Icon } from '../components/Icons'

export function Dashboard() {
  const [stats, setStats] = useState<DashboardStats | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [recent, setRecent] = useState<Task[]>([])
  const [monthly, setMonthly] = useState<MonthlyStat[]>([])
  const [disk, setDisk] = useState<DiskInfo | null>(null)
  const [analytics, setAnalytics] = useState<{ top_actors: { name: string; count: number }[]; top_tags: { name: string; count: number }[]; rating_dist: { bucket: string; count: number }[] } | null>(null)
  // 次级区块软错误（失败 ≠ 空数据，给行内重试）
  const [softErr, setSoftErr] = useState<{ recent?: boolean; monthly?: boolean; analytics?: boolean }>({})
  // 磁盘条生长动画 + 百分比 count-up
  const diskOk = !!(disk && disk.data && !disk.data.error)
  const [barOn, setBarOn] = useState(false)
  const [pctShown, setPctShown] = useState(0)

  const load = () => {
    setStats(null); setError(null); setSoftErr({})
    api.dashboard.stats().then(setStats).catch((e) => setError(String((e as Error).message)))
    api.dashboard.recent(12).then(setRecent).catch(() => setSoftErr((s) => ({ ...s, recent: true })))
    api.dashboard.monthly().then(setMonthly).catch(() => setSoftErr((s) => ({ ...s, monthly: true })))
    api.system.disk().then(setDisk).catch(() => {})
    api.v2.analytics().then(setAnalytics).catch(() => setSoftErr((s) => ({ ...s, analytics: true })))
  }

  useEffect(() => { load() }, [])

  useEffect(() => {
    if (!diskOk || !disk?.data) return
    setBarOn(false)
    setPctShown(0)
    const raf1 = requestAnimationFrame(() => setBarOn(true))
    const target = disk.data.free_percent
    const start = performance.now()
    let raf2 = 0
    const tick = (now: number) => {
      const k = Math.min(1, (now - start) / 800)
      setPctShown(Math.round(target * (1 - Math.pow(1 - k, 3))))
      if (k < 1) raf2 = requestAnimationFrame(tick)
    }
    raf2 = requestAnimationFrame(tick)
    return () => { cancelAnimationFrame(raf1); cancelAnimationFrame(raf2) }
  }, [diskOk, disk?.data?.free_percent])

  if (error) return <div className="page"><ErrorEmpty message={error} onRetry={load} /></div>
  if (!stats) return <div className="page"><Loading /></div>
  const maxCount = Math.max(...monthly.map((m) => m.count), 1)

  return (
    <div className="page">
      <PageHead eyebrow="Overview" title={<>仪表<em>盘</em></>}
        sub="灯已调暗——今晚，想先看谁？">
        <button className="btn btn--ghost btn--sm" onClick={load}><Icon.refresh />刷新</button>
      </PageHead>

      <HeatmapCard />
      <RecommendationsCard />
      <YearlyReportCard />

      <div className="stat-row">
        <Stat num={stats.total_tasks} unit="部" label="总作品" trend={`已入库 ${stats.visited_tasks}`} />
        <Stat num={stats.favorite_count} unit="部" label="已收藏" trend={`${stats.actor_count} 位演员`} />
        <Stat num={stats.pending_tasks} unit="条" label="待处理" trend={`${stats.total_magnets} 个磁力`} down />
        <Stat num={stats.failed_tasks} unit="条" label="失败任务" trend={stats.db_size_mb ? `数据库 ${stats.db_size_mb} MB` : '—'} down />
      </div>

      {/* 磁盘空间 + 近12月采集量 并排 */}
      <div className={`dash-top-grid${diskOk ? '' : ' dash-top-grid--solo'}`}>
        {diskOk && (
          <div className="card" style={{ padding: '16px 20px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--t-body)' }}>磁盘空间</span>
              <span style={{ fontFamily: 'var(--ff-mono)', fontSize: 11, color: disk!.data!.free_percent < 10 ? 'var(--red)' : 'var(--t-mute)' }}>
                {disk!.data!.free_gb} GB 可用 ({pctShown}%)
              </span>
            </div>
            <div style={{ height: 8, borderRadius: 4, background: 'var(--bg-surface)', overflow: 'hidden' }}>
              <div style={{
                height: '100%', borderRadius: 4,
                width: barOn ? `${100 - disk!.data!.free_percent}%` : '0%',
                background: disk!.data!.free_percent < 10 ? 'var(--red)' : disk!.data!.free_percent < 25 ? 'var(--gold)' : 'var(--green, #4caf50)',
                transition: 'width .4s',
              }} />
            </div>
            <div style={{ display: 'flex', gap: 20, marginTop: 8, fontSize: 11, color: 'var(--t-faint)', fontFamily: 'var(--ff-mono)' }}>
              <span>图片缓存 {disk!.images_size_mb} MB ({disk!.images_count} 张)</span>
              <span>数据库 {disk!.db_size_mb} MB</span>
              <span>已用 {disk!.data!.used_gb} / {disk!.data!.total_gb} GB</span>
            </div>
          </div>
        )}

        <div className="panel">
          <div className="panel-head">
            <div className="panel-title">近 12 月<em> 采集量</em></div>
            <Link to="/crawl" className="panel-link">查看爬取控制台 →</Link>
          </div>
          <div className="panel-body">
            {softErr.monthly ? <InlineErr onRetry={load} /> :
              monthly.length === 0 ? <Empty title="暂无数据" /> : (
              <div className="bar-chart bar-chart--sm">
                {monthly.slice().reverse().map((m) => (
                  <div className="bar-col" key={m.month} data-num={`${m.month} · ${m.count}部`} title={`${m.month}: ${m.count} 部`}>
                    <div className="bar" style={{ height: `${(m.count / maxCount) * 100}%` }} />
                    <div className="bar-x">{m.month.slice(5)}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* 最近完成 放大全宽 */}
      <div className="panel recent-panel">
        <div className="panel-head">
          <div className="panel-title">最近<em> 完成</em></div>
          <Link to="/library" className="panel-link">全部 →</Link>
        </div>
        <div className="panel-body">
          {softErr.recent ? <InlineErr onRetry={load} /> :
            recent.length === 0 ? <Empty title="暂无已完成任务" /> : <RecentCarousel tasks={recent} />}
        </div>
      </div>

      {/* F14: 分析维度 —— Top 演员 / 标签 / 评分分布 */}
      {softErr.analytics ? (
        <div style={{ marginTop: 24 }}><InlineErr onRetry={load} /></div>
      ) : analytics && (
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

function HeatmapCard() {
  const [heat, setHeat] = useState<Record<string, { favorites: number; downloads: number }> | null>(null)
  useEffect(() => {
    api.activityHeatmap(180).then((r) => setHeat(r.days)).catch(() => setHeat({}))
  }, [])
  const items = heat ? Object.entries(heat).sort((a, b) => a[0].localeCompare(b[0])).slice(-180) : []
  if (!heat || items.length === 0) return null
  const max = Math.max(...items.map(([, v]) => v.favorites + v.downloads), 1)
  const cells: ReactNode[] = []
  for (const [d, v] of items) {
    const level = Math.round(((v.favorites + v.downloads) / max) * 4)
    const colors = ['#d1fae5', '#6ee7b7', '#10b981', '#047857']
    cells.push(
      <div key={d} title={`${d} 收藏 ${v.favorites} · 下载 ${v.downloads}`}
        style={{
          width: 11, height: 11, borderRadius: 2,
          background: level === 0 ? 'var(--bg-raised, #f3f4f6)' : colors[Math.min(level - 1, 3)],
          opacity: 0.9,
        }} />
    )
  }
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>活动热力 · 最近 180 天</div>
        <div style={{ fontSize: 11, color: 'var(--t-mute)' }}>收藏 / 下载行为（悬停查看详情）</div>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>{cells}</div>
    </div>
  )
}

function Stat({ num, unit, label, trend, down }: { num: number; unit: string; label: string; trend: string; down?: boolean }) {
  return (
    <div className="stat">
      <div className="stat-num">{(num ?? 0).toLocaleString()} <small>{unit}</small></div>
      <div className="stat-label">{label}</div>
      <div className={`stat-trend${down ? ' down' : ''}`}>{trend}</div>
    </div>
  )
}

/** 次级区块加载失败：行内提示 + 重试 */
function InlineErr({ onRetry }: { onRetry: () => void }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 12, color: 'var(--red)', padding: '14px 2px' }}>
      <span>该区块加载失败</span>
      <button className="btn btn--ghost btn--sm" onClick={onRetry}><Icon.refresh />重试</button>
    </div>
  )
}

function RecommendationsCard() {
  const [recs, setRecs] = useState<{ task_id: number; video_code: string | null; title: string | null; rating: number | null; poster_url: string | null; score: number | null; match: string[] }[] | null>(null)
  const [reason, setReason] = useState<Record<number, { text: string; loading: boolean }>>({})
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    api.recommendations().then((r) => setRecs(r.items)).catch(() => setRecs([]))
  }, [])
  if (!recs || recs.length === 0) return null
  const genReason = async (t: { task_id: number; video_code: string | null }) => {
    setReason((p) => ({ ...p, [t.task_id]: { text: '', loading: true } }))
    try {
      const r = await api.recommendReason(t.task_id)
      setReason((p) => ({ ...p, [t.task_id]: { text: r.reason || '（未能生成理由）', loading: false } }))
    } catch {
      setReason((p) => ({ ...p, [t.task_id]: { text: '生成失败', loading: false } }))
    }
  }
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>为你推荐</div>
        <div style={{ fontSize: 11, color: 'var(--t-mute)' }}>基于收藏与已看偏好 · AI 理由按需生成</div>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {recs.map((t) => (
          <div key={t.task_id} style={{
            display: 'flex', gap: 10, alignItems: 'center', padding: '6px 8px',
            border: '1px solid var(--line, #eee)', borderRadius: 8, fontSize: 12,
          }}>
            <div style={{ flex: '1 1 auto', minWidth: 0 }}>
              <div style={{ fontWeight: 600 }}>{t.video_code || '—'} {t.rating ? <span style={{ color: 'var(--gold, #d97706)' }}>{t.rating}</span> : null}</div>
              <div style={{ color: 'var(--t-mute)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.title || ''}</div>
              {t.match.length > 0 && (
                <div style={{ display: 'flex', gap: 4, marginTop: 2, flexWrap: 'wrap' }}>
                  {t.match.map((m) => <span key={m} style={{ fontSize: 10, background: 'var(--bg-raised, #f3f4f6)', padding: '1px 6px', borderRadius: 99 }}>{m}</span>)}
                </div>
              )}
              {reason[t.task_id]?.text && (
                <div style={{ marginTop: 4, fontSize: 11, color: 'var(--t-mute)', fontStyle: 'italic' }}>「{reason[t.task_id].text}」</div>
              )}
            </div>
            <button className="btn btn--ghost btn--sm" disabled={reason[t.task_id]?.loading}
              onClick={() => genReason(t)} style={{ flex: 'none' }}>
              {reason[t.task_id]?.loading ? '生成中…' : '推荐理由'}
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}

function YearlyReportCard() {
  const [rep, setRep] = useState<{ year: number; stats: { added: number; downloads: number; favorites: number }; top_actors: { name: string; count: number }[]; top_tags: { name: string; count: number }[]; top_makers: { name: string; count: number }[]; monthly: number[] } | null>(null)
  useEffect(() => {
    api.yearlyReport().then(setRep).catch(() => setRep(null))
  }, [])
  if (!rep || rep.stats.added === 0) return null
  const maxMonth = Math.max(...rep.monthly, 1)
  const monthLabels = ['1月', '2月', '3月', '4月', '5月', '6月', '7月', '8月', '9月', '10月', '11月', '12月']
  const topList = (arr: { name: string; count: number }[]) =>
    arr.length === 0 ? <span style={{ color: 'var(--t-faint)', fontSize: 11 }}>暂无</span> : (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
        {arr.map((x) => (
          <div key={x.name} style={{ display: 'flex', justifyContent: 'space-between', gap: 10, fontSize: 11 }}>
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{x.name}</span>
            <span style={{ color: 'var(--t-mute)' }}>{x.count}</span>
          </div>
        ))}
      </div>
    )
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>年度回顾 · {rep.year}</div>
        <div style={{ fontSize: 11, color: 'var(--t-mute)' }}>入库 {rep.stats.added} · 下载 {rep.stats.downloads} · 收藏 {rep.stats.favorites}</div>
      </div>
      <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap' }}>
        <div style={{ flex: '1 1 220px', minWidth: 180 }}>
          <div style={{ fontSize: 11, color: 'var(--t-mute)', marginBottom: 4 }}>月度入库分布</div>
          <div style={{ display: 'flex', gap: 3, alignItems: 'end', height: 52 }}>
            {rep.monthly.map((c, i) => (
              <div key={i} title={`${monthLabels[i]} 入库 ${c}`}
                style={{ flex: 1, height: Math.max(3, (c / maxMonth) * 48), background: 'var(--gold, #d97706)', borderRadius: 2, opacity: 0.85 }} />
            ))}
          </div>
          <div style={{ display: 'flex', gap: 3, fontSize: 8, color: 'var(--t-faint)', marginTop: 2 }}>
            {monthLabels.map((m, i) => <span key={m} style={{ flex: 1, textAlign: 'center' }}>{i + 1}</span>)}
          </div>
        </div>
        <div style={{ flex: '1 1 160px', minWidth: 130 }}>
          <div style={{ fontSize: 11, color: 'var(--t-mute)', marginBottom: 4 }}>年度 Top 演员</div>
          {topList(rep.top_actors)}
        </div>
        <div style={{ flex: '1 1 160px', minWidth: 130 }}>
          <div style={{ fontSize: 11, color: 'var(--t-mute)', marginBottom: 4 }}>年度 Top 标签</div>
          {topList(rep.top_tags)}
        </div>
        <div style={{ flex: '1 1 160px', minWidth: 130 }}>
          <div style={{ fontSize: 11, color: 'var(--t-mute)', marginBottom: 4 }}>年度 Top 厂商</div>
          {topList(rep.top_makers)}
        </div>
      </div>
    </div>
  )
}
