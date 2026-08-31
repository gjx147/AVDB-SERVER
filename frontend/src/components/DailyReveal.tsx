/** 今夜情人 —— 盲盒揭幕仪式（Boudoir Phase 3）。
 *  丝绒红包囊 → 黑场烛息 → 剪影悬念 3s → 三级对焦揭晓（60→18→0px）→ 番号落款。
 *  候选池 = 评分 Top20；真随机（共用随机工具）——每次揭幕随机一位。 */
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, coverFileUrl, withImageAuth } from '../api/client'
import type { Task } from '../api/types'
import { useWhisper } from '../i18n/whisper'
import { audio } from '../audio/engine'
import { pickOne } from '../utils/random'

type Phase = 'idle' | 'silhouette' | 'f1' | 'f2' | 'f3' | 'done'

export function DailyReveal() {
  const nav = useNavigate()
  const w = useWhisper()
  const [tasks, setTasks] = useState<Task[] | null>(null)
  const [phase, setPhase] = useState<Phase>('idle')
  const [pick, setPick] = useState<Task | null>(null)
  const timers = useRef<number[]>([])

  useEffect(() => {
    api.v2.tasks({ sort: 'rating_desc', limit: 20 }).then((r) => setTasks(r.tasks)).catch(() => setTasks([]))
    return () => { timers.current.forEach(clearTimeout) }
  }, [])

  // 揭幕完成：杯碰一声，像酒杯轻轻相碰
  useEffect(() => { if (phase === 'done') audio.play('chime', 1) }, [phase])

  const start = () => {
    if (!tasks || tasks.length === 0) return
    setPick(pickOne(tasks) ?? null)
    setPhase('silhouette')
    timers.current.forEach(clearTimeout)
    timers.current = []
    const at = (ms: number, fn: () => void) => { timers.current.push(window.setTimeout(fn, ms)) }
    at(3000, () => setPhase('f1'))   // 剪影悬念 3s
    at(3800, () => setPhase('f2'))   // 三级对焦，每级心搏间隔
    at(4600, () => setPhase('f3'))
    at(5300, () => setPhase('done'))
  }
  const reroll = () => {
    if (!tasks || tasks.length === 0) return
    setPick(pickOne(tasks) ?? null)
    setPhase('f1')
    timers.current.forEach(clearTimeout)
    timers.current = []
    const at = (ms: number, fn: () => void) => { timers.current.push(window.setTimeout(fn, ms)) }
    at(800, () => setPhase('f2'))
    at(1600, () => setPhase('f3'))
    at(2300, () => setPhase('done'))
  }

  const close = () => {
    timers.current.forEach(clearTimeout)
    timers.current = []
    setPhase('idle')
  }

  // ESC 或点背景关闭舞台（保留"就是她了/再换一位"路径）
  useEffect(() => {
    if (phase === 'idle') return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [phase])

  if (phase === 'idle') {
    return (
      <button className="reveal-pouch" onClick={start} disabled={!tasks || tasks.length === 0}
        title={w('blindbox')}>
        <span className="pouch-gem" aria-hidden="true">💎</span>
        今夜情人
      </button>
    )
  }
  if (!pick) return null
  const cls = phase === 'silhouette' ? 'silhouette' : phase

  return (
    <div className="reveal-stage" role="dialog" aria-label="今夜情人揭幕" onClick={(e) => { if (e.target === e.currentTarget) close() }}>
      <div className="reveal-inner">
        <div className={`reveal-cover ${cls}`} onClick={() => nav(`/task/${pick.id}`)} style={{ cursor: 'pointer' }}
          role="button" tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter') nav(`/task/${pick.id}`) }}>
          <span className="reveal-candle" aria-hidden="true">🕯</span>
          <img src={withImageAuth(coverFileUrl(pick.id))} alt="" referrerPolicy="no-referrer"
            onError={(e) => {
              const r = pick.poster_url || (() => { try { return JSON.parse(pick.thumbnail_urls || '[]')[0] } catch { return null } })()
              if (r && e.currentTarget.src !== r) e.currentTarget.src = r
            }} />
        </div>
        {(phase === 'done') && (<>
          <div className="reveal-code">{pick.video_code || '—'}</div>
          <div className="reveal-title">{w('blindbox_result')} · {pick.title || '未命名'}{pick.actors ? ` · ${pick.actors.split(',')[0].trim()}` : ''}</div>
          <div className="reveal-actions">
            <button className="btn btn--gold" onClick={() => nav(`/task/${pick.id}`)}>就是她了 →</button>
            <button className="btn btn--ghost" onClick={reroll}>再换一位</button>
          </div>
        </>)}
        <div className="reveal-hint">TONIGHT'S PICK</div>
      </div>
    </div>
  )
}
