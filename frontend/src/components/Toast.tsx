import { useEffect } from 'react'
import { useStore } from '../store/useStore'

/** 全局 Toast —— 由 store 触发；错误类停 5s，可手动关闭 */
export function Toast() {
  const toast = useStore((s) => s.toast)
  const close = () => useStore.setState({ toast: null })
  useEffect(() => {
    if (toast) {
      const t = setTimeout(close, toast.err ? 5000 : 2600)
      return () => clearTimeout(t)
    }
  }, [toast])
  if (!toast) return null
  return (
    <div className={`toast show${toast.err ? ' err' : ''}`} key={toast.key}
         role={toast.err ? 'alert' : 'status'} aria-live="polite" aria-atomic="true">
      <span className="ic">{toast.err ? '⚠' : '✓'}</span>
      <span>{toast.msg}</span>
      <button className="toast-close" aria-label="关闭提示" onClick={close}>✕</button>
    </div>
  )
}
