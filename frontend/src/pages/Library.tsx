import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, coverFileUrl } from '../api/client'
import type { Task, ListSourceWithStats } from '../api/types'
import { PosterCard } from '../components/PosterCard'
import { QueueOverlay } from '../components/QueueOverlay'
import { PageHead, Loading, Empty, ErrorEmpty } from '../components/States'
import { Icon } from '../components/Icons'
import { useStore } from '../store/useStore'

const PAGE = 48

export function Library() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const initialQ = searchParams.get('q') || ''
  const [tasks, setTasks] = useState<Task[] | null>(null)
  const [sources, setSources] = useState<ListSourceWithStats[]>([])
  const [q, setQ] = useState(initialQ)
  const [status, setStatus] = useState('')
  const [sourceId, setSourceId] = useState<number | ''>('')
  const [view, setView] = useState<'grid' | 'row'>('grid')
  const [sort, setSort] = useState('date_desc')
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [page, setPage] = useState(0)
  const [total, setTotal] = useState(0)
  const [queueRunning, setQueueRunning] = useState(false)
  const [queueInfo, setQueueInfo] = useState<{ current: number; total: number; current_video_code: string | null; stage: string; done: number[]; failed: number[] } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const toastOk = useStore((s) => s.toastOk)
  const toastErr = useStore((s) => s.toastErr)

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
        const r = await api.v2.searchFts(searchQ, PAGE)
        if (reqId !== reqSeqRef.current) return
        setTasks(r.tasks)
        setTotal(r.total)
      } else {
        const r = await api.v2.tasks({
          status: status || undefined,
          list_source_id: sourceId || undefined,  // P1#5: 列表源筛选（之前漏传）
          sort,
          limit: PAGE, offset: skip,
        })
        if (reqId !== reqSeqRef.current) return
        setTasks(r.tasks)
        setTotal(r.total)
      }
    } catch (e) { setError(String((e as Error).message)); setTasks([]) }
    // P0#3: page 不进依赖（通过 pageOverride 传入），避免翻页时 load 重建→effect 回弹到第0页
  }, [page, status, sourceId, sort])

  useEffect(() => {
    // 优先读 store 缓存，避免重复请求
    const cached = useStore.getState().listSources
    if (cached) { setSources(cached); return }
    api.listSources.list().then((data) => { setSources(data); useStore.getState().setListSources(data) }).catch(() => {})
  }, [])
  // P0-6/P0#3: 切换筛选条件时重置到第0页并清空选中。
  // 关键：依赖筛选条件 [status, sourceId, sort] 而非 load（load 含 page 依赖，翻页时也会重建，
  // 若依赖 load 会导致翻页触发此 effect 把 page 回弹到 0）。用 ref 读最新 load 避免闭包陷阱。
  const loadRef = useRef(load)
  useEffect(() => { loadRef.current = load }, [load])
  useEffect(() => {
    setPage(0); setSelected(new Set()); loadRef.current(0)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, sourceId, sort])

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

  const batch = async (kind: 'delete' | 'retry' | 'favorite') => {
    const ids = [...selected]
    if (!ids.length) return
    try {
      if (kind === 'delete') await api.tasks.batchDelete(ids)
      if (kind === 'retry') await api.tasks.batchRetry(ids)
      if (kind === 'favorite') await api.tasks.batchFavorite(ids)
      toastOk(`已批量${kind === 'delete' ? '删除' : kind === 'retry' ? '重试' : '收藏'} ${ids.length} 项`)
      setSelected(new Set())
      load(page)  // P1-3: 显式传当前页码，避免 load() 闭包用了旧 page
    } catch (e) { toastErr(String((e as Error).message)) }
  }

  return (
    <div className="page">
      <PageHead eyebrow={`Library · ${tasks?.length ?? 0} 部`} title={<>影片<em>库</em></>}
        sub="按画廊式海报墙浏览全部藏品。支持按番号、演员、标签筛选与批量管理。">
      </PageHead>

      <div className="gallery-toolbar">
        <div className="search">
          <Icon.search />
          <input placeholder="输入番号或关键词搜索…" value={q}
            onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && (setPage(0), load(0))} />
        </div>
        <select className="select" value={sourceId} onChange={(e) => setSourceId(e.target.value ? +e.target.value : '')} aria-label="筛选列表源">
          <option value="">全部列表源</option>
          {sources.map((s) => <option key={s.id} value={s.id}>{s.list_code}</option>)}
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
       tasks === null ? <Loading /> : tasks.length === 0 ? (
        <Empty icon="○" title="暂无任务" sub="请先到列表源执行扫描，或按番号创建任务。" />
      ) : view === 'grid' ? (
        <div className="gallery">
          {tasks.map((t) => <PosterCard key={t.id} task={t} selected={selected.has(t.id)} selectable onToggle={() => toggleSel(t.id)} />)}
        </div>
      ) : (
        <div className="card">
          {tasks.map((t) => (
            <div className="row-item" key={t.id} onClick={() => navigate(`/task/${t.id}`)}>
              <img className="row-thumb" src={coverFileUrl(t.id)} alt={`${t.video_code || '作品'} 封面`} referrerPolicy="no-referrer"
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
                {t.rating ? `★ ${t.rating}` : ''}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ── Pager ── */}
      {total > PAGE && (
        <div className="pager">
          <button disabled={page === 0} onClick={() => { setPage(page - 1); load(page - 1) }}>上一页</button>
          <span style={{ fontFamily: 'var(--ff-mono)', fontSize: 13, color: 'var(--t-mute)', padding: '0 14px' }}>
            {page * PAGE + 1}-{Math.min((page + 1) * PAGE, total)} / 共 {total} 条
          </span>
          <button disabled={(page + 1) * PAGE >= total} onClick={() => { setPage(page + 1); load(page + 1) }}>下一页</button>
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
        <button className="btn btn--ghost btn--icon" onClick={() => setSelected(new Set())}>✕</button>
      </div>

      {/* 串行队列状态条 */}
      {queueRunning && queueInfo && <QueueOverlay info={queueInfo} />}
    </div>
  )
}
