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
}

/** 影片库海报卡 —— 点击整卡进入详情页；左上角复选框用于批量选择 */
export function PosterCard({ task, selected, selectable, onToggle, onClick }: Props) {
  const nav = useNavigate()
  const [bs, label] = statusMap[task.status] || statusMap.pending
  const tags = task.tags ? task.tags.split(',').map((t) => t.trim()).filter(Boolean) : []

  const open = () => { if (onClick) { onClick() } else { nav(`/task/${task.id}`) } }
  const toggle = (e: React.MouseEvent) => { e.stopPropagation(); onToggle?.() }
  const handleKeyDown = (e: React.KeyboardEvent) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open() } }

  return (
    <div className="poster" onClick={open} onKeyDown={handleKeyDown} tabIndex={0} role="button"
      aria-label={`查看 ${task.video_code || '未命名'} 的详情`} style={{ cursor: 'pointer' }}>
      <div className="poster-frame" style={selected ? { boxShadow: '0 0 0 3px var(--gold), 0 8px 24px rgba(232,93,138,.25)' } : undefined}>
        {/* 本地缓存高清封面，无缓存时显示占位 */}
        <img
          src={`${coverFileUrl(task.id)}?v=${task.updated_at || '0'}`}
          alt={task.video_code || ''}
          loading="lazy"
          onLoad={(e) => { e.currentTarget.classList.add('loaded') }}
          onError={(e) => { e.currentTarget.style.display = 'none' }}
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
