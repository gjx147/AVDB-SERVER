import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, backdropUrl, coverFileUrl, withImageAuth } from '../api/client'
import type { Task } from '../api/types'
import { Loading, Empty, ErrorEmpty } from '../components/States'
import { MasonryCard } from '../components/MasonryCard'
import { useWhisper } from '../i18n/whisper'
import { computeShortestColumnLayout, colCountOf } from '../utils/masonry'
import { createPortal } from 'react-dom'

const PAGE = 60
type Mode = 'masonry' | 'river'

function remoteOf(t: Task): string | null {
  return t.poster_url || (() => { try { return JSON.parse(t.thumbnail_urls || '[]')[0] as string } catch { return null } })()
}

/** 预览 · 双形态海报墙
 *  masonry（默认）：JS 最短列瀑布流，原始宽高比不等高，卡片含标题/年份/评分，
 *                   onload 重排校正，骨架屏，触底加载
 *  river：漂移海报河（五列异速异向漂移，hover 暂停）
 *  数据：v2.tasks sort=random + 会话种子（全库真随机，翻页不重不漏），status=visited
 */
export function Preview() {
  const nav = useNavigate()
  const w = useWhisper()
  const [items, setItems] = useState<Task[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [mode, setMode] = useState<Mode>(() => (localStorage.getItem('preview_mode') === 'river' ? 'river' : 'masonry'))
  const [containerW, setContainerW] = useState(() => Math.max(320, window.innerWidth - 300))

  // 窗口尺寸联动（双模式共用）：masonry/河流的列数都随窗口宽度走
  useEffect(() => {
    const onR = () => setContainerW(Math.max(320, window.innerWidth - 300))
    window.addEventListener('resize', onR)
    return () => window.removeEventListener('resize', onR)
  }, [])
  const [layoutV, setLayoutV] = useState(0)
  const [hover, setHover] = useState<{ task: Task; x: number; y: number } | null>(null)
  const hoverTimer = useRef<number | undefined>(undefined)
  const ratioRef = useRef<Record<number, number>>({})
  const seen = useRef<Set<number>>(new Set())
  const offset = useRef(0)
  const busy = useRef(false)
  const seed = useRef(Math.floor(Math.random() * 2147483647))
  const sentinel = useRef<HTMLDivElement | null>(null)
  const containerRef = useRef<HTMLDivElement | null>(null)
  const roTimer = useRef<number | undefined>(undefined)

  const loadMore = useCallback(async () => {
    if (busy.current) return
    busy.current = true
    try {
      const r = await api.v2.tasks({ status: 'visited', sort: 'random', seed: seed.current, limit: PAGE, offset: offset.current })
      const batch = r.tasks.filter((t) => !seen.current.has(t.id))
      batch.forEach((t) => seen.current.add(t.id))
      offset.current += r.tasks.length
      setTotal(r.total)
      setItems((prev) => [...prev, ...batch])
      setError(null)
    } catch (e) {
      setError(String((e as Error).message))
    } finally {
      setLoading(false)
      busy.current = false
    }
  }, [])

  useEffect(() => { loadMore() }, [loadMore])

  // 后台持续补池：分批拉到全库，新海报随补随入列
  useEffect(() => {
    if (error) return
    if (total > 0 && items.length >= total) return
    const t = setTimeout(() => { loadMore() }, 1200)
    return () => clearTimeout(t)
  }, [items.length, total, error, loadMore])

  const colCount = colCountOf(containerW, 240, 5)
  const gap = 14

  // JS masonry 布局计算（onload 更新 ratio → layoutV 触发增量重排）
  const layout = useMemo(() => {
    if (mode !== 'masonry') return null
    const entries = items.map((t) => ({ id: t.id, ratio: ratioRef.current[t.id] || 1.4 }))
    return computeShortestColumnLayout(entries, containerW, colCount, gap)
  }, [items, containerW, colCount, mode, layoutV])

  const placementMap = useMemo(
    () => new Map((layout?.placements || []).map((p) => [p.id, p])),
    [layout])

  // 无限滚动哨兵（masonry 模式；触底 + 尚未拉完 → 下一批）
  useEffect(() => {
    const el = sentinel.current
    if (!el || mode !== 'masonry') return
    const ob = new IntersectionObserver(
      (es) => { if (es[0].isIntersecting && !loading && items.length < total) loadMore() },
      { rootMargin: '700px' })
    ob.observe(el)
    return () => ob.disconnect()
  }, [mode, loading, items.length, total, loadMore])

  // 悬停详情卡：优先条目右侧，空间不足翻左侧；垂直钳制在视口内
  const openHover = (t: Task, el: HTMLElement) => {
    clearTimeout(hoverTimer.current)
    const r = el.getBoundingClientRect()
    const W = 340
    let x = r.right + 12
    if (x + W > window.innerWidth - 8) x = Math.max(8, r.left - W - 12)
    const y = Math.max(66, Math.min(r.top - 30, window.innerHeight - 440))
    setHover({ task: t, x, y })
  }
  const scheduleClose = () => {
    clearTimeout(hoverTimer.current)
    hoverTimer.current = window.setTimeout(() => setHover(null), 150)
  }
  const cancelClose = () => clearTimeout(hoverTimer.current)

  // 图片 onload：测原始宽高比 → 更新 ratio → 重排
  const onImgLoad = (t: Task) => (e: React.SyntheticEvent<HTMLImageElement>) => {
    const nh = e.currentTarget.naturalHeight
    const nw = e.currentTarget.naturalWidth
    if (nh > 0 && nw > 0) {
      const r = nh / nw
      if (Math.abs((ratioRef.current[t.id] || 0) - r) > 0.02) {
        ratioRef.current[t.id] = r
        setLayoutV((v) => v + 1)
      }
    }
  }

  const setModePersist = (m: Mode) => { setMode(m); localStorage.setItem('preview_mode', m) }
  const stage = mode === 'river' ? 'preview-stage' : 'preview-stage preview-stage--scroll'

  // ── 状态分支（全部 hooks 之后）──
  if (error && items.length === 0) {
    return (
      <div className={stage}>
        <ErrorEmpty message={error} onRetry={() => { setLoading(true); loadMore() }} />
      </div>
    )
  }
  if (loading && items.length === 0 && mode === 'river') {
    return <div className={stage}><Loading label={w('preview_loading')} /></div>
  }
  if (items.length === 0 && total === 0 && !loading) {
    return <div className={stage}><Empty title={w('preview_empty_title')} sub={w('preview_empty_sub')} /></div>
  }

  const head = (
    <div className="preview-head">
      <span className="preview-title">预览<em> · {mode === 'river' ? '漂移海报河' : '随机瀑布流'}</em></span>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span className="preview-sub">{total > 0 ? `已加载 ${items.length} / ${total}` : ''}</span>
        <div className="seg" role="group" aria-label="布局模式">
          <button className={mode === 'masonry' ? 'on' : ''} onClick={() => setModePersist('masonry')}>静态</button>
          <button className={mode === 'river' ? 'on' : ''} onClick={() => setModePersist('river')}>河流</button>
        </div>
      </div>
    </div>
  )

  // ── masonry 形态 ──
  if (mode === 'masonry') {
    const skel = loading && items.length === 0
    return (
      <div className={stage}>
        {head}
        {!skel && layout && (
          <div ref={containerRef} className="preview-masonry" style={{ height: layout.height || 'auto' }}>
            {items.map((t) => {
              const p = placementMap.get(t.id)
              if (!p) return null
              const remote = remoteOf(t)
              return (
                <MasonryCard key={t.id} x={p.x} y={p.y} w={layout.colWidth}
                  src={withImageAuth(`${coverFileUrl(t.id)}?v=${t.updated_at || '0'}`)} remote={remote}
                  title={t.title || t.video_code || '未命名'} code={t.video_code}
                  year={t.release_date?.slice(0, 4)} rating={t.rating}
                  onLoad={onImgLoad(t)} onClick={() => nav(`/task/${t.id}`)}
                  onMouseEnter={(el) => openHover(t, el)} onMouseLeave={scheduleClose}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); nav(`/task/${t.id}`) } }}
                  ariaLabel={`查看 ${t.video_code || '作品'} 详情`} />
              )
            })}
          </div>
        )}
        {skel && layout && (
          <div ref={containerRef} className="preview-masonry" style={{ height: layout.height || 'auto' }}>
            {layout.placements.map((p, i) => (
              <div key={`sk${i}`} className="mcard-skel"
                style={{ left: p.x, top: p.y, width: layout.colWidth, height: Math.round(layout.colWidth * 1.4) + 62 }} />
            ))}
          </div>
        )}
        <div ref={sentinel} style={{ height: 1 }} />
        {!loading && total > 0 && items.length >= total && (
          <div className="preview-end">{w('preview_end')}</div>
        )}
        {loading && items.length > 0 && <div className="preview-more"><Loading /></div>}
      </div>
    )
  }

  // ── river 形态（漂移海报河）──
  return (
    <div className={stage}>
      {head}
      <RiverView items={items} colCount={colCountOf(containerW, 300, 5)} nav={nav}
        onHover={(t, el) => openHover(t, el)} onLeave={scheduleClose} />
      {loading && items.length > 0 && <div className="preview-more"><Loading /></div>}
      {hover && createPortal(
        <div className="pv-hover" style={{ left: hover.x, top: hover.y }}
          onMouseEnter={cancelClose} onMouseLeave={scheduleClose}>
          <img className="pv-hover-cover" alt=""
            src={withImageAuth(`${backdropUrl(hover.task.id)}?v=${hover.task.updated_at || '0'}`)}
            onError={(e) => {
              const fb = remoteOf(hover.task)
              if (fb && e.currentTarget.src !== fb) e.currentTarget.src = fb
            }} />
          <div className="pv-hover-body">
            <div className="pv-hover-code">{hover.task.video_code || '—'}</div>
            <div className="pv-hover-title" title={hover.task.title || ''}>{hover.task.title || '未命名'}</div>
            <div className="pv-hover-meta">
              {hover.task.rating ? <span>★ {hover.task.rating}</span> : null}
              {hover.task.release_date ? <span>{hover.task.release_date.slice(0, 10)}</span> : null}
              {hover.task.duration ? <span>{hover.task.duration}</span> : null}
            </div>
            {hover.task.actors ? (
              <div className="pv-hover-row">主演：{hover.task.actors.split(',').map((x) => x.trim()).filter(Boolean).slice(0, 3).join(' / ')}</div>
            ) : null}
            {hover.task.maker ? (
              <div className="pv-hover-row">厂牌：{hover.task.maker}{hover.task.series ? ` · ${hover.task.series}` : ''}</div>
            ) : null}
            {(hover.task.synopsis || hover.task.tags) ? (
              <div className="pv-hover-syn">
                {(hover.task.synopsis || '').trim() || (hover.task.tags || '').split(',').map((x) => x.trim()).filter(Boolean).slice(0, 6).join(' · ')}
              </div>
            ) : null}
          </div>
        </div>, document.body)}
    </div>
  )
}

