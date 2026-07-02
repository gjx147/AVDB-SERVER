import type { ReactNode } from 'react'

export function Loading({ label = '加载中…' }: { label?: string }) {
  return <div className="loading">{label}</div>
}

export function Empty({ icon = '◯', title, sub }: { icon?: string; title: string; sub?: string }) {
  return (
    <div className="empty">
      <div className="em-icon">{icon}</div>
      <div className="em-title">{title}</div>
      {sub && <div style={{ fontSize: 13 }}>{sub}</div>}
    </div>
  )
}

export function ErrorEmpty({ message, onRetry }: { message?: string; onRetry?: () => void }) {
  return (
    <div className="empty">
      <div className="em-icon" style={{ color: 'var(--red)' }}>⚠</div>
      <div className="em-title" style={{ color: 'var(--red)' }}>加载失败</div>
      <div style={{ fontSize: 13, color: 'var(--t-mute)', maxWidth: 400, textAlign: 'center', margin: '4px 0 12px' }}>
        {message || '无法连接到服务器，请检查网络后重试。'}
      </div>
      {onRetry && (
        <button className="btn btn--ghost btn--sm" onClick={onRetry}>重新加载</button>
      )}
    </div>
  )
}

export function PageHead({
  eyebrow, title, sub, children,
}: { eyebrow: string; title: ReactNode; sub?: string; children?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1 className="page-title">{title}</h1>
        {sub && <p className="page-sub">{sub}</p>}
      </div>
      {children && <div className="page-actions">{children}</div>}
    </div>
  )
}
