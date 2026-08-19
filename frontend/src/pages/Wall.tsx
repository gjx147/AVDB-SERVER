import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, coverFileUrl } from '../api/client'
import type { Task } from '../api/types'
import { Loading, Empty, ErrorEmpty } from '../components/States'
import { DailyReveal } from '../components/DailyReveal'
import { useStore } from '../store/useStore'
import { useWhisper, effectiveTier, isNight } from '../i18n/whisper'
import { audio } from '../audio/engine'

/** 凝视运镜表：每张随机一种（12–18s），key=index 切换自动重播 */
const MOVES = ['gz-zoomIn', 'gz-zoomOut', 'gz-panL', 'gz-panR'] as const

/** AI 耳语缓存（task+tone+night → 情话；失败静默回退静态池） */
const aiCache = new Map<string, string>()

/** 首页 · 影视墙 —— 满屏封面轮播 + 凝视运镜 + 拉焦进场 + AI 耳语 + 全屏氛围背景（Eros V3） */
export function Wall() {
  const nav = useNavigate()
  const w = useWhisper()
  const moodMode = useStore((s) => s.moodMode)
  const [tasks, setTasks] = useState<Task[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [index, setIndex] = useState(0)
  const [paused, setPaused] = useState(false)
  const [tall, setTall] = useState(false)  // 竖版封面：contain 完整显示（横版 cover 满屏）
  const [tallRatio, setTallRatio] = useState<number | null>(null)  // 竖图宽高比（信息层贴图定位用）
  const [tallGeom, setTallGeom] = useState<{ left: number; w: number } | null>(null)
  const [aiLine, setAiLine] = useState<string | null>(null)
  const touchX = useRef<number | null>(null)
  const pageRef = useRef<HTMLDivElement | null>(null)

  // 切换作品时重置方向检测（等 onLoad 重新判定）+ 换片掀纱音
  useEffect(() => { setTall(false); setTallRatio(null); setTallGeom(null); audio.play('veil', 0.6) }, [index])

  // 竖版 contain 时计算图片实际显示区域（居中），信息层贴图左缘+图底
  // 注意：wall-page 在 .main 内（左侧有侧边栏），必须用容器实际尺寸而非 window 尺寸
  useEffect(() => {
    const recalc = () => {
      if (tallRatio == null) return
      const el = pageRef.current
      const W = el ? el.clientWidth : window.innerWidth
      const H = el ? el.clientHeight : window.innerHeight
      const w = H * tallRatio
      const left = (W - w) / 2
      // 接近横图比例时退化为全宽贴视口（避免信息层过窄）
      setTallGeom(left > 8 ? { left, w } : { left: 0, w: W })
    }
    recalc()
    window.addEventListener('resize', recalc)
    return () => window.removeEventListener('resize', recalc)
  }, [tallRatio])

  // AI 耳语：换片时按 task+tone+night 取一句情话（缓存/失败回退静态池）
  const currentId = tasks && tasks[index] ? tasks[index].id : null
  useEffect(() => {
    if (!currentId) return
    const tone = effectiveTier()
    const night = isNight()
    const key = `${currentId}:${tone}:${night ? 1 : 0}`
    const cached = aiCache.get(key)
    if (cached !== undefined) { setAiLine(cached || null); return }
    let alive = true
    setAiLine(null)
    api.ai.whisper(currentId, tone, night)
      .then((r) => {
        if (!alive) return
        const line = r.ok ? (r.line || '').trim() : ''
        aiCache.set(key, line)
        if (line) setAiLine(line)
      })
      .catch(() => { if (alive) aiCache.set(key, '') })
    return () => { alive = false }
  }, [currentId, moodMode])

  const load = () => {
    setTasks(null); setError(null)
    api.v2.tasks({ sort: 'date_desc', limit: 60 }).then((r) => setTasks(r.tasks))
      .catch((e) => { setError(String((e as Error).message)); setTasks([]) })
  }
  useEffect(() => { load() }, [])

  const n = tasks?.length || 0
  const go = (i: number) => { if (n > 0) setIndex(((i % n) + n) % n) }

  // 自动轮播：日常 6s，密室模式切 12s 慢节奏；hover 暂停
  useEffect(() => {
    if (paused || n < 2) return
    const interval = moodMode ? 12000 : 6000
    const t = setInterval(() => setIndex((v) => (v + 1 >= n ? 0 : v + 1)), interval)
    return () => clearInterval(t)
  }, [paused, n, moodMode])

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
  if (tasks === null) return <div className="page wall-loading"><Loading label={w('loading_wall')} /></div>
  if (tasks.length === 0) return <div className="page"><Empty icon="♡" title={w('empty_wall_title')} sub={w('empty_wall_sub')} /></div>

  const t = tasks[index]
  const move = MOVES[index % MOVES.length]
  const remote = t.poster_url || (() => { try { return JSON.parse(t.thumbnail_urls || '[]')[0] } catch { return null } })()
  // 简介：synopsis 优先，无则标签兜底
  const synopsis = (t.synopsis || t.description || '').trim() ||
    (t.tags ? t.tags.split(',').map((x) => x.trim()).filter(Boolean).join(' · ') : '')

  return (
    <div className="wall-page" ref={pageRef} onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)}
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
        <span className="wall-sub">{n} 部作品 · {moodMode ? '密室慢舞' : '自动轮播'}</span>
      </div>

      {/* 今夜情人 · 盲盒揭幕入口 */}
      <DailyReveal />

      {/* 满屏封面 + 左下角简介（key 切换触发拉焦进场；凝视运镜类随 index 变化）
          信息层始终贴在封面图左下角：横图=视口左下，竖图 contain 时贴图左缘 */}
      <div className={`wstage${tall ? ' is-tall' : ''}`} key={index} onClick={() => nav(`/task/${t.id}`)}
        role="button" tabIndex={0} aria-label={`查看 ${t.video_code || '作品'} 详情`}
        style={tallGeom ? ({ '--tall-left': `${tallGeom.left}px`, '--tall-w': `${tallGeom.w}px` } as React.CSSProperties) : undefined}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); nav(`/task/${t.id}`) } }}>
        <div className={`wfull ${move}`}>
          <img src={coverFileUrl(t.id)} alt={t.video_code || ''} referrerPolicy="no-referrer"
            style={{ objectFit: tall ? 'contain' : 'cover' }}
            onLoad={(e) => { const im = e.currentTarget; const isTall = im.naturalHeight > im.naturalWidth; setTall(isTall); setTallRatio(isTall ? im.naturalWidth / im.naturalHeight : null) }}
            onError={(e) => { if (remote && e.currentTarget.src !== remote) e.currentTarget.src = remote; else e.currentTarget.style.opacity = '0.15' }} />
          {t.is_favorite ? <span className="wslide-fav">♥</span> : null}
        </div>
        {/* 左下角信息层：错峰显影（番号→标题→简介→元信息）；
            简介位优先 AI 耳语情话，无则 synopsis/静态池兜底 */}
        <div className="winfo">
          <div className="winfo-code">{t.video_code || '—'}</div>
          <div className="winfo-title">{t.title || '未命名'}</div>
          {(aiLine || synopsis) && <div className="winfo-syn">{aiLine || synopsis}</div>}
          {!aiLine && !synopsis && <div className="winfo-syn">{w('carousel_line')}</div>}
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
        {/* 底部进度（心搏步进）+ 计数 */}
        <div className="wslide-prog">
          <div className="wslide-prog-track"><div className="wslide-prog-fill" style={{ width: `${((index + 1) / n) * 100}%` }} /></div>
          <span className="wslide-count">{index + 1} / {n}</span>
        </div>
      </>)}
    </div>
  )
}
