/** 骨架屏加载态 —— 替代文字 Loading，画廊场景用海报形状骨架更贴合。 */

/** 海报画廊骨架：N 张 7:10 竖版占位卡，呼吸闪动 */
export function SkeletonGallery({ count = 12, square = false }: { count?: number; square?: boolean }) {
  return (
    <div className="gallery" aria-busy="true" aria-label="加载中">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className={`skeleton-card${square ? ' skeleton-card--square' : ''}`}
          style={{ animationDelay: `${(i % 8) * 60}ms` }} />
      ))}
    </div>
  )
}

/** 行列表骨架（下载历史/收藏行视图用） */
export function SkeletonRows({ count = 6 }: { count?: number }) {
  return (
    <div className="card" aria-busy="true" aria-label="加载中">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="skeleton-row" style={{ animationDelay: `${(i % 6) * 80}ms` }}>
          <div className="skeleton-row-thumb" />
          <div className="skeleton-row-lines">
            <div className="skeleton-line" style={{ width: '30%' }} />
            <div className="skeleton-line" style={{ width: '70%' }} />
          </div>
        </div>
      ))}
    </div>
  )
}
