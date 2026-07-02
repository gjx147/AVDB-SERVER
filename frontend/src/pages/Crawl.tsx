import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { CrawlStatus, ListSourceWithStats } from '../api/types'
import { PageHead, Loading, ErrorEmpty } from '../components/States'
import { Icon } from '../components/Icons'
import { useStore } from '../store/useStore'

export function Crawl() {
  const [status, setStatus] = useState<CrawlStatus | null | undefined>(undefined)
  const [logs, setLogs] = useState<string[] | null>(null)
  const [sources, setSources] = useState<ListSourceWithStats[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selSource, setSelSource] = useState<number | ''>('')
  const wsRef = useRef<WebSocket | null>(null)
  const logBodyRef = useRef<HTMLDivElement>(null)
  const toastOk = useStore((s) => s.toastOk)
  const toastErr = useStore((s) => s.toastErr)

  useEffect(() => {
    api.listSources.list().then(setSources).catch(() => setSources([]))
    const refresh = () => {
      api.crawl.status().then((s) => { setStatus(s); setError(null) }).catch(() => setError('无法获取爬取状态'))
      api.crawl.logs().then((l) => setLogs(l.lines)).catch(() => setLogs([]))
    }
    refresh()
    const t = setInterval(refresh, 3000)
    return () => clearInterval(t)
  }, [])

  // WebSocket 实时进度（含指数退避重连）
  useEffect(() => {
    let retries = 0
    const maxRetries = 5
    let wsRefLocal: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let closed = false  // P1-5: 卸载标志，阻止卸载后新建 WS

    const connect = () => {
      if (closed) return  // P1-5: 卸载后不再重连
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      const ws = new WebSocket(`${proto}://${location.host}/ws/crawl-progress`)
      wsRef.current = ws
      wsRefLocal = ws
      ws.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data)
          // P1-11: 用合并而非整体替换，避免丢失 progress
          setStatus((prev) => prev ? { ...prev, ...data } : { running: false, paused: false, list_code: null, crawl_type: null, progress: data })
        } catch { /* ignore */ }
      }
      ws.onclose = () => {
        if (!closed && retries < maxRetries) {
          const delay = Math.min(1000 * 2 ** retries, 30000)
          retries++
          reconnectTimer = setTimeout(connect, delay)
        }
      }
      ws.onerror = () => { ws.close() }
    }
    connect()
    // P1-5: 卸载时清理 pending 重连定时器 + 关闭 WS，防止卸载后 setState / WS 泄漏
    return () => {
      closed = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      if (wsRefLocal) wsRefLocal.close()
    }
  }, [])

  useEffect(() => {
    if (logBodyRef.current) logBodyRef.current.scrollTop = logBodyRef.current.scrollHeight
  }, [logs])

  const doScan = async () => {
    const sid = selSource || (sources ?? [])[0]?.id
    if (!sid) return toastErr('请先选择列表源')
    try { await api.crawl.scan({ list_source_id: sid }); toastOk('已开始扫描') } catch (e) { toastErr(String((e as Error).message)) }
  }
  const doExtract = async () => {
    const sid = selSource || (sources ?? [])[0]?.id
    if (!sid) return toastErr('请先选择列表源')
    try { await api.crawl.extract({ list_source_id: sid }); toastOk('已开始提取磁力') } catch (e) { toastErr(String((e as Error).message)) }
  }
  const doExtractFailed = async () => {
    const sid = selSource || (sources ?? [])[0]?.id
    if (!sid) return toastErr('请先选择列表源')
    try { await api.crawl.extractFailed({ list_source_id: sid }); toastOk('已开始重试失败任务') } catch (e) { toastErr(String((e as Error).message)) }
  }
  const ctrl = async (op: 'pause' | 'resume' | 'stop') => {
    try { await api.crawl[op](); toastOk(op === 'stop' ? '已停止' : op === 'pause' ? '已暂停' : '已继续') } catch (e) { toastErr(String((e as Error).message)) }
  }

  const running = status?.running
  const pct = status?.progress && typeof status.progress === 'object' && 'percent' in (status.progress as object)
    ? Number((status.progress as { percent: number }).percent) : 0
  const cur = status?.progress && typeof status.progress === 'object' && 'current_code' in (status.progress as object)
    ? String((status.progress as { current_code: string }).current_code) : '—'

  if (status === undefined) return <div className="page"><Loading /></div>
  if (error) return <div className="page"><ErrorEmpty message={error} onRetry={() => window.location.reload()} /></div>

  return (
    <div className="page">
      <PageHead eyebrow="Crawl Console" title={<>爬取<em>控制台</em></>}
        sub="实时监控采集进度，支持暂停 / 继续 / 停止，日志流实时输出。">
        <button className="btn btn--ghost btn--sm" onClick={doExtractFailed}>重试失败</button>
        <button className="btn btn--gold" onClick={doExtract} disabled={running}><Icon.play />开始提取</button>
      </PageHead>

      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 24 }}>
        <span style={{ fontSize: 13, color: 'var(--t-mute)' }}>选择列表源</span>
        <select className="select" value={selSource} onChange={(e) => setSelSource(e.target.value ? +e.target.value : '')} aria-label="选择列表源">
          <option value="">默认</option>
          {(sources ?? []).map((s) => <option key={s.id} value={s.id}>{s.list_code}（待处理 {s.pending_count}）</option>)}
        </select>
        <button className="btn btn--ghost btn--sm" onClick={doScan}>扫描列表</button>
      </div>

      <div className="console-grid">
        <div className="crawl-state">
          <div className="crawl-ring">
            <svg width="140" height="140" viewBox="0 0 140 140">
              <circle cx="70" cy="70" r="60" fill="none" stroke="var(--line-soft)" strokeWidth="8" />
              <circle cx="70" cy="70" r="60" fill="none" stroke="var(--gold)" strokeWidth="8"
                strokeDasharray={2 * Math.PI * 60} strokeDashoffset={2 * Math.PI * 60 * (1 - pct / 100)} strokeLinecap="round" />
            </svg>
            <div className="pct"><b>{Math.round(pct)}%</b><span>{running ? '采集中' : '空闲'}</span></div>
          </div>
          <div className="crawl-now">当前{running ? '' : '无任务'}</div>
          {running && <div className="crawl-code-now">{cur}</div>}
          <div className="crawl-actions">
            <button className="btn btn--ghost btn--sm" onClick={() => ctrl('pause')} disabled={!running}>暂停</button>
            <button className="btn btn--ghost btn--sm" onClick={() => ctrl('resume')} disabled={!running}>继续</button>
            <button className="btn btn--danger btn--sm" onClick={() => ctrl('stop')} disabled={!running}>停止</button>
          </div>
        </div>

        <div className="terminal">
          <div className="term-head">
            <div className="term-dot" style={{ background: '#ff5f56' }} />
            <div className="term-dot" style={{ background: '#ffbd2e' }} />
            <div className="term-dot" style={{ background: '#27c93f' }} />
            <div className="term-title">scraper.py — 实时日志</div>
            <div className="term-live">{running ? '运行中' : '已停止'}</div>
          </div>
          <div className="term-body" ref={logBodyRef}>
            {(logs ?? []).length === 0 ? (
              <div className="term-line"><span className="ts">--:--:--</span> 等待日志输出…</div>
            ) : logs!.map((line, i) => (
              <div className="term-line" key={i}>
                <span className={line.includes('[ERR]') || line.toLowerCase().includes('error') ? 'lv-err'
                  : line.includes('[WARN]') ? 'lv-warn'
                  : line.includes('[OK]') ? 'lv-ok' : 'lv-info'}>{line}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