/** 漂移海报河（river 形态渲染体） */
function RiverView({ items, colCount, nav, onHover, onLeave }: {
  items: Task[]; colCount: number; nav: (p: string) => void
  onHover: (t: Task, el: HTMLElement) => void; onLeave: () => void
}) {
  const buckets = useMemo(() => {
    const bs: Task[][] = Array.from({ length: colCount }, () => [])
    items.forEach((t, i) => bs[i % colCount].push(t))
    return bs
  }, [items, colCount])
  const remoteOf = (t: Task) => t.poster_url || (() => { try { return JSON.parse(t.thumbnail_urls || '[]')[0] as string } catch { return null } })()
  return (
    <div className="preview-cols">
      {buckets.map((b, ci) => {
        const dur = Math.max(32, Math.round(b.length * 4))
        return (
          <div key={ci} className={`preview-col${ci % 2 ? ' reverse' : ''}`}>
            <div className="preview-track" style={{ animationDuration: `${dur}s` }}>
              {[...b, ...b].map((t, i) => {
                const remote = remoteOf(t)
                return (
                  <div key={`${ci}-${i}`} className="preview-item" role="button" tabIndex={0}
                    onClick={() => nav(`/task/${t.id}`)}
                    onMouseEnter={(e) => onHover(t, e.currentTarget)} onMouseLeave={onLeave}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); nav(`/task/${t.id}`) } }}
                    aria-label={`查看 ${t.video_code || '作品'} 详情`}>
                    <img loading="lazy" decoding="async" referrerPolicy="no-referrer"
                      src={withImageAuth(`${coverFileUrl(t.id)}?v=${t.updated_at || '0'}`)}
                      alt={t.video_code || ''}
                      onError={(e) => { if (remote && e.currentTarget.src !== remote) e.currentTarget.src = remote; else e.currentTarget.style.opacity = '0.2' }} />
                    {t.video_code && <span className="preview-code">{t.video_code}</span>}
                  </div>
                )
              })}
            </div>
          </div>
        )
      })}
    </div>
  )
}
