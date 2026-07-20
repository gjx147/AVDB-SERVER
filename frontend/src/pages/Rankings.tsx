import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, coverFileUrl } from '../api/client'
import type { Ranking, RankType, Task } from '../api/types'
import { PosterCard } from '../components/PosterCard'
import { QueueOverlay } from '../components/QueueOverlay'
import { PageHead, Loading, Empty, ErrorEmpty } from '../components/States'
import { Icon } from '../components/Icons'
import { useStore } from '../store/useStore'

const TABS: { key: RankType; label: string }[] = [
  { key: 'daily', label: '日榜' },
  { key: 'weekly', label: '周榜' },
  { key: 'monthly', label: '月榜' },
  { key: 'actor', label: '演员月榜' },
]

/** 扩展 Task，携带排行榜特有展示字段 */
type RankingTask = Task & {
  _ranking_id: number
  _task_id: number | null
  _rank_position: number
  _views: number
  _is_in_library: boolean
}

/** Ranking → RankingTask 适配（PosterCard 需要 Task 类型）。
 * 优先使用后端 join 的 task_* 真实数据（番号/标题/海报/缩略图），
 * 没有则用 ranking 概览数据 + cover_url fallback。
 */
const toTask = (r: Ranking, isActor = false): RankingTask => {
  // 从 cover_url 推导出竖版预览图 URL
  // covers/eb/EbO6md.jpg → samples/eb/EbO6md_s_0.jpg
  let fallbackThumbs: string | null = null
  if (r.cover_url) {
    const m = r.cover_url.match(/\/covers\/(.+\/.+)\.jpg/)
    if (m) {
      fallbackThumbs = JSON.stringify([`https://c0.jdbstatic.com/samples/${m[1]}_s_0.jpg`])
    }
  }
  return {
    id: r.task_id || 0,
    list_source_id: 0,
    url: '',
    // actor 类型：演员不是 task，无"待处理"概念，用 visited 避免显示"待处理"标签
    status: (isActor ? 'visited' : (r.task_status || (r.is_in_library ? 'visited' : 'pending'))) as Task['status'],
    retry_count: 0,
    best_magnet: null,
    magnets_json: null,
    // 优先用 task 的真实番号/标题，没有则用 ranking 概览
    video_code: r.task_video_code || r.video_code,
    title: r.task_title || r.title,
    poster_url: r.task_poster_url || r.cover_url || null,
    thumbnail_urls: r.task_thumbnail_urls || fallbackThumbs,
    synopsis: null,
    description: null,
    actors: null,
    tags: null,
    release_date: null,
    duration: null,
    director: null,
    maker: null,
    label: null,
    series: null,
    rating: r.score || null,
    file_size: null,
    is_favorite: 0 as 0 | 1,
    favorite_at: null,
    note: null,
    error_message: null,
    created_at: null,
    updated_at: null,
    _ranking_id: r.id,
    _task_id: r.task_id,
    _rank_position: r.rank_position,
    _views: r.views,
    _is_in_library: r.is_in_library,
  }
}

