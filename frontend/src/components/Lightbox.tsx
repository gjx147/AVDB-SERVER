import { useEffect } from 'react'

interface Props {
  src: string
  alt: string
  onClose: () => void
  onPrev?: () => void
  onNext?: () => void
  counter?: string
}

/** 全屏悬浮看图：点击遮罩/ESC 关闭，‹ › 或 ←/→ 切换（预览图模式） */
export function Lightbox({ src, alt, onClose, onPrev, onNext, counter }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      if (onPrev && e.key === 'ArrowLeft') { e.preventDefault(); onPrev() }
      if (onNext && e.key === 'ArrowRight') { e.preventDefault(); onNext() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, onPrev, onNext])
  const navBtn: React.CSSProperties = {
    position: 'fixed', top: '50%', transform: 'translateY(-50%)', width: 44, height: 44,
    borderRadius: '50%', border: '1px solid rgba(255,255,255,.35)', background: 'rgba(0,0,0,.45)',
    color: '#fff', fontSize: 20, lineHeight: 1, cursor: 'pointer', zIndex: 202,
  }
  return (
    <div onClick={onClose} role="dialog" aria-modal="true" aria-label="图片查看"
      style={{ position: 'fixed', inset: 0, zIndex: 200, background: 'rgba(10,4,8,.92)',
        backdropFilter: 'blur(10px)', display: 'flex', alignItems: 'center', justifyContent: 'center',
        cursor: 'zoom-out' }}>
      <img src={src} alt={alt} onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: '94vw', maxHeight: '92vh', objectFit: 'contain', borderRadius: 8,
          boxShadow: '0 10px 60px rgba(0,0,0,.65)', cursor: 'default' }} />
      {counter && (
        <span onClick={(e) => e.stopPropagation()}
          style={{ position: 'fixed', bottom: 18, left: '50%', transform: 'translateX(-50%)',
            fontFamily: 'var(--ff-mono)', fontSize: 12, color: 'rgba(255,255,255,.85)', cursor: 'default', zIndex: 202 }}>
          {counter}
        </span>
      )}
      {onPrev && (
        <button aria-label="上一张" onClick={(e) => { e.stopPropagation(); onPrev() }} style={{ ...navBtn, left: 18 }}>‹</button>
      )}
      {onNext && (
        <button aria-label="下一张" onClick={(e) => { e.stopPropagation(); onNext() }} style={{ ...navBtn, right: 18 }}>›</button>
      )}
      <button aria-label="关闭" onClick={onClose}
        style={{ position: 'fixed', top: 16, right: 18, width: 36, height: 36, borderRadius: '50%',
          border: '1px solid rgba(255,255,255,.35)', background: 'rgba(0,0,0,.45)', color: '#fff',
          fontSize: 16, cursor: 'pointer', zIndex: 202 }}>×</button>
    </div>
  )
}
