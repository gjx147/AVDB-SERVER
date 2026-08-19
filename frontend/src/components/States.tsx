import { isValidElement, type ReactNode } from 'react'
import { useWhisper, navEyebrow, useNavMode } from '../i18n/whisper'
import { useStore } from '../store/useStore'

export function Loading({ label }: { label?: string }) {
  const w = useWhisper()
  return <div className="loading">{label ?? w('loading')}</div>
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
  const w = useWhisper()
  return (
    <div className="empty">
      <div className="em-icon" style={{ color: 'var(--red)' }}>⚠</div>
      <div className="em-title" style={{ color: 'var(--red)' }}>{w('err_load')}</div>
      <div style={{ fontSize: 13, color: 'var(--t-mute)', maxWidth: 400, textAlign: 'center', margin: '4px 0 12px' }}>
        {message || w('err_network')}
      </div>
      {onRetry && (
        <button className="btn btn--ghost btn--sm" onClick={onRetry}>重新加载</button>
      )}
    </div>
  )
}

/** 提取 ReactNode 的纯文本（title 为 <>影片<em>库</em></> 时得"影片库"） */
function nodeText(n: ReactNode): string {
  if (n == null || typeof n === 'boolean') return ''
  if (typeof n === 'string' || typeof n === 'number') return String(n)
  if (Array.isArray(n)) return n.map(nodeText).join('')
  if (isValidElement(n)) return nodeText((n.props as { children?: ReactNode }).children)
  return ''
}

export function PageHead({
  eyebrow, title, sub, children,
}: { eyebrow: string; title: ReactNode; sub?: string; children?: ReactNode }) {
  const navMode = useStore((s) => s.navMode)
  const nl = useNavMode()
  // 情话模式：页面标题与眉题联动切换（映射词拆末字做金色强调，保持视觉一致）
  let titleNode = title
  let eyebrowNode = eyebrow
  if (navMode === 'whisper') {
    const mapped = nl(nodeText(title))
    if (mapped !== nodeText(title) && mapped.length >= 2) {
      titleNode = <>{mapped.slice(0, -1)}<em>{mapped.slice(-1)}</em></>
    }
    eyebrowNode = navEyebrow(eyebrow)
  }
  return (
    <div className="page-head">
      <div>
        <div className="eyebrow">{eyebrowNode}</div>
        <h1 className="page-title">{titleNode}</h1>
        {sub && <p className="page-sub">{sub}</p>}
      </div>
      {children && <div className="page-actions">{children}</div>}
    </div>
  )
}
