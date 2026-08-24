import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, coverFileUrl, withImageAuth } from '../api/client'
import type { Ranking, RankType, Task } from '../api/types'
import { PosterCard } from '../components/PosterCard'
import { QueueOverlay } from '../components/QueueOverlay'
import { PageHead, Empty, ErrorEmpty } from '../components/States'
import { SkeletonGallery } from '../components/Skeleton'
import { Icon } from '../components/Icons'
import { useStore } from '../store/useStore'

const TABS: { key: RankType; label: string }[] = [
  { key: 'daily', label: '日榜' },
  { key: 'weekly', label: '周榜' },
  { key: 'monthly', label: '月榜' },
  { key: 'actor', label: '演员月榜' },
]

/** 刷新顺序：日榜 → 周榜 → 月榜 → 演员月榜（scraper 全局锁，必须逐个等） */
const REFRESH_ORDER: RankType[] = ['daily', 'weekly', 'monthly', 'actor']

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

/** 扩展 Task，携带排行榜特有展示字段 */
type RankingTask = Task & {
  _ranking_id: number
  _task_id: number | null
  _rank_position: number
  _views: number
  _is_in_library: boolean
  _actor_id?: number | null
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
    _actor_id: r.actor_id ?? null,
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
  const [inLib, setInLib] = useState<'all' | 'in' | 'out'>('all')
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [refreshing, setRefreshing] = useState<RankType | null>(null)  // 正在按序刷新的榜单
  const [batchBusy, setBatchBusy] = useState(false)
  /** 每个榜单等待爬取完成的时长上限（分钟），前端手动配置，保存在本机浏览器 */
  const [waitLimitMin, setWaitLimitMin] = useState<number>(() => {
    const v = parseInt(localStorage.getItem('rankWaitLimitMin') ?? '', 10)
    return Number.isFinite(v) && v > 0 ? v : 60
  })
  const setWaitLimit = (v: number) => {
    const n = Math.max(1, Math.min(2880, Math.round(v)))  // 1 分钟 ~ 48 小时
    setWaitLimitMin(n)
    localStorage.setItem('rankWaitLimitMin', String(n))
  }
  const toastOk = useStore((s) => s.toastOk)
  const toastErr = useStore((s) => s.toastErr)
  const confirmBox = useStore((s) => s.confirm)

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const reqSeqRef = useRef(0)  // P1#6: 标签切换竞态防护
  useEffect(() => { return () => { if (pollRef.current) clearInterval(pollRef.current) } }, [])

  // P3：检查队列状态
  useEffect(() => {
    api.images.queueStatus().then((s) => { if (s.running) { setQueueRunning(true); setQueueInfo(s) } }).catch(() => {})
  }, [])

  useEffect(() => { api.rankingsNew.dates().then(setLatest).catch(() => {}) }, [])

  const load = useCallback(async (t: RankType) => {
    const reqId = ++reqSeqRef.current  // P1#6: 丢弃旧标签的响应
    setTab(t)
    setList(null)
    setSearchQ('')
    setFilterStatus('all')
    setSelected(new Set())
    setError(null)
    try {
      // 只读取排行榜数据（由 scraper ranking 命令完整爬取后写入）；非演员榜支持在库筛选
      const data = await api.rankings.list(t, undefined, 0, 100,
        t === 'actor' ? undefined : (inLib === 'all' ? undefined : inLib === 'in'))
      if (reqId !== reqSeqRef.current) return
      setList(data)
    } catch (e) {
      setError(String((e as Error).message))
      setList([])
    }
  }, [toastErr, inLib])
  // load 含 inLib 依赖会重建：挂载/切筛选都用 ref 取最新 load，避免 effect 把 tab 弹回 daily
  const loadRef = useRef(load)
  useEffect(() => { loadRef.current = load }, [load])
  useEffect(() => { loadRef.current('daily') }, [])
  const inLibTouched = useRef(false)
  useEffect(() => {
    if (!inLibTouched.current) { inLibTouched.current = true; return }  // 跳过首渲染
    loadRef.current(tab)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inLib])

  /** 等待 scraper 全局锁空闲（后端同一时刻只允许一个爬取进程）。
   *  每次触发后都要等它爬完再触发下一个，保证日→周→月→演员严格按序。
   *  等待上限由前端配置（waitLimitMin 分钟），超时返回 false。 */
  const waitIdle = async () => {
    // 上限越长轮询越疏（3s~30s 自适应），避免长时间等待时轰炸状态接口
    const intervalMs = Math.max(3000, Math.min(30000, waitLimitMin * 100))
    const polls = Math.ceil((waitLimitMin * 60_000) / intervalMs)
    for (let i = 0; i < polls; i++) {
      try {
        const s = await api.crawl.status()
        if (!s.running) return true
      } catch { /* 状态查询失败不中断，继续等 */ }
      await sleep(intervalMs)
    }
    return false
  }

  /** 一键刷新：四个榜单按 日→周→月→演员 顺序逐个爬取，全程等待各自完成 */
  const refreshAll = async () => {
    if (refreshing) return
    try {
      for (const t of REFRESH_ORDER) {
        setRefreshing(t)
        if (!(await waitIdle())) { toastErr(`等待其他爬取任务超过 ${waitLimitMin} 分钟，刷新中止`); break }
        try {
          await api.rankings.crawl(t)
        } catch (e) {
          // 409 锁竞争：等空闲后重试一次
          if (!String((e as Error).message).includes('已有爬取任务')) throw e
          if (!(await waitIdle())) { toastErr(`${TABS.find(x => x.key === t)?.label} 等待超过 ${waitLimitMin} 分钟，刷新中止`); break }
          await api.rankings.crawl(t)
        }
        if (!(await waitIdle())) { toastErr(`${TABS.find(x => x.key === t)?.label} 等待超过 ${waitLimitMin} 分钟，刷新中止`); break }
      }
      toastOk('四榜已按序刷新完成（日榜→周榜→月榜→演员月榜）')
      api.rankingsNew.dates().then(setLatest).catch(() => {})
      load(tab)
    } catch (e) {
      toastErr(String((e as Error).message))
    } finally {
      setRefreshing(null)
    }
  }

  const openRank = (r: Ranking) => {
    // actor 类型：有 actor_id 进演员详情页，否则 fallback 到影视库按演员名筛选
    if (tab === 'actor') {
      if (r.actor_id) {
        nav(`/actor/${r.actor_id}`)
        return
      }
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

  // ── 多选 ──
  const toggleSel = (id: number) => {
    setSelected((prev) => {
      const n = new Set(prev)
      n.has(id) ? n.delete(id) : n.add(id)
      return n
    })
  }
  const allSelected = list !== null && list.length > 0 && list.every((r) => selected.has(r.id))
  const toggleAll = () => {
    if (!list) return
    setSelected((prev) => {
      const allOnPage = list.every((r) => prev.has(r.id))
      const n = new Set(prev)
      if (allOnPage) list.forEach((r) => n.delete(r.id))
      else list.forEach((r) => n.add(r.id))
      return n
    })
  }
  const selRankings = (list || []).filter((r) => selected.has(r.id))

  /** 批量操作（影片榜）：入库 / 收藏 / 删除 */
  const batch = async (kind: 'add' | 'favorite' | 'delete' | 'follow') => {
    const sels = selRankings
    if (!sels.length) return
    setBatchBusy(true)
    try {
      if (kind === 'add') {
        const noTask = sels.filter((r) => !r.task_id)
        if (!noTask.length) { toastErr('所选条目均已入库，无需再入库'); return }
        const r = await api.rankings.batchAddTasks(noTask.map((x) => x.id))
        toastOk(`已入库 ${r.added ?? r.results?.length ?? noTask.length} 项${r.skipped ? `，跳过 ${r.skipped} 项` : ''}`)
      } else if (kind === 'favorite') {
        const ids = sels.filter((r) => r.task_id).map((r) => r.task_id as number)
        if (!ids.length) { toastErr('所选条目尚未入库，请先批量入库'); return }
        await api.tasks.batchFavorite(ids)
        toastOk(`已收藏 ${ids.length} 项`)
      } else if (kind === 'delete') {
        const ids = sels.filter((r) => r.task_id).map((r) => r.task_id as number)
        if (!ids.length) { toastErr('所选条目尚未入库，无任务可删'); return }
        const ok = await confirmBox('批量删除', `将删除 ${ids.length} 个任务及其关联图片缓存，不可恢复。确定继续？`)
        if (!ok) return
        await api.tasks.batchDelete(ids)
        toastOk(`已删除 ${ids.length} 项`)
      } else {
        // actor 榜：批量关注（逐个创建 actor 订阅）
        let n = 0
        for (const r of sels) {
          if (!r.actor_id) continue
          try { await api.actors.follow(r.actor_id); n++ } catch { /* 单个失败不中断 */ }
        }
        toastOk(`已关注 ${n} 位演员`)
      }
      setSelected(new Set())
      load(tab)
    } catch (e) {
      toastErr(String((e as Error).message))
    } finally {
      setBatchBusy(false)
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
  // 领奖台：无搜索无筛选时，前三名单独放大展示；主画廊从第 4 名起平铺
  const showPodium = view === 'grid' && !searchQ.trim() && filterStatus === 'all' && inLib === 'all' && filtered.length > 3
  const podiumTasks = showPodium ? filtered.slice(0, 3) : []
  const restTasks = showPodium ? filtered.slice(3) : filtered

  const refreshingLabel = refreshing ? TABS.find(x => x.key === refreshing)?.label : ''

  return (
    <div className="page">
      <PageHead eyebrow="Rankings" title={<>排<em>行榜</em></>}
        sub="今夜最热的她们，已经按心动值排好了队。">
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--t-mute)', whiteSpace: 'nowrap', cursor: 'pointer' }}
          title="每个榜单等待爬取完成的时长上限，超时则跳过并中止本次刷新（仅保存在本机浏览器）">
          每榜等待上限
          <input className="input" type="number" min={1} max={2880} value={waitLimitMin}
            style={{ width: 68, padding: '6px 8px', textAlign: 'center' }}
            onChange={(e) => setWaitLimit(+e.target.value)}
            onBlur={(e) => { if (!e.target.value) setWaitLimit(60) }} />
          分钟
        </label>
        <button className="btn btn--ghost btn--sm" onClick={refreshAll} disabled={!!refreshing}>
          <Icon.refresh />{refreshing ? `刷新中 · ${refreshingLabel}…` : '刷新排行'}
        </button>
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
        <select className="select" value={inLib} disabled={tab === 'actor'}
          onChange={(e) => setInLib(e.target.value as 'all' | 'in' | 'out')}
          aria-label="媒体库筛选" title={tab === 'actor' ? '演员榜不支持在库筛选' : undefined}>
          <option value="all">全部媒体库状态</option>
          <option value="in">✓ 在媒体库</option>
          <option value="out">✗ 不在媒体库</option>
        </select>
        <div className="seg">
          <button className={view === 'grid' ? 'on' : ''} onClick={() => setView('grid')}>画廊</button>
          <button className={view === 'row' ? 'on' : ''} onClick={() => setView('row')}>列表</button>
        </div>
        {list && list.length > 0 && (
          <button className="btn btn--ghost btn--sm" onClick={toggleAll}>{allSelected ? '取消全选' : '全选本页'}</button>
        )}
        {latest[tab]?.[0] && <span style={{ fontSize: 11, color: 'var(--t-faint)', whiteSpace: 'nowrap' }}>更新于 {latest[tab][0]}</span>}
      </div>

      {error ? <ErrorEmpty message={error} onRetry={() => load(tab)} /> :
       list === null ? <SkeletonGallery /> : list.length === 0 ? (
        <Empty icon="○" title="暂无排行数据" sub="系统启动后会自动爬取，或点击右上角刷新。" />
      ) : filtered.length === 0 ? (
        <Empty icon="○" title="无匹配结果" sub="尝试更换筛选条件或搜索关键词。" />
      ) : view === 'grid' ? (
        isActorTab ? (
          // 演员月榜：用演员库的 .actor-grid 样式（1:1 正方形头像 + 名字图下）
          <>
          {podiumTasks.length > 0 && (
            <div className="podium">
              {podiumTasks.map((t) => {
                const r = list!.find((x) => x.id === t._ranking_id)!
                return (
                  <div className="actor" key={t._ranking_id} tabIndex={0} role="button"
                    aria-label={`查看演员 ${t.video_code || ''}`}
                    onClick={() => openRank(r)}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openRank(r) } }}
                    style={{ cursor: 'pointer' }}>
                    <div className="actor-photo">
                      <span className={`crown-badge cb-${t._rank_position}`}>
                        <span className="crown-crown">♛</span>
                        <span className="crown-num">{t._rank_position}</span>
                      </span>
                      <div className={`actor-check${selected.has(t._ranking_id) ? ' on' : ''}`} role="checkbox"
                        style={{ left: 'auto', right: 6 }}
                        aria-checked={selected.has(t._ranking_id)} tabIndex={0}
                        onClick={(e) => { e.stopPropagation(); toggleSel(t._ranking_id) }}
                        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); toggleSel(t._ranking_id) } }}>
                        {selected.has(t._ranking_id) ? '✓' : ''}
                      </div>
                      {t.poster_url
                        ? <img src={t.poster_url} alt={t.video_code || ''} referrerPolicy="no-referrer"
                            onError={(e) => { e.currentTarget.style.visibility = 'hidden' }} />
                        : <div style={{ width: '100%', height: '100%', background: 'var(--bg-page)' }} />}
                    </div>
                    <div className="actor-name">{t.video_code || '—'}</div>
                    <div className="actor-count">第 {t._rank_position} 位 · {t._views} 次浏览</div>
                  </div>
                )
              })}
            </div>
          )}
          <div className="actor-grid">
            {restTasks.map((t) => {
              const r = list!.find((x) => x.id === t._ranking_id)!
              return (
                <div className="actor" key={t._ranking_id} tabIndex={0} role="button"
                  aria-label={`查看演员 ${t.video_code || ''}`}
                  onClick={() => openRank(r)}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openRank(r) } }}
                  style={{ cursor: 'pointer' }}>
                  <div className="actor-photo">
                    {t._rank_position <= 10 && <span className="rank-badge">{t._rank_position}</span>}
                    <div className={`actor-check${selected.has(t._ranking_id) ? ' on' : ''}`} role="checkbox"
                      style={{ left: 'auto', right: 6 }}
                      aria-checked={selected.has(t._ranking_id)} tabIndex={0}
                      onClick={(e) => { e.stopPropagation(); toggleSel(t._ranking_id) }}
                      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); toggleSel(t._ranking_id) } }}>
                      {selected.has(t._ranking_id) ? '✓' : ''}
                    </div>
                    {t.poster_url
                      ? <img src={t.poster_url} alt={t.video_code || ''} referrerPolicy="no-referrer"
                          onError={(e) => { e.currentTarget.style.visibility = 'hidden' }} />
                      : <div style={{ width: '100%', height: '100%', background: 'var(--bg-page)' }} />}
                  </div>
                  <div className="actor-name">{t.video_code || '—'}</div>
                  <div className="actor-count">第 {t._rank_position} 位 · {t._views} 次浏览</div>
                </div>
              )
            })}
          </div>
          </>
        ) : (
          // 影片榜：前三领奖台放大 + 主画廊平铺（Top10 挂角标）
          <>
          {podiumTasks.length > 0 && (
            <div className="podium">
              {podiumTasks.map((t) => {
                const r = list!.find((x) => x.id === t._ranking_id)!
                return <PosterCard key={t._ranking_id} task={t} rank={t._rank_position}
                  selected={selected.has(t._ranking_id)} selectable onToggle={() => toggleSel(t._ranking_id)}
                  onClick={() => openRank(r)} />
              })}
            </div>
          )}
          <div className="gallery">
            {restTasks.map((t) => {
              const r = list!.find((x) => x.id === t._ranking_id)!
              return <PosterCard key={t._ranking_id} task={t} rank={t._rank_position <= 10 ? t._rank_position : undefined}
                selected={selected.has(t._ranking_id)} selectable onToggle={() => toggleSel(t._ranking_id)}
                onClick={() => openRank(r)} />
            })}
          </div>
          </>
        )
      ) : (
        <div className="card">
          {filtered.map((t) => {
            const r = list!.find((x) => x.id === t._ranking_id)!
            return (
              <div className="row-item" key={t._ranking_id} onClick={() => openRank(r)}
                role="button" tabIndex={0}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openRank(r) } }}>
                <img className="row-thumb" referrerPolicy="no-referrer"
                  src={t._task_id ? withImageAuth(coverFileUrl(t._task_id)) : (t.poster_url || '')}
                  alt={`${t.video_code || '作品'} 封面`}
                  onError={(e) => { const r = t.poster_url || (() => { try { return JSON.parse(t.thumbnail_urls || '[]')[0] } catch { return null } })(); if (r && e.currentTarget.src !== r) { e.currentTarget.src = r } else { e.currentTarget.style.visibility = 'hidden' } }} />
                <div>
                  <div className="row-code">
                    {t._rank_position <= 3 ? (
                      <span className={`rank-badge rank-badge--inline rb-${t._rank_position}`}>{t._rank_position}</span>
                    ) : <span>#{t._rank_position} </span>}
                    {t.video_code || '—'}
                  </div>
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
                  {t.rating ? `♥ ${t.rating}` : ''}{t._views > 0 ? ` · ${t._views.toLocaleString()}` : ''}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* 批量操作栏 */}
      <div className={`batchbar${selected.size ? ' show' : ''}`}>
        <span className="sel-count">已选 {selected.size} 项</span>
        {isActorTab ? (
          <button className="btn btn--gold btn--sm" onClick={() => batch('follow')} disabled={batchBusy}>批量关注</button>
        ) : (
          <>
            <button className="btn btn--gold btn--sm" onClick={() => batch('add')} disabled={batchBusy}>批量入库</button>
            <button className="btn btn--ghost btn--sm" onClick={() => batch('favorite')} disabled={batchBusy}>批量收藏</button>
            <button className="btn btn--danger btn--sm" onClick={() => batch('delete')} disabled={batchBusy}>批量删除</button>
          </>
        )}
        <button className="btn btn--ghost btn--icon" onClick={() => setSelected(new Set())}>✕</button>
      </div>

      {/* P3：队列进度条 */}
      {queueRunning && queueInfo && <QueueOverlay info={queueInfo} />}
    </div>
  )
}
