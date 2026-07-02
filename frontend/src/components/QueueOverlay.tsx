/** 浮动队列进度条 — Library / Rankings 共用 */
interface QueueInfo {
  current: number
  total: number
  current_video_code: string | null
  stage: string
  done: number[]
  failed: number[]
}

export function QueueOverlay({ info }: { info: QueueInfo }) {
  return (
    <div style={{
      position: 'fixed', top: 24, left: '50%', transform: 'translateX(-50%)',
      background: 'var(--bg-raised)', border: '1px solid var(--gold-glow)',
      borderRadius: 'var(--r-md)', padding: '12px 24px',
      boxShadow: 'var(--shadow-float)', zIndex: 300,
      display: 'flex', alignItems: 'center', gap: 16,
    }}>
      <div className="crawl-ring" style={{ width: 48, height: 48, margin: 0 }}>
        <svg width="48" height="48" viewBox="0 0 48 48">
          <circle cx="24" cy="24" r="20" fill="none" stroke="var(--line-soft)" strokeWidth="4" />
          <circle cx="24" cy="24" r="20" fill="none" stroke="var(--gold)" strokeWidth="4"
            strokeDasharray={2 * Math.PI * 20}
            strokeDashoffset={2 * Math.PI * 20 * (1 - (info.current || 0) / Math.max(info.total, 1))}
            strokeLinecap="round" transform="rotate(-90 24 24)" />
        </svg>
        <div className="pct" style={{ fontSize: 10 }}>
          <b style={{ fontSize: 14 }}>{info.current}/{info.total}</b>
        </div>
      </div>
      <div>
        <div style={{ fontSize: 13, color: 'var(--t-display)', fontWeight: 600 }}>
          {info.current_video_code || '处理中'}
        </div>
        <div style={{ fontSize: 11, color: 'var(--t-mute)' }}>
          {info.stage} · 成功 {info.done?.length || 0} · 失败 {info.failed?.length || 0}
        </div>
      </div>
    </div>
  )
}