export function Rankings() {
  const nav = useNavigate()
  const [tab, setTab] = useState<RankType>('daily')
  const [list, setList] = useState<Ranking[] | null>(null)
  const [latest, setLatest] = useState<Record<string, string[]>>({})
  const [view, setView] = useState<'grid' | 'row'>('grid')
  const [searchQ, setSearchQ] = useState('')
  const [filterStatus, setFilterStatus] = useState<'all' | 'visited' | 'pending'>('all')
  const [queueRunning, setQueueRunning] = useState(false)
  const [queueInfo, setQueueInfo] = useState<{ current: number; total: number; current_video_code: string | null; stage: string; done: number[]; failed: number[] } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [maxPages, setMaxPages] = useState<number>(5)
  const toastOk = useStore((s) => s.toastOk)
  const toastErr = useStore((s) => s.toastErr)

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const reqSeqRef = useRef(0)  // P1#6: 标签切换竞态防护
  useEffect(() => { return () => { if (pollRef.current) clearInterval(pollRef.current) } }, [])

  // P3：检查队列状态
  useEffect(() => {
    api.images.queueStatus().then((s) => { if (s.running) { setQueueRunning(true); setQueueInfo(s) } }).catch(() => {})
    // 读取设置中的默认最大页数
    api.settings.get().then((s) => { setMaxPages(s.max_pages_default || 5) }).catch(() => {})
  }, [])

  useEffect(() => { api.rankingsNew.dates().then(setLatest).catch(() => {}) }, [])

  const load = useCallback(async (t: RankType) => {
    const reqId = ++reqSeqRef.current  // P1#6: 丢弃旧标签的响应
    setTab(t)
    setList(null)
    setSearchQ('')
    setFilterStatus('all')
    setError(null)
    try {
      // 只读取排行榜数据（由 scraper ranking 命令完整爬取后写入）
      const data = await api.rankings.list(t)
      if (reqId !== reqSeqRef.current) return
      setList(data)
    } catch (e) {
      setError(String((e as Error).message))
      setList([])
    }
  }, [toastErr])
  useEffect(() => { load('daily') }, [load])

  const crawl = async () => {
    try { await api.rankings.crawl(tab, maxPages); toastOk(`已启动 ${tab} 排行榜爬取（${maxPages}页）`) } catch (e) { toastErr(String((e as Error).message)) }
  }

  const openRank = (r: Ranking) => {
    // actor 类型：跳转影视库按演员名筛选（演员不是 task，无详情页）
    if (tab === 'actor') {
      const name = r.task_video_code || r.video_code || ''
      if (name) {
        nav(`/library?q=${encodeURIComponent(name)}`)
      } else {
        toastErr('无演员名')
      }
      return
    }
    if (r.task_id) {
      nav(`/task/${r.task_id}`)
    } else {
      toastErr('该排行榜条目尚未爬取详情，请先刷新排行')
    }
  }

  // ── 前端过滤 ──
  const isActorTab = tab === 'actor'
  const tasks: RankingTask[] = (list || []).map(r => toTask(r, isActorTab))
  const filtered = tasks.filter((t) => {
    const q = searchQ.trim().toLowerCase()
    if (q && !(t.video_code || '').toLowerCase().includes(q)) return false
    if (filterStatus === 'visited' && !t._is_in_library) return false
    if (filterStatus === 'pending' && t._is_in_library) return false
    return true
  })

  return (
    <div className="page">
      <PageHead eyebrow="Rankings" title={<>排<em>行榜</em></>}
        sub="每日、每周、每月的热门作品排行。系统后台自动提取元数据和海报。">
        <button className="btn btn--ghost btn--sm" onClick={crawl}><Icon.refresh />刷新排行</button>
      </PageHead>

      {/* Toolbar：Tab + 搜索 + 筛选 + 视图切换 */}
      <div className="gallery-toolbar">
        <div className="seg">
          {TABS.map((t) => <button key={t.key} className={tab === t.key ? 'on' : ''} onClick={() => load(t.key)}>{t.label}</button>)}
        </div>
        <div className="search">
          <Icon.search />
          <input placeholder="搜索番号…" value={searchQ}
            onChange={(e) => setSearchQ(e.target.value)} />
        </div>
        <select className="select" value={filterStatus} onChange={(e) => setFilterStatus(e.target.value as typeof filterStatus)} aria-label="筛选状态">
          <option value="all">全部状态</option>
          <option value="visited">已入库</option>
          <option value="pending">待处理</option>
        </select>
        <div className="seg">
          <button className={view === 'grid' ? 'on' : ''} onClick={() => setView('grid')}>画廊</button>
          <button className={view === 'row' ? 'on' : ''} onClick={() => setView('row')}>列表</button>
        </div>
        {latest[tab]?.[0] && <span style={{ fontSize: 11, color: 'var(--t-faint)', whiteSpace: 'nowrap' }}>更新于 {latest[tab][0]}</span>}
      </div>

      {error ? <ErrorEmpty message={error} onRetry={() => load(tab)} /> :
       list === null ? <Loading /> : list.length === 0 ? (
        <Empty icon="○" title="暂无排行数据" sub="系统启动后会自动爬取，或点击右上角刷新。" />
      ) : filtered.length === 0 ? (
        <Empty icon="○" title="无匹配结果" sub="尝试更换筛选条件或搜索关键词。" />
      ) : view === 'grid' ? (
        <div className="gallery">
          {filtered.map((t) => {
            const r = list!.find((x) => x.id === t._ranking_id)!
            return <PosterCard key={t._ranking_id} task={t} onClick={() => openRank(r)} centerImage={isActorTab} />
          })}
        </div>
      ) : (
        <div className="card">
          {filtered.map((t) => {
            const r = list!.find((x) => x.id === t._ranking_id)!
            return (
              <div className="row-item" key={t._ranking_id} onClick={() => openRank(r)}>
                <img className="row-thumb" src={coverFileUrl(t._task_id || 0)}
                  alt={`${t.video_code || '作品'} 封面`}
                  onError={(e) => { const r = t.poster_url || (() => { try { return JSON.parse(t.thumbnail_urls || '[]')[0] } catch { return null } })(); if (r && e.currentTarget.src !== r) { e.currentTarget.src = r } else { e.currentTarget.style.visibility = 'hidden' } }} />
                <div>
                  <div className="row-code">#{t._rank_position} {t.video_code || '—'}</div>
                  <div className="row-title">{t.title || '未命名'}</div>
                </div>
                <div className="row-tags">
                  {t._is_in_library ? (
                    <span className="chip chip-green">已入库</span>
                  ) : (
                    <span className="chip chip-amber">待处理</span>
                  )}
                </div>
                <div style={{ fontFamily: 'var(--ff-mono)', fontSize: 11, color: 'var(--t-faint)' }}>
                  {t.rating ? `★ ${t.rating}` : ''}{t._views > 0 ? ` · ${t._views.toLocaleString()}` : ''}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* P3：队列进度条 */}
      {queueRunning && queueInfo && <QueueOverlay info={queueInfo} />}
    </div>
  )
}
