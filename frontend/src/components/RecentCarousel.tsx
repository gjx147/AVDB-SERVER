import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { coverFileUrl } from '../api/client'
import type { Task } from '../api/types'

const GAP = 14 // 与 CSS .rc-track 的 gap 保持一致

/** 仪表盘「最近完成」多卡胶片轮播 —— 完整海报 contain 显示 + 逐卡 scroll-snap + 自动轮播（hover 暂停） */
export function RecentCarousel({ tasks }: { tasks: Task[] }) {
  const nav = useNavigate()
  const trackRef = useRef<HTMLDivElement>(null)
  const [progress, setProgress] = useState(0)
  const [paused, setPaused] = useState(false)
  const n = tasks.length

  // 一张卡片的滚动步长（卡宽 + 间距）
  const step = () => {
    const el = trackRef.current
    const card = el?.firstElementChild as HTMLElement | null
    return card ? card.offsetWidth + GAP : el?.clientWidth || 1
  }

  // 滚动进度：滑动/滚轮/箭头/自动播放统一生效
  const handleScroll = () => {
    const el = trackRef.current
    if (!el) return
    const max = el.scrollWidth - el.clientWidth
    setProgress(max > 0 ? Math.min(1, el.scrollLeft / max) : 0)
  }

  const go = (i: number) => {
    const el = trackRef.current
    if (!el) return
    const max = Math.max(0, el.scrollWidth - el.clientWidth)
    const left = Math.min(Math.max(0, i * step()), max)
    el.scrollTo({ left, behavior: 'smooth' })
  }

  // 自动轮播：5s 前进一张，到头回到第一张；hover 暂停
  useEffect(() => {
    if (paused || n < 2) return
    const t = setInterval(() => {
      const el = trackRef.current
      if (!el) return
      const max = el.scrollWidth - el.clientWidth
      const atEnd = el.scrollLeft >= max - 2
      go(atEnd ? 0 : Math.round(el.scrollLeft / step()) + 1)
    }, 5000)
    return () => clearInterval(t)
  }, [paused, n])

  if (n === 0) return null

  return (
    <div className="rc" onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)}>
      <div className="rc-track" ref={trackRef} onScroll={handleScroll}>
        {tasks.map((t) => {
          const remote = t.poster_url || (() => { try { return JSON.parse(t.thumbnail_urls || '[]')[0] } catch { return null } })()
          return (
            <div key={t.id} className="rc-card" onClick={() => nav(`/task/${t.id}`)}
              role="button" tabIndex={0} aria-label={`查看 ${t.video_code || '作品'} 详情`}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); nav(`/task/${t.id}`) } }}>
              <div className="rc-imgbox">
                <img src={coverFileUrl(t.id)} alt={t.video_code || ''} loading="lazy" referrerPolicy="no-referrer"
                  onError={(e) => { if (remote && e.currentTarget.src !== remote) e.currentTarget.src = remote; else e.currentTarget.style.opacity = '0.15' }} />
                {t.is_favorite ? <span className="rc-fav">♥</span> : null}
              </div>
              <div className="rc-cap">
                <div className="rc-code">{t.video_code || '—'}</div>
                <div className="rc-title">{t.title || '未命名'}</div>
              </div>
            </div>
          )
        })}
      </div>

      {n > 1 && (<>
        <button className="rc-nav rc-nav--prev" aria-label="上一张"
          onClick={() => go(Math.round((trackRef.current?.scrollLeft || 0) / step()) - 1)}>‹</button>
        <button className="rc-nav rc-nav--next" aria-label="下一张"
          onClick={() => go(Math.round((trackRef.current?.scrollLeft || 0) / step()) + 1)}>›</button>
      </>)}
      <div className="rc-progress" aria-hidden="true">
        <div className="rc-progress-fill" style={{ width: `${Math.max(4, progress * 100)}%` }} />
      </div>
    </div>
  )
}
