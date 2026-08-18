import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, coverFileUrl } from '../api/client'
import type { Task } from '../api/types'
import { Loading, Empty, ErrorEmpty } from '../components/States'

const PER_PAGE = 15

/** 首页 · 影视墙 —— 影片库作品全屏海报墙 + 分页轮播（自动翻页/箭头/圆点，点击进详情） */
export function Wall() {
  const nav = useNavigate()
  const [tasks, setTasks] = useState<Task[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const trackRef = useRef<HTMLDivElement>(null)
  const [index, setIndex] = useState(0)
  const [paused, setPaused] = useState(false)

  const load = () => {
    setTasks(null); setError(null)
    api.v2.tasks({ sort: 'date_desc', limit: 60 }).then((r) => setTasks(r.tasks))
      .catch((e) => { setError(String((e as Error).message)); setTasks([]) })
  }
  useEffect(() => { load() }, [])

  // 分页：每页 PER_PAGE 张铺满一屏
  const pages: Task[][] = []
  if (tasks) for (let i = 0; i < tasks.length; i += PER_PAGE) pages.push(tasks.slice(i, i + PER_PAGE))
  const n = pages.length

  // onScroll 反算当前页
  const handleScroll = () => {
    const el = trackRef.current
    if (!el || !el.clientWidth) return
    const i = Math.round(el.scrollLeft / el.clientWidth)
    if (i !== index && i >= 0 && i < n) setIndex(i)
  }
  const go = (i: number) => {
    const el = trackRef.current
    if (!el || !el.clientWidth) return
    el.scrollTo({ left: (((i % n) + n) % n) * el.clientWidth, behavior: 'smooth' })
  }
  // 自动翻页 6s；hover 暂停
  useEffect(() => {
    if (paused || n < 2) return
    const t = setInterval(() => go(index + 1 >= n ? 0 : index + 1), 6000)
    return () => clearInterval(t)
  }, [paused, index, n])

  if (error) return <div className="page"><ErrorEmpty message={error} onRetry={load} /></div>
  if (tasks === null) return <div className="page wall-loading"><Loading label="正在挂今晚的影视墙…" /></div>
  if (tasks.length === 0) return <div className="page"><Empty icon="♡" title="墙还空着" sub="去把她们带回来——先扫描列表源，或按番号创建任务。" /></div>

  return (
    <div className="wall-page" onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)}>
      {/* 顶部浮动标识 */}
      <div className="wall-head">
        <span className="wall-title">首页<em> · 今夜影视墙</em></span>
        <span className="wall-sub">{tasks.length} 部作品 · 自动翻页</span>
      </div>

      <div className="wall-track" ref={trackRef} onScroll={handleScroll}>
        {pages.map((page, pi) => (
          <div className="wall-slide" key={pi}>
            {page.map((t, i) => {
              const remote = t.poster_url || (() => { try { return JSON.parse(t.thumbnail_urls || '[]')[0] } catch { return null } })()
              return (
                <div className="wall-cell" key={t.id} onClick={() => nav(`/task/${t.id}`)}
                  role="button" tabIndex={0} aria-label={`查看 ${t.video_code || '作品'} 详情`}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); nav(`/task/${t.id}`) } }}>
                  <img src={coverFileUrl(t.id)} alt={t.video_code || ''} loading="lazy" referrerPolicy="no-referrer"
                    style={{ '--d': `${(i % 6) * 1.8}s` } as React.CSSProperties}
                    onError={(e) => { if (remote && e.currentTarget.src !== remote) e.currentTarget.src = remote; else e.currentTarget.style.visibility = 'hidden' }} />
                  <span className="wall-cap">{t.video_code || '—'}</span>
                  {t.is_favorite ? <span className="wall-fav">♥</span> : null}
                </div>
              )
            })}
          </div>
        ))}
      </div>

      {n > 1 && (<>
        <button className="wall-nav wall-nav--prev" aria-label="上一页" onClick={() => go(index - 1)}>‹</button>
        <button className="wall-nav wall-nav--next" aria-label="下一页" onClick={() => go(index + 1)}>›</button>
        <div className="wall-dots">
          {pages.map((_, i) => (
            <button key={i} className={`wall-dot${i === index ? ' on' : ''}`} aria-label={`第 ${i + 1} 页`} onClick={() => go(i)} />
          ))}
        </div>
      </>)}
    </div>
  )
}
