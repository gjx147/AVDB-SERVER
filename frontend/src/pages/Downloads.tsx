import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { DownloadRecord } from '../api/types'
import { PageHead, Loading, Empty, ErrorEmpty } from '../components/States'
import { useStore } from '../store/useStore'

const statusLabel: Record<string, string> = {
  pushed: '已推送', downloading: '下载中', completed: '已下载', failed: '失败',
}
const statusCls: Record<string, string> = {
  pushed: 'chip-blue', downloading: 'chip-amber', completed: 'chip-green', failed: 'chip-red',
}

export function Downloads() {
  const nav = useNavigate()
  const [data, setData] = useState<{ downloads: DownloadRecord[]; total: number } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState('')
  const toastErr = useStore((s) => s.toastErr)

  const load = useCallback(() => {
    setData(null); setError(null)
    api.downloads.list(filter || undefined).then(setData).catch((e) => { setError(String((e as Error).message)); setData({ downloads: [], total: 0 }) })
  }, [filter])

  useEffect(() => { load() }, [load])
  // 自动刷新（下载中状态会更新）
  useEffect(() => {
    const t = setInterval(load, 15000)
    return () => clearInterval(t)
  }, [load])

  return (
    <div className="page">
      <PageHead eyebrow={`Downloads · ${data?.total ?? 0} 条`} title={<>下载<em>历史</em></>}
        sub="查看所有推送过的下载任务及其状态。下载中状态每 15 秒自动刷新。">
      </PageHead>

      <div className="gallery-toolbar">
        <select className="select" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="筛选下载状态">
          <option value="">全部状态</option>
          <option value="downloading">下载中</option>
          <option value="completed">已下载</option>
          <option value="pushed">已推送</option>
          <option value="failed">失败</option>
        </select>
      </div>

      {error ? <ErrorEmpty message={error} onRetry={load} /> :
       data === null ? <Loading /> : data.downloads.length === 0 ? (
        <Empty icon="○" title="暂无下载记录" sub="在任务详情页推送磁力后，记录会出现在这里。" />
      ) : (
        <div className="card">
          {data.downloads.map((d) => (
            <div className="row-item" key={d.id}
              onClick={() => d.task_id && nav(`/task/${d.task_id}`)}
              role={d.task_id ? "button" : undefined}
              tabIndex={d.task_id ? 0 : undefined}
              style={{ cursor: d.task_id ? 'pointer' : 'default' }}
              onKeyDown={(e) => { if (d.task_id && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); nav(`/task/${d.task_id}`) } }}>
              <div style={{ flex: 1 }}>
                <div className="row-code">{d.video_code || d.magnet.slice(0, 20)}</div>
                <div className="row-title" style={{ fontSize: 11, color: 'var(--t-mute)' }}>
                  {d.title || d.magnet.slice(0, 60)}
                </div>
              </div>
              <div className="row-tags">
                <span className="chip" style={{ fontSize: 10 }}>{d.downloader === 'qbittorrent' ? 'qB' : 'CD2'}</span>
                <span className={`chip ${statusCls[d.status] || ''}`}>{statusLabel[d.status] || d.status}</span>
                {d.status === 'downloading' && d.progress > 0 && (
                  <span className="chip chip-amber" style={{ fontFamily: 'var(--ff-mono)' }}>{Math.round(d.progress)}%</span>
                )}
              </div>
              <div style={{ fontFamily: 'var(--ff-mono)', fontSize: 11, color: 'var(--t-faint)', minWidth: 90, textAlign: 'right' }}>
                {d.added_at?.slice(0, 16).replace('T', ' ') || '—'}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
