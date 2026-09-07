import { useEffect, useState } from 'react'
import { api } from '../api/client'

interface MagnetItem {
  task_id: number
  video_code: string | null
  magnet: string | null
  has_magnet: boolean
}

/** 批量导出磁力弹窗：勾选作品 → 复制全部 / 下载 .txt（粘贴到迅雷/手机迅雷远程批量添加） */
export function MagnetExportModal({ open, taskIds, onClose }: {
  open: boolean
  taskIds: number[]
  onClose: () => void
}) {
  const [items, setItems] = useState<MagnetItem[] | null>(null)
  const [picked, setPicked] = useState<Set<number>>(new Set())
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!open) return
    setItems(null)
    setPicked(new Set())
    setCopied(false)
    api.tasks.batchMagnets(taskIds).then((r) => {
      setItems(r.items)
      setPicked(new Set(r.items.filter((i) => i.has_magnet).map((i) => i.task_id)))
    }).catch(() => setItems([]))
  }, [open, taskIds.join(',')])  // E1：数组引用稳定化，防父级重渲染重置勾选

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  if (!open) return null
  const chosen = (items || []).filter((i) => picked.has(i.task_id) && i.has_magnet)
  const magnetLines = chosen.map((i) => i.magnet).filter(Boolean) as string[]

  const copyAll = async () => {
    const txt = magnetLines.join('\n')
    if (!txt) return
    try {
      await navigator.clipboard.writeText(txt)
    } catch {
      const ta = document.createElement('textarea')
      ta.value = txt
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    }
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }
  const downloadTxt = () => {
    const txt = magnetLines.join('\n')
    if (!txt) return
    const blob = new Blob([txt], { type: 'text/plain;charset=utf-8' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `magnets-${Date.now()}.txt`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  return (
    <div className="cd-overlay" onClick={onClose}>
      <div className="cd-card" role="dialog" aria-modal="true" aria-label="导出磁力" onClick={(e) => e.stopPropagation()}>
        <div className="cd-title">导出磁力</div>
        <div className="cd-message">勾选要导出的作品（仅含磁力的可勾选），复制或下载 .txt 后粘贴到迅雷 / 手机迅雷远程设备批量添加。</div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
          <button className="btn btn--ghost btn--sm" onClick={() => setPicked(new Set((items || []).filter((i) => i.has_magnet).map((i) => i.task_id)))}>全选有磁力</button>
          <button className="btn btn--ghost btn--sm" onClick={() => setPicked(new Set())}>清空</button>
          <span style={{ fontSize: 12, color: 'var(--t-mute)' }}>已选 {chosen.length} 条</span>
        </div>
        <div style={{ maxHeight: 260, overflowY: 'auto', border: '1px solid var(--line, #ddd)', borderRadius: 8, padding: '4px 10px' }}>
          {!items ? (
            <div style={{ fontSize: 12, color: 'var(--t-mute)', padding: '8px 0' }}>加载中…</div>
          ) : items.length === 0 ? (
            <div style={{ fontSize: 12, color: 'var(--t-mute)', padding: '8px 0' }}>无可导出的作品</div>
          ) : items.map((i) => (
            <label key={i.task_id} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, padding: '5px 0', cursor: 'pointer' }}>
              <input type="checkbox" disabled={!i.has_magnet} checked={picked.has(i.task_id)}
                onChange={() => setPicked((prev) => { const n = new Set(prev); if (n.has(i.task_id)) n.delete(i.task_id); else n.add(i.task_id); return n })} />
              <span style={{ minWidth: 90 }}>{i.video_code || '?'}</span>
              <span style={{ fontSize: 12, color: 'var(--t-mute)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 300 }}>{i.magnet || ''}</span>
              {!i.has_magnet && <span style={{ fontSize: 10, color: 'var(--t-mute)' }}>无磁力</span>}
            </label>
          ))}
        </div>
        <div className="cd-actions">
          <button className="btn btn--ghost btn--sm" onClick={onClose}>关闭</button>
          <button className="btn btn--ghost btn--sm" onClick={downloadTxt} disabled={magnetLines.length === 0}>下载 .txt</button>
          <button className="btn btn--gold btn--sm" onClick={copyAll} disabled={magnetLines.length === 0}>{copied ? '已复制' : '复制全部'}</button>
        </div>
      </div>
    </div>
  )
}
