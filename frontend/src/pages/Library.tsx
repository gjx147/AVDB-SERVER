import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, coverFileUrl, withImageAuth } from '../api/client'
import type { Task, ListSourceWithStats } from '../api/types'
import { PosterCard } from '../components/PosterCard'
import { QueueOverlay } from '../components/QueueOverlay'
import { PageHead, Empty, ErrorEmpty } from '../components/States'
import { SkeletonGallery } from '../components/Skeleton'
import { Icon } from '../components/Icons'
import { useStore } from '../store/useStore'
import { useWhisper } from '../i18n/whisper'

const PAGE = 48

export function Library() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  // 全部筛选/页码状态从 URL 恢复（刷新/分享/返回不丢）
  const initialQ = searchParams.get('q') || ''
  const [tasks, setTasks] = useState<Task[] | null>(null)
  const [sources, setSources] = useState<ListSourceWithStats[]>([])
  const [q, setQ] = useState(initialQ)
  const [status, setStatus] = useState(searchParams.get('status') || '')
  const [sourceId, setSourceId] = useState<number | ''>(searchParams.get('source') ? +(searchParams.get('source') as string) : '')
  const [view, setView] = useState<'grid' | 'row'>(searchParams.get('view') === 'row' ? 'row' : 'grid')
  const [sort, setSort] = useState(searchParams.get('sort') || 'date_desc')
  const [inLib, setInLib] = useState<'all' | 'in' | 'out'>(
    searchParams.get('inlib') === 'in' ? 'in' : searchParams.get('inlib') === 'out' ? 'out' : 'all')
  const [selected, setSelected] = useState<Set<number>>(new Set())
  // 无效页码（如 ?page=abc）回退 0，避免 NaN 污染请求参数
  const [page, setPage] = useState(() => {
    const p = searchParams.get('page')
    const n = p ? Number(p) : NaN
    return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0
  })
  const [total, setTotal] = useState(0)
  const [queueRunning, setQueueRunning] = useState(false)
  const [queueInfo, setQueueInfo] = useState<{ current: number; total: number; current_video_code: string | null; stage: string; done: number[]; failed: number[] } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const toastOk = useStore((s) => s.toastOk)
  const toastErr = useStore((s) => s.toastErr)
  const confirmBox = useStore((s) => s.confirm)
  const w = useWhisper()

  // P1: 修复定时器泄漏 —— 用 ref 存储 interval，组件卸载时清理
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [])

  // 串行处理：选中任务逐个走「下载图片+提取磁力」
  const queueProcess = async () => {
    const ids = [...selected]
    if (!ids.length) return
    // P1-7: 进入函数立即置 running，避免网络往返期间重复提交
    setQueueRunning(true)
    try {
      await api.images.queueStart(ids)
      setSelected(new Set())
      toastOk(`已启动串行队列，共 ${ids.length} 个任务`)
      // 轮询状态（用 ref 存储，确保卸载时清理）
      pollRef.current = setInterval(async () => {
        try {
          const s = await api.images.queueStatus()
          setQueueInfo(s)
          if (!s.running) {
            if (pollRef.current) clearInterval(pollRef.current)
            pollRef.current = null
            setQueueRunning(false)
            toastOk(`串行处理完成：成功 ${s.done?.length || 0}，失败 ${s.failed?.length || 0}`)
            load()
          }
        } catch {
          // P1-4: 轮询失败时停止，避免无限轰击接口 + unhandled rejection
          if (pollRef.current) clearInterval(pollRef.current)
          pollRef.current = null
          setQueueRunning(false)
          toastErr('队列状态查询失败，已停止轮询')
        }
      }, 3000)
    } catch (e) {
      setQueueRunning(false)
      toastErr(String((e as Error).message))
    }
  }

  useEffect(() => {
    // 页面加载时检查是否有运行中的队列
    api.images.queueStatus().then((s) => { if (s.running) { setQueueRunning(true); setQueueInfo(s) } }).catch(() => {})
  }, [])

  // P1: 搜索防抖 —— q 不在依赖里，避免逐字符请求；用 ref 持有最新值
  const qRef = useRef(initialQ)
  useEffect(() => { qRef.current = q }, [q])
  const reqSeqRef = useRef(0)  // P1-6: 请求序号防竞态

  const load = useCallback(async (pageOverride?: number) => {
    const p = pageOverride !== undefined ? pageOverride : page
    const reqId = ++reqSeqRef.current  // P1-6: 丢弃旧请求结果
    setTasks(null)
    setError(null)
    try {
      const searchQ = qRef.current.trim()
      const skip = p * PAGE
      // F11/F12: 使用 v2 API 支持排序 + FTS 搜索
      if (searchQ) {
        const r = await api.v2.searchFts(searchQ, PAGE, skip)
        if (reqId !== reqSeqRef.current) return
        setTasks(r.tasks)
        setTotal(r.total)
      } else {
        const r = await api.v2.tasks({
          status: status || undefined,
          list_source_id: sourceId || undefined,  // P1#5: 列表源筛选（之前漏传）
          in_library: inLib === 'all' ? undefined : inLib === 'in',
          sort,
          limit: PAGE, offset: skip,
        })
        if (reqId !== reqSeqRef.current) return
        setTasks(r.tasks)
        setTotal(r.total)
      }
    } catch (e) { setError(String((e as Error).message)); setTasks([]) }
    // P0#3: page 不进依赖（通过 pageOverride 传入），避免翻页时 load 重建→effect 回弹到第0页
  }, [page, status, sourceId, sort, inLib])

  useEffect(() => {
    // 优先读 store 缓存，避免重复请求
    const cached = useStore.getState().listSources
    if (cached) { setSources(cached); return }
    api.listSources.list().then((data) => { setSources(data); useStore.getState().setListSources(data) }).catch(() => {})
  }, [])

  // ── URL 同步：筛选/页码/搜索词全部序列化进 searchParams ──
  const filtersRef = useRef({ status, sourceId, sort, view, inLib })
  useEffect(() => { filtersRef.current = { status, sourceId, sort, view, inLib } }, [status, sourceId, sort, view, inLib])
  const syncUrl = (p: number) => {
    const f = filtersRef.current
    const trimmed = qRef.current.trim()
    const next: Record<string, string> = {}
    if (trimmed) next.q = trimmed
    if (f.status) next.status = f.status
    if (f.sourceId) next.source = String(f.sourceId)
    if (f.sort !== 'date_desc') next.sort = f.sort
    if (f.view !== 'grid') next.view = f.view
    if (f.inLib !== 'all') next.inlib = f.inLib
    if (p > 0) next.page = String(p)
    setSearchParams(next, { replace: true })
  }
  const goPage = (p: number) => { setPage(p); load(p); syncUrl(p) }

  // 切换筛选条件：重置到第0页 + 写 URL（首帧跳过——初始值已从 URL 恢复）
  const loadRef = useRef(load)
  useEffect(() => { loadRef.current = load }, [load])
  const firstFilters = useRef(true)
  useEffect(() => {
    if (firstFilters.current) { firstFilters.current = false; loadRef.current(page); return }
    setPage(0); setSelected(new Set()); loadRef.current(0); syncUrl(0)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, sourceId, sort, inLib, view])

  // 搜索防抖 300ms：停字自动搜索 + 写 URL（首帧跳过避免挂载双请求）
  const firstQ = useRef(true)
  useEffect(() => {
    if (firstQ.current) { firstQ.current = false; return }
    const t = setTimeout(() => {
      setPage(0); setSelected(new Set()); loadRef.current(0); syncUrl(0)
    }, 300)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q])

  const toggleSel = (id: number) => {
    setSelected((prev) => {
      const n = new Set(prev)
      n.has(id) ? n.delete(id) : n.add(id)
      return n
    })
  }

  // 全选/取消全选当前页（仅当前可见的任务）
  const allSelected = tasks !== null && tasks.length > 0 && tasks.every((t) => selected.has(t.id))
  const toggleAll = () => {
    if (!tasks) return
    setSelected((prev) => {
      const allOnPage = tasks.every((t) => prev.has(t.id))
      const n = new Set(prev)
      if (allOnPage) tasks.forEach((t) => n.delete(t.id))
      else tasks.forEach((t) => n.add(t.id))
      return n
    })
  }

  const selectAllFiltered = async () => {
    try {
      const searchQ = qRef.current.trim()
      const r = searchQ
        ? await api.v2.searchFts(searchQ, 200)
        : await api.v2.tasks({
            status: status || undefined,
            list_source_id: sourceId || undefined,
            in_library: inLib === 'all' ? undefined : inLib === 'in',
            sort, limit: 200, offset: 0,
          })
      setSelected(new Set(r.tasks.map((t) => t.id)))
      toastOk(`已全选 ${r.tasks.length} 条（当前筛选范围内，上限 200）`)
    } catch (e) { toastErr(String((e as Error).message)) }
  }

  const batch = async (kind: 'delete' | 'retry' | 'favorite' | 'push' | 'view') => {
    const ids = [...selected]
    if (!ids.length) return
    if (kind === 'delete') {
      const ok = await confirmBox('批量删除', `将删除 ${ids.length} 个任务及其关联图片缓存，不可恢复。确定继续？`)
      if (!ok) return
    }
    try {
      if (kind === 'delete') await api.tasks.batchDelete(ids)
      if (kind === 'retry') await api.tasks.batchRetry(ids)
      if (kind === 'favorite') await api.tasks.batchFavorite(ids)
      if (kind === 'push') {
        const r = await api.tasks.batchPush(ids)
        toastOk(`已推送 ${r.pushed} 项${r.skipped ? `，跳过 ${r.skipped} 项（无磁力或失败）` : ''}`)
        setSelected(new Set())
        load(page)
        return
      }
      if (kind === 'view') {
        const r = await api.tasks.batchView(ids, 'viewed')
        toastOk(`已标记 ${r.updated} 项已看`)
        setSelected(new Set())
        load(page)
        return
      }
      toastOk(`已批量${kind === 'delete' ? '删除' : kind === 'retry' ? '重试' : '收藏'} ${ids.length} 项`)
      setSelected(new Set())
      load(page)  // P1-3: 显式传当前页码，避免 load() 闭包用了旧 page
    } catch (e) { toastErr(String((e as Error).message)) }
  }

  return (
    <div className="page fade-bottom">
      <PageHead eyebrow={`Library · ${tasks?.length ?? 0} 部`} title={<>影片<em>库</em></>}
        sub="把每一张让你心动的脸，收进你的深夜画廊。">
      </PageHead>

      <div className="gallery-toolbar">
        <div className="search">
          <Icon.search />
          <input placeholder="输入番号或关键词，停字自动搜索…" value={q}
            onChange={(e) => setQ(e.target.value)} aria-label="搜索影片库" />
        </div>
        <select className="select" value={sourceId} onChange={(e) => setSourceId(e.target.value ? +e.target.value : '')} aria-label="筛选列表源">
          <option value="">全部列表源</option>
          {sources.map((s) => <option key={s.id} value={s.id}>{s.list_code}</option>)}
        </select>
        <select className="select" value={inLib} onChange={(e) => setInLib(e.target.value as 'all' | 'in' | 'out')} aria-label="媒体库筛选">
          <option value="all">全部媒体库状态</option>
          <option value="in">✓ 在媒体库</option>
          <option value="out">✗ 不在媒体库</option>
        </select>
        <select className="select" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="筛选状态">
          <option value="">全部状态</option>
          <option value="visited">已入库</option>
          <option value="pending">待处理</option>
          <option value="failed">失败</option>
        </select>
        <select className="select" value={sort} onChange={(e) => { setSort(e.target.value); setPage(0) }} aria-label="排序方式">
          <option value="date_desc">最新发行</option>
          <option value="rating_desc">评分最高</option>
          <option value="title_asc">标题排序</option>
          <option value="favorite_desc">收藏优先</option>
        </select>
        <div className="seg">
          <button className={view === 'grid' ? 'on' : ''} onClick={() => setView('grid')}>画廊</button>
          <button className={view === 'row' ? 'on' : ''} onClick={() => setView('row')}>列表</button>
        </div>
        {tasks && tasks.length > 0 && (
          <button className="btn btn--ghost btn--sm" onClick={toggleAll}>{allSelected ? '取消全选' : '全选本页'}</button>
        )}
      </div>

      {error ? <ErrorEmpty message={error} onRetry={() => { setPage(0); load(0) }} /> :
       tasks === null ? <SkeletonGallery /> : tasks.length === 0 ? (
        <Empty icon="♡" title={w('empty_lib_title')} sub={w('empty_lib_sub')} />
      ) : view === 'grid' ? (
        <div className="gallery">
          {tasks.map((t) => <PosterCard key={t.id} task={t} selected={selected.has(t.id)} selectable onToggle={() => toggleSel(t.id)} />)}
        </div>
      ) : (
        <div className="card">
          {tasks.map((t) => (
            <div className="row-item" key={t.id} onClick={() => navigate(`/task/${t.id}`)}
              role="button" tabIndex={0}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); navigate(`/task/${t.id}`) } }}>
              <img className="row-thumb" src={withImageAuth(coverFileUrl(t.id))} alt={`${t.video_code || '作品'} 封面`} referrerPolicy="no-referrer" loading="lazy" decoding="async"
                onError={(e) => { const r = t.poster_url || (() => { try { return JSON.parse(t.thumbnail_urls || '[]')[0] } catch { return null } })(); if (r && e.currentTarget.src !== r) { e.currentTarget.src = r } else { e.currentTarget.style.visibility = 'hidden' } }} />
              <div>
                <div className="row-code">{t.video_code || '—'}</div>
                <div className="row-title">{t.title || '未命名'}</div>
              </div>
              <div className="row-tags">
                {t.status === 'visited' && <span className="chip chip-green">已入库</span>}
                {t.status === 'pending' && <span className="chip chip-amber">待处理</span>}
                {t.status === 'failed' && <span className="chip chip-red">失败</span>}
                {t.is_favorite ? <span className="chip chip-rose">收藏</span> : null}
              </div>
              <div style={{ fontFamily: 'var(--ff-mono)', fontSize: 11, color: 'var(--t-faint)' }}>
                {t.rating ? `♥ ${t.rating}` : ''}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ── Pager ── */}
      {total > PAGE && (
        <div className="pager">
          <button disabled={page === 0} onClick={() => goPage(page - 1)}>上一页</button>
          <span style={{ fontFamily: 'var(--ff-mono)', fontSize: 13, color: 'var(--t-mute)', padding: '0 14px' }}>
            {page * PAGE + 1}-{Math.min((page + 1) * PAGE, total)} / 共 {total} 条
          </span>
          <button disabled={(page + 1) * PAGE >= total} onClick={() => goPage(page + 1)}>下一页</button>
        </div>
      )}

      <div className={`batchbar${selected.size ? ' show' : ''}`}>
        <span className="sel-count">已选 {selected.size} 项</span>
        <button className="btn btn--gold btn--sm" onClick={() => queueProcess()} disabled={queueRunning}>
          {queueRunning ? '处理中…' : '串行处理(图片+磁力)'}
        </button>
        <button className="btn btn--ghost btn--sm" onClick={() => batch('favorite')}>批量收藏</button>
        <button className="btn btn--ghost btn--sm" onClick={() => batch('retry')}>批量重试</button>
        <button className="btn btn--danger btn--sm" onClick={() => batch('delete')}>批量删除</button>
        <button className="btn btn--ghost btn--sm" onClick={() => batch('push')} disabled={selected.size === 0}>批量推送下载</button>
        <button className="btn btn--ghost btn--sm" onClick={() => batch('view')} disabled={selected.size === 0}>标记已看</button>
        <button className="btn btn--ghost btn--sm" onClick={selectAllFiltered} disabled={total === 0}>全选全部</button>
        <button className="btn btn--ghost btn--icon" onClick={() => setSelected(new Set())}>✕</button>
      </div>

      {/* 串行队列状态条 */}
      {queueRunning && queueInfo && <QueueOverlay info={queueInfo} />}
    </div>
  )
}
