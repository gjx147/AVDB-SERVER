import { useEffect, useState } from 'react'

interface Props {
  page: number       // 当前页（1 基）
  totalPages: number
  onPage: (p: number) => void  // 回传 1 基页码
  info?: string      // 中间信息（如 "1-48 / 共 1027 条"）；缺省 "p / total"
}

/** 全站共享分页器：« 第一页 · 上一页 · 信息 · 页码输入跳转 · 下一页 · 最后一页 » */
export function Pager({ page, totalPages, onPage, info }: Props) {
  const [val, setVal] = useState(String(page))
  useEffect(() => { setVal(String(page)) }, [page])
  const tp = Math.max(1, totalPages)
  const jump = () => {
    const p = parseInt(val, 10)
    if (Number.isNaN(p)) { setVal(String(page)); return }
    const clamped = Math.min(Math.max(1, p), tp)
    if (clamped !== page) onPage(clamped)
    else setVal(String(page))
  }
  return (
    <div className="pager" onClick={(e) => e.stopPropagation()}>
      <button disabled={page <= 1} onClick={() => onPage(1)} aria-label="第一页" title="第一页">«</button>
      <button disabled={page <= 1} onClick={() => onPage(page - 1)}>上一页</button>
      <span className="pager-info">{info ?? `${page} / ${tp}`}</span>
      <span className="pager-jump">
        <input className="input" type="number" min={1} max={tp} value={val} aria-label="跳转页码"
          onChange={(e) => setVal(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); jump() } }} />
        <button onClick={jump}>跳转</button>
      </span>
      <button disabled={page >= tp} onClick={() => onPage(page + 1)}>下一页</button>
      <button disabled={page >= tp} onClick={() => onPage(tp)} aria-label="最后一页" title="最后一页">»</button>
    </div>
  )
}
