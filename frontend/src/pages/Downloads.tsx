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
  // N26: 字幕上传
  const uploadSub = (dl: { id: number; organized?: boolean | null }) => {
    if (!dl.organized) return
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = '.srt,.ass'
    input.onchange = async () => {
      const f = input.files?.[0]
      if (!f) return
      try {
        const r = await api.uploadSubtitle(dl.id, f)
        toastOk(`字幕已上传：${r.name}`)
      } catch (e) { toastErr(String((e as Error).message)) }
    }
    input.click()
  }

  // N19: 种子健康（做种数 <5 低健康）
  const [unhealthy, setUnhealthy] = useState<Set<number>>(new Set())
  useEffect(() => {
    let alive = true
    api.torrentHealth().then((r) => {
      if (alive && r.items) setUnhealthy(new Set(r.items.filter((i) => !i.healthy).map((i) => i.dl_id)))
    }).catch(() => {})
    return () => { alive = false }
  }, [])
  const [filter, setFilter] = useState('')
  const toastErr = useStore((s) => s.toastErr)
  const toastOk = useStore((s) => s.toastOk)

  const load = useCallback(() => {
    setData(null); setError(null)
    api.downloads.list(filter || undefined).then(setData).catch((e) => { setError(String((e as Error).message)); setData({ downloads: [], total: 0 }) })
  }, [filter])

  useEffect(() => { load() }, [load])
  // 自动刷新：仅页面可见且存在下载中任务时轮询（空闲/后台不请求）
  useEffect(() => {
    const t = setInterval(() => {
      if (document.hidden) return
      const hasActive = (data?.downloads ?? []).some((d) => (d as { status?: string }).status === 'downloading')
      if (hasActive) load()
    }, 15000)
    return () => clearInterval(t)
  }, [load, data])

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
                {d.organized && <span className="chip chip-green" style={{ fontSize: 10 }}>已整理</span>}
                {d.organized && <button className="btn btn--ghost btn--sm" style={{ fontSize: 10, padding: '1px 8px' }} onClick={() => uploadSub(d)}>字幕</button>}
                {unhealthy.has(d.id) && <span className="chip chip-red" style={{ fontSize: 10 }}>低健康</span>}
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
