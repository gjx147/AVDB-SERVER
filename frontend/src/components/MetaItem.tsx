/** 元数据标签-值对 —— TaskDetail / 通用 */
export function MetaItem({ label, val }: { label: string; val?: string | null }) {
  if (!val) return null
  return (
    <div className="dm-item">
      <span className="dm-label">{label}</span>
      <span className="dm-val">{val}</span>
    </div>
  )
}
