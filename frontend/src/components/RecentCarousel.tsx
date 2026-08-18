import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { coverFileUrl } from '../api/client'
import type { Task } from '../api/types'

/** 仪表盘「最近完成」大图轮播 —— 原生 scroll-snap 横滑 + 箭头/圆点 + 自动轮播（hover 暂停） */
export function RecentCarousel({ tasks }: { tasks: Task[] }) {
  const nav = useNavigate()
  const trackRef = useRef<HTMLDivElement>(null)
  const [index, setIndex] = useState(0)
  const [paused, setPaused] = useState(false)
  const n = tasks.length

  // onScroll 反算当前索引：滑动/滚轮/箭头/自动播放统一生效
  const handleScroll = () => {
    const el = trackRef.current
    if (!el || !el.clientWidth) return
    const i = Math.round(el.scrollLeft / el.clientWidth)
    if (i !== index && i >= 0 && i < n) setIndex(i)
  }

  const go = (i: number) => {
    const el = trackRef.current
    if (!el || !el.clientWidth) return
    const target = (((i % n) + n) % n) * el.clientWidth
    el.scrollTo({ left: target, behavior: 'smooth' })
  }

  // 自动轮播 5s；hover 暂停；手动切换后重置计时
  useEffect(() => {
    if (paused || n < 2) return
    const t = setInterval(() => go(index + 1 >= n ? 0 : index + 1), 5000)
    return () => clearInterval(t)
  }, [paused, index, n])

  if (n === 0) return null

  return (
    <div className="rc" onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)}>
      <div className="rc-track" ref={trackRef} onScroll={handleScroll}>
        {tasks.map((t) => {
          const remote = t.poster_url || (() => { try { return JSON.parse(t.thumbnail_urls || '[]')[0] } catch { return null } })()
          return (
            <div key={t.id} className="rc-slide" onClick={() => nav(`/task/${t.id}`)}
              role="button" tabIndex={0} aria-label={`查看 ${t.video_code || '作品'} 详情`}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); nav(`/task/${t.id}`) } }}>
              <img src={coverFileUrl(t.id)} alt={t.video_code || ''} loading="lazy" referrerPolicy="no-referrer"
                onError={(e) => { if (remote && e.currentTarget.src !== remote) e.currentTarget.src = remote; else e.currentTarget.style.opacity = '0.15' }} />
              <div className="rc-shade" />
              <div className="rc-info">
                <div className="rc-code">{t.video_code || '—'}</div>
                <div className="rc-title">{t.title || '未命名'}</div>
                <div className="rc-tags">
                  {t.is_favorite ? <span className="chip chip-rose">收藏</span> : null}
                  <span className="chip chip-green">已入库</span>
                  <span className="rc-more">查看详情 →</span>
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {n > 1 && (<>
        <button className="rc-nav rc-nav--prev" aria-label="上一张" onClick={() => go(index - 1)}>‹</button>
        <button className="rc-nav rc-nav--next" aria-label="下一张" onClick={() => go(index + 1)}>›</button>
        <div className="rc-dots">
          {tasks.map((t, i) => (
            <button key={t.id} className={`rc-dot${i === index ? ' on' : ''}`} aria-label={`第 ${i + 1} 张`}
              onClick={() => go(i)} />
          ))}
        </div>
      </>)}
    </div>
  )
}
