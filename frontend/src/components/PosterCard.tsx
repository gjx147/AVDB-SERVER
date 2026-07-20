import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { coverFileUrl } from '../api/client'
import type { Task } from '../api/types'

const statusMap = {
  visited: ['bs-visited', '已入库'],
  pending: ['bs-pending', '待处理'],
  failed: ['bs-failed', '失败'],
} as const

const dlStatusMap: Record<string, { icon: string; cls: string; label: string }> = {
  completed: { icon: '📥', cls: 'dl-completed', label: '已下载' },
  downloading: { icon: '⏳', cls: 'dl-downloading', label: '下载中' },
  pushed: { icon: '📤', cls: 'dl-pushed', label: '已推送' },
  failed: { icon: '❌', cls: 'dl-failed', label: '下载失败' },
}

interface Props {
  task: Task
  selected?: boolean
  selectable?: boolean
  onToggle?: () => void
  onClick?: () => void  // 可选：自定义点击行为（Rankings 用）
  centerImage?: boolean  // 可选：图片居中裁剪（演员方形头像用，默认 right center 影片封面）
}

/** 影片库海报卡 —— 点击整卡进入详情页；左上角复选框用于批量选择 */
export function PosterCard({ task, selected, selectable, onToggle, onClick, centerImage }: Props) {
  const nav = useNavigate()
  const [bs, label] = statusMap[task.status] || statusMap.pending
  const tags = task.tags ? task.tags.split(',').map((t) => t.trim()).filter(Boolean) : []

  // 与详情页封面统一：优先 poster_url（横版 covers/，CSS 用 right center 裁剪主角），
  // 兜底 thumbnail_urls[0]（竖版 samples/，无裁剪也能显示）
  const remoteCover = (() => {
    if (task.poster_url) return task.poster_url
    if (task.thumbnail_urls) {
      try {
        const arr = JSON.parse(task.thumbnail_urls)
        if (Array.isArray(arr) && arr.length > 0) return arr[0]
      } catch { /* ignore */ }
    }
    return null
  })()

  // 图片源：先试本地缓存，失败后 fallback 到远程
  const [imgSrc, setImgSrc] = useState(`${coverFileUrl(task.id)}?v=${task.updated_at || '0'}`)
  const [triedRemote, setTriedRemote] = useState(false)

  const open = () => { if (onClick) { onClick() } else { nav(`/task/${task.id}`) } }
  const toggle = (e: React.MouseEvent) => { e.stopPropagation(); onToggle?.() }
  const handleKeyDown = (e: React.KeyboardEvent) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open() } }

  const handleImgError = (e: React.MouseEvent<HTMLImageElement>) => {
    // 本地缓存失败 → 尝试远程封面
    if (!triedRemote && remoteCover) {
      setTriedRemote(true)
      setImgSrc(remoteCover)
    } else {
      e.currentTarget.style.display = 'none'
    }
  }

  return (
    <div className="poster" onClick={open} onKeyDown={handleKeyDown} tabIndex={0} role="button"
      aria-label={`查看 ${task.video_code || '未命名'} 的详情`} style={{ cursor: 'pointer' }}>
      <div className="poster-frame" style={selected ? { boxShadow: '0 0 0 3px var(--gold), 0 8px 24px rgba(232,93,138,.25)' } : undefined}>
        {/* 优先本地缓存高清封面，无缓存时 fallback 到远程封面 */}
        <img
          src={imgSrc}
          alt={task.video_code || ''}
          loading="lazy"
          style={centerImage ? { objectPosition: 'center center' } : undefined}
          onLoad={(e) => { e.currentTarget.classList.add('loaded') }}
          onError={handleImgError}
        />
        <div className="poster-grad-top">
          <span className="poster-code">{task.video_code || '—'}</span>
          <div style={{ display: 'flex', gap: 6 }}>
            {selectable && (
              <div onClick={toggle} role="checkbox" aria-checked={selected} tabIndex={0}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(e as any) } }}
                style={{
                  width: 26, height: 26, borderRadius: 6, cursor: 'pointer',
                  border: selected ? 'none' : '2.5px solid rgba(255,255,255,.85)',
                  background: selected ? 'var(--gold)' : 'rgba(0,0,0,.35)',
                  color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 15, fontWeight: 700,
                  boxShadow: selected ? '0 0 8px rgba(232,93,138,.4)' : 'none',
                  transition: 'all .2s',
                }}>{selected ? '✓' : ''}</div>
            )}
            {task.is_favorite ? <div className="badge-fav">♥</div> : null}
          </div>
        </div>
        <div className="poster-info">
          <span className={`badge-status ${bs}`}>{label}</span>
          {task.download_status && dlStatusMap[task.download_status] && (
            <span className={`badge-status badge-dl ${dlStatusMap[task.download_status].cls}`} title={dlStatusMap[task.download_status].label}>
              {dlStatusMap[task.download_status].icon} {dlStatusMap[task.download_status].label}
            </span>
          )}
          <div className="poster-title">{task.title || '未命名'}</div>
          <div className="poster-meta">
            {task.release_date && <span>{task.release_date.slice(0, 4)}</span>}
            {task.release_date && <span className="sep">·</span>}
            {task.rating && <span className="poster-score">★ {task.rating}</span>}
          </div>
        </div>
      </div>
      <div className="poster-caption">
        <div className="cap-code">{task.video_code || '—'}</div>
        <div className="cap-actor">{[task.actors, ...tags].filter(Boolean).join(' · ') || '—'}</div>
      </div>
    </div>
  )
}
