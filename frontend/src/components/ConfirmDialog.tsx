import { useEffect } from 'react'
import { useStore } from '../store/useStore'

/** 全局应用内确认弹窗 —— 替代原生 confirm，由 store.confirm() 触发 */
export function ConfirmDialog() {
  const d = useStore((s) => s.confirmDialog)
  const resolveConfirm = useStore((s) => s.resolveConfirm)

  useEffect(() => {
    if (!d.open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') resolveConfirm(false)
      if (e.key === 'Enter') resolveConfirm(true)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [d.open, resolveConfirm])

  if (!d.open) return null
  return (
    <div className="cd-overlay" onClick={() => resolveConfirm(false)}>
      <div className="cd-card" role="alertdialog" aria-modal="true" aria-label={d.title}
        onClick={(e) => e.stopPropagation()}>
        <div className="cd-title">{d.title}</div>
        <div className="cd-message">{d.message}</div>
        <div className="cd-actions">
          <button className="btn btn--ghost btn--sm" onClick={() => resolveConfirm(false)}>再想想</button>
          <button className={`btn btn--sm${d.danger ? ' btn--danger' : ' btn--gold'}`}
            autoFocus onClick={() => resolveConfirm(true)}>确认</button>
        </div>
      </div>
    </div>
  )
}
