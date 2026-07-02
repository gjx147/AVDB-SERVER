import { useEffect } from 'react'
import { useStore } from '../store/useStore'

/** 全局 Toast —— 由 store 触发 */
export function Toast() {
  const toast = useStore((s) => s.toast)
  useEffect(() => {
    if (toast) {
      const t = setTimeout(() => useStore.setState({ toast: null }), 2600)
      return () => clearTimeout(t)
    }
  }, [toast])
  if (!toast) return null
  return (
    <div className={`toast show${toast.err ? ' err' : ''}`} key={toast.key}
         role="status" aria-live="polite" aria-atomic="true">
      <span className="ic">{toast.err ? '⚠' : '✓'}</span>
      <span>{toast.msg}</span>
    </div>
  )
}
