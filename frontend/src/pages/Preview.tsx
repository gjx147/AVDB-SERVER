import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, coverFileUrl, withImageAuth } from '../api/client'
import type { Task } from '../api/types'
import { Loading, Empty, ErrorEmpty } from '../components/States'
import { useWhisper } from '../i18n/whisper'

const PAGE = 60

function colCountOf(w: number): number {
  return Math.max(2, Math.min(5, Math.floor(w / 300)))
}

/** 预览 · 漂移海报河 —— 五列异速异向无缝漂移，hover 暂停，点击进详情 */
export function Preview() {
  const nav = useNavigate()
  const w = useWhisper()
  const [items, setItems] = useState<Task[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [colCount, setColCount] = useState(() => colCountOf(window.innerWidth))
  const seen = useRef<Set<number>>(new Set())
  const offset = useRef(0)
  const busy = useRef(false)
  const seed = useRef(Math.floor(Math.random() * 2147483647))

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

  // 列数响应式
  useEffect(() => {
    let t: number | undefined
    const onR = () => { clearTimeout(t); t = window.setTimeout(() => setColCount(colCountOf(window.innerWidth)), 150) }
    window.addEventListener('resize', onR)
    return () => { window.removeEventListener('resize', onR); clearTimeout(t) }
  }, [])

  const buckets = useMemo(() => {
    const bs: Task[][] = Array.from({ length: colCount }, () => [])
    items.forEach((t, i) => bs[i % colCount].push(t))
    return bs
  }, [items, colCount])

  const remoteOf = (t: Task) => t.poster_url || (() => { try { return JSON.parse(t.thumbnail_urls || '[]')[0] as string } catch { return null } })()
  const item = (t: Task, key: string) => {
    const remote = remoteOf(t)
    return (
      <div key={key} className="preview-item" role="button" tabIndex={0}
        onClick={() => nav(`/task/${t.id}`)}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); nav(`/task/${t.id}`) } }}
        aria-label={`查看 ${t.video_code || '作品'} 详情`}>
        <img loading="lazy" decoding="async" referrerPolicy="no-referrer"
          src={withImageAuth(`${coverFileUrl(t.id)}?v=${t.updated_at || '0'}`)}
          alt={t.video_code || ''}
          onError={(e) => { if (remote && e.currentTarget.src !== remote) e.currentTarget.src = remote; else e.currentTarget.style.opacity = '0.2' }} />
        {t.video_code && <span className="preview-code">{t.video_code}</span>}
      </div>
    )
  }

  const stage = 'preview-stage'

  if (error && items.length === 0) {
    return (
      <div className={stage}>
        <ErrorEmpty message={error} onRetry={() => { setLoading(true); loadMore() }} />
      </div>
    )
  }
  if (loading && items.length === 0) {
    return <div className={stage}><Loading label={w('preview_loading')} /></div>
  }
  if (items.length === 0 && total === 0) {
    return <div className={stage}><Empty title={w('preview_empty_title')} sub={w('preview_empty_sub')} /></div>
  }

  return (
    <div className={stage}>
      <div className="preview-cols">
        {buckets.map((b, ci) => {
          const dur = Math.max(32, Math.round(b.length * 4))
          return (
            <div key={ci} className={`preview-col${ci % 2 ? ' reverse' : ''}`}>
              <div className="preview-track" style={{ animationDuration: `${dur}s` }}>
                {[...b, ...b].map((t, i) => item(t, `${ci}-${i}`))}
              </div>
            </div>
          )
        })}
      </div>
      <div className="preview-fade top" aria-hidden="true" />
      <div className="preview-fade bottom" aria-hidden="true" />
      <div className="preview-head">
        <span className="preview-title">预览<em> · 漂移海报河</em></span>
        <span className="preview-sub">
          {total > 0 ? `已入列 ${items.length} / ${total}` : ''}
          {items.length > 0 ? ' · 悬停暂停 · 点击进详情' : ''}
        </span>
      </div>
      {loading && items.length > 0 && <div className="preview-more"><Loading /></div>}
    </div>
  )
}
