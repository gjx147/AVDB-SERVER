import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, coverFileUrl, withImageAuth } from '../api/client'
import type { Task } from '../api/types'
import { Loading, Empty, ErrorEmpty } from '../components/States'
import { useWhisper } from '../i18n/whisper'

const PAGE = 60

/** 预览 · 影片库海报随机瀑布流（布满侧边栏右侧区域，无限滚动，点击进详情） */
export function Preview() {
  const nav = useNavigate()
  const w = useWhisper()
  const [items, setItems] = useState<Task[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const seen = useRef<Set<number>>(new Set())
  const offset = useRef(0)
  const busy = useRef(false)
  const seed = useRef(Math.floor(Math.random() * 2147483647))  // 会话种子：本次浏览顺序稳定
  const sentinel = useRef<HTMLDivElement | null>(null)

  const loadMore = useCallback(async () => {
    if (busy.current) return
    busy.current = true
    try {
      const r = await api.v2.tasks({ sort: 'random', seed: seed.current, limit: PAGE, offset: offset.current })
      // 后端会话种子随机排序（全库级真随机，翻页不重不漏）；跨批 id 去重
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

  // 无限滚动：哨兵触底加载下一批
  useEffect(() => {
    const el = sentinel.current
    if (!el) return
    const ob = new IntersectionObserver(
      (es) => { if (es[0].isIntersecting && !loading && items.length < total) loadMore() },
      { rootMargin: '700px' })
    ob.observe(el)
    return () => ob.disconnect()
  }, [items.length, total, loading, loadMore])

  const remoteOf = (t: Task) => t.poster_url || (() => { try { return JSON.parse(t.thumbnail_urls || '[]')[0] as string } catch { return null } })()

  if (error && items.length === 0) {
    return (
      <div className="preview-page">
        <ErrorEmpty message={error} onRetry={() => { setLoading(true); loadMore() }} />
      </div>
    )
  }
  if (loading && items.length === 0) {
    return <div className="preview-page"><Loading label={w('preview_loading')} /></div>
  }
  if (items.length === 0 && total === 0) {
    return (
      <div className="preview-page">
        <Empty title={w('preview_empty_title')} sub={w('preview_empty_sub')} />
      </div>
    )
  }

  return (
    <div className="preview-page">
      <div className="preview-head">
        <span className="preview-title">预览<em> · 随机瀑布流</em></span>
        <span className="preview-sub">{total > 0 ? `已加载 ${items.length} / ${total}` : ''}</span>
      </div>
      <div className="preview-wall">
        {items.map((t) => {
          const remote = remoteOf(t)
          return (
            <div key={t.id} className="preview-item" role="button" tabIndex={0}
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
        })}
      </div>
      <div ref={sentinel} style={{ height: 1 }} />
      {loading && items.length > 0 && <div className="preview-more"><Loading /></div>}
      {!loading && total > 0 && items.length >= total && (
        <div className="preview-end">{w('preview_end')}</div>
      )}
    </div>
  )
}
