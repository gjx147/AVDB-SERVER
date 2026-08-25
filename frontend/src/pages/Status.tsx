import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { PageHead, Loading } from '../components/States'

export function Status() {
  const [d, setD] = useState<Awaited<ReturnType<typeof api.systemStatus>> | null>(null)
  useEffect(() => {
    let alive = true
    api.systemStatus().then((r) => { if (alive) setD(r) }).catch(() => {})
    return () => { alive = false }
  }, [])
  const fmt = (s: string) => (s ? s.replace('T', ' ').slice(0, 19) : '—')
  return (
    <div className="page">
      <PageHead eyebrow="Status" title={<>系统<em>状态</em></>}
        sub="调度任务、队列、下载器活跃度、最近错误与备份。">
        {d && <span style={{ fontSize: 11, color: 'var(--t-mute)' }}>服务器时间 {d.server_time}</span>}
      </PageHead>
      {!d ? <Loading /> : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 14 }}>
          <div className="card" style={{ padding: '14px 16px' }}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>调度任务</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 5, fontSize: 11 }}>
              {d.jobs.map((j) => (
                <div key={j.id} style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span>{j.id}</span>
                  <span style={{ color: 'var(--t-mute)' }}>下次 {fmt(j.next_run)}</span>
                </div>
              ))}
              {d.jobs.length === 0 && <span style={{ color: 'var(--t-mute)' }}>无调度任务</span>}
            </div>
          </div>
          <div className="card" style={{ padding: '14px 16px' }}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>队列与错误</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 11 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>待处理任务</span><span>{d.queue.pending}</span></div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>失败任务</span><span>{d.queue.failed}</span></div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>活跃下载</span><span>{d.active_downloads}</span></div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>24h 爬取错误</span><span style={{ color: d.errors_24h.crawl > 0 ? 'var(--red, #dc2626)' : 'var(--green, #059669)' }}>{d.errors_24h.crawl}</span></div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>24h 通知失败</span><span style={{ color: d.errors_24h.notify > 0 ? 'var(--red, #dc2626)' : 'var(--green, #059669)' }}>{d.errors_24h.notify}</span></div>
            </div>
          </div>
          <div className="card" style={{ padding: '14px 16px' }}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>最近备份</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 5, fontSize: 11 }}>
              {d.backups.map((b) => (
                <div key={b.name} style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span>{b.name}</span>
                  <span style={{ color: 'var(--t-mute)' }}>{b.size_mb} MB</span>
                </div>
              ))}
              {d.backups.length === 0 && <span style={{ color: 'var(--t-mute)' }}>暂无备份</span>}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
