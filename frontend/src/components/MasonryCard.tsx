interface Props {
  x: number
  y: number
  w: number
  src: string
  remote: string | null
  title: string
  code: string | null
  year?: string | null
  rating?: string | number | null
  onLoad: (e: React.SyntheticEvent<HTMLImageElement>) => void
  onClick: () => void
  onKeyDown: (e: React.KeyboardEvent) => void
  ariaLabel: string
  onMouseEnter?: (el: HTMLElement) => void
  onMouseLeave?: () => void
}

/** 海报卡（JS masonry 定位）：图片 + 标题 + 附加信息行，hover 上浮 */
export function MasonryCard(p: Props) {
  return (
    <div className="mcard" role="button" tabIndex={0} aria-label={p.ariaLabel}
      style={{ left: p.x, top: p.y, width: p.w }}
      onClick={p.onClick} onKeyDown={p.onKeyDown}
      onMouseEnter={(e) => p.onMouseEnter?.(e.currentTarget)} onMouseLeave={() => p.onMouseLeave?.()}>
      <img src={p.src} alt={p.code || ''} loading="lazy" decoding="async" referrerPolicy="no-referrer"
        onLoad={p.onLoad}
        onError={(e) => { if (p.remote && e.currentTarget.src !== p.remote) e.currentTarget.src = p.remote; else e.currentTarget.style.opacity = '0.2' }} />
      <div className="mcard-info">
        <div className="mcard-title" title={p.title}>{p.title}</div>
        <div className="mcard-sub">
          <span>{p.code || '—'}</span>
          {p.year ? <span>· {p.year}</span> : null}
          {p.rating ? <span>· ★ {p.rating}</span> : null}
        </div>
      </div>
    </div>
  )
}
