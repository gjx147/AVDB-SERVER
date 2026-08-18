import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, coverFileUrl } from '../api/client'
import type { Task } from '../api/types'
import { Loading, Empty, ErrorEmpty } from '../components/States'

/** 首页 · 影视墙 —— 单张大横图轮播（完整封面 + 切换特效 + 全屏模糊背景氛围） */
export function Wall() {
  const nav = useNavigate()
  const [tasks, setTasks] = useState<Task[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [index, setIndex] = useState(0)
  const [paused, setPaused] = useState(false)
  const touchX = useRef<number | null>(null)

  const load = () => {
    setTasks(null); setError(null)
    api.v2.tasks({ sort: 'date_desc', limit: 60 }).then((r) => setTasks(r.tasks))
      .catch((e) => { setError(String((e as Error).message)); setTasks([]) })
  }
  useEffect(() => { load() }, [])

  const n = tasks?.length || 0
  const go = (i: number) => { if (n > 0) setIndex(((i % n) + n) % n) }

  // 自动轮播 6s；hover 暂停
  useEffect(() => {
    if (paused || n < 2) return
    const t = setInterval(() => setIndex((v) => (v + 1 >= n ? 0 : v + 1)), 6000)
    return () => clearInterval(t)
  }, [paused, n])

  // 键盘 ←/→ 切换
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft') go(index - 1)
      if (e.key === 'ArrowRight') go(index + 1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [index, n])

  if (error) return <div className="page"><ErrorEmpty message={error} onRetry={load} /></div>
  if (tasks === null) return <div className="page wall-loading"><Loading label="正在挂今晚的影视墙…" /></div>
  if (tasks.length === 0) return <div className="page"><Empty icon="♡" title="墙还空着" sub="去把她们带回来——先扫描列表源，或按番号创建任务。" /></div>

  const t = tasks[index]
  const remote = t.poster_url || (() => { try { return JSON.parse(t.thumbnail_urls || '[]')[0] } catch { return null } })()
  // 简介：synopsis 优先，无则标签兜底
  const synopsis = (t.synopsis || t.description || '').trim() ||
    (t.tags ? t.tags.split(',').map((x) => x.trim()).filter(Boolean).join(' · ') : '')

  return (
    <div className="wall-page" onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)}
      onTouchStart={(e) => { touchX.current = e.touches[0].clientX }}
      onTouchEnd={(e) => {
        if (touchX.current == null) return
        const dx = e.changedTouches[0].clientX - touchX.current
        if (Math.abs(dx) > 50) go(dx < 0 ? index + 1 : index - 1)
        touchX.current = null
      }}>
      {/* 全屏模糊背景（图片加载期/失败时的氛围兜底） */}
      <div className="wbg" aria-hidden="true">
        <img key={index} src={coverFileUrl(t.id)} alt="" referrerPolicy="no-referrer"
          onError={(e) => { if (remote) e.currentTarget.src = remote; else e.currentTarget.style.opacity = '0' }} />
      </div>

      {/* 顶部浮动标识 */}
      <div className="wall-head">
        <span className="wall-title">首页<em> · 今夜影视墙</em></span>
        <span className="wall-sub">{n} 部作品 · 自动轮播</span>
      </div>

      {/* 满屏封面 + 左下角简介（key 切换触发入场特效） */}
      <div className="wstage" key={index} onClick={() => nav(`/task/${t.id}`)}
        role="button" tabIndex={0} aria-label={`查看 ${t.video_code || '作品'} 详情`}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); nav(`/task/${t.id}`) } }}>
        <div className="wfull">
          <img src={coverFileUrl(t.id)} alt={t.video_code || ''} referrerPolicy="no-referrer"
            onError={(e) => { if (remote && e.currentTarget.src !== remote) e.currentTarget.src = remote; else e.currentTarget.style.opacity = '0.15' }} />
          {t.is_favorite ? <span className="wslide-fav">♥</span> : null}
        </div>
        {/* 左下角信息层：深色渐变遮罩保证可读 */}
        <div className="winfo">
          <div className="winfo-code">{t.video_code || '—'}</div>
          <div className="winfo-title">{t.title || '未命名'}</div>
          {synopsis && <div className="winfo-syn">{synopsis}</div>}
          <div className="winfo-meta">
            {t.rating ? <span className="winfo-score">♥ {t.rating}</span> : null}
            {t.release_date && <span>{t.release_date.slice(0, 4)}</span>}
            {t.actors && <span>{t.actors.split(',')[0].trim()}</span>}
            <span className="winfo-more">查看详情 →</span>
          </div>
        </div>
      </div>

      {n > 1 && (<>
        <button className="wall-nav wall-nav--prev" aria-label="上一张" onClick={() => go(index - 1)}>‹</button>
        <button className="wall-nav wall-nav--next" aria-label="下一张" onClick={() => go(index + 1)}>›</button>
        {/* 底部进度 + 计数 */}
        <div className="wslide-prog">
          <div className="wslide-prog-track"><div className="wslide-prog-fill" style={{ width: `${((index + 1) / n) * 100}%` }} /></div>
          <span className="wslide-count">{index + 1} / {n}</span>
        </div>
      </>)}
    </div>
  )
}
