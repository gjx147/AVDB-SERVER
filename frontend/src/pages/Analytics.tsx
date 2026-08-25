import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { PageHead, Loading } from '../components/States'
import { useStore } from '../store/useStore'

const useToastOk = () => useStore((st) => st.toastOk)
const useToastErr = () => useStore((st) => st.toastErr)

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card" style={{ padding: '14px 16px' }}>
      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>{title}</div>
      {children}
    </div>
  )
}

function HealthPanel() {
  const [d, setD] = useState<Awaited<ReturnType<typeof api.libraryHealth>> | null>(null)
  useEffect(() => {
    let alive = true
    api.libraryHealth().then((r) => { if (alive) setD(r) }).catch(() => {})
    return () => { alive = false }
  }, [])
  if (!d) return <Panel title="库健康度"><Loading /></Panel>
  const color = d.score >= 85 ? 'var(--green, #059669)' : d.score >= 60 ? 'var(--gold, #d97706)' : 'var(--red, #dc2626)'
  return (
    <Panel title={`库健康度 · ${d.score} / 100`}>
      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginBottom: 8 }}>
        {Object.entries(d.rates).map(([k, v]) => (
          <div key={k} style={{ fontSize: 11 }}>
            <span style={{ color: 'var(--t-mute)' }}>{k} </span>
            <span style={{ fontWeight: 600, color }}>{v}%</span>
          </div>
        ))}
      </div>
      {d.fix_top.length > 0 && (
        <div style={{ fontSize: 11, color: 'var(--t-mute)' }}>
          最该补磁力：{d.fix_top.slice(0, 5).map((t) => t.video_code).join(' / ')}
        </div>
      )}
    </Panel>
  )
}

function ProfilePanel() {
  const [d, setD] = useState<Awaited<ReturnType<typeof api.profileReport>> | null>(null)
  useEffect(() => {
    let alive = true
    api.profileReport().then((r) => { if (alive) setD(r) }).catch(() => {})
    return () => { alive = false }
  }, [])
  if (!d) return <Panel title="观看偏好画像"><Loading /></Panel>
  const list = (arr: { name: string; score: number }[]) => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {arr.slice(0, 6).map((x) => (
        <div key={x.name} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{x.name}</span>
          <span style={{ color: 'var(--t-mute)' }}>{x.score}</span>
        </div>
      ))}
    </div>
  )
  return (
    <Panel title={`观看偏好 · ${d.total} 条标记${d.avg_rating != null ? ` · 均分 ${d.avg_rating}` : ''}`}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
        <div><div style={{ fontSize: 11, color: 'var(--t-mute)' }}>Top 演员</div>{list(d.top_actors)}</div>
        <div><div style={{ fontSize: 11, color: 'var(--t-mute)' }}>Top 标签</div>{list(d.top_tags)}</div>
        <div><div style={{ fontSize: 11, color: 'var(--t-mute)' }}>Top 厂牌</div>{list(d.top_makers)}</div>
      </div>
    </Panel>
  )
}

function DownloadStatsPanel() {
  const [d, setD] = useState<Awaited<ReturnType<typeof api.downloadStats>> | null>(null)
  useEffect(() => {
    let alive = true
    api.downloadStats().then((r) => { if (alive) setD(r) }).catch(() => {})
    return () => { alive = false }
  }, [])
  if (!d) return <Panel title="下载成功率"><Loading /></Panel>
  return (
    <Panel title={`下载成功率（近 ${d.days} 天）`}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {d.items.map((it) => (
          <div key={it.downloader} style={{ fontSize: 11 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span>{it.downloader} · {it.total} 次</span>
              <span style={{ color: it.success_rate >= 90 ? 'var(--green, #059669)' : 'var(--gold, #d97706)' }}>
                {it.success_rate}%{it.avg_hours != null ? ` · 平均 ${it.avg_hours}h` : ''}
              </span>
            </div>
            {it.top_errors.length > 0 && (
              <div style={{ color: 'var(--t-mute)', fontSize: 10 }}>失败：{it.top_errors.map((e) => `${e.msg}×${e.count}`).join(' / ')}</div>
            )}
          </div>
        ))}
        {d.items.length === 0 && <div style={{ fontSize: 11, color: 'var(--t-mute)' }}>近 30 天无下载记录</div>}
      </div>
    </Panel>
  )
}

function CrawlPanel() {
  const [d, setD] = useState<Awaited<ReturnType<typeof api.crawlEfficiency>> | null>(null)
  useEffect(() => {
    let alive = true
    api.crawlEfficiency().then((r) => { if (alive) setD(r) }).catch(() => {})
    return () => { alive = false }
  }, [])
  if (!d) return <Panel title="爬虫效率"><Loading /></Panel>
  const max = Math.max(...d.trend.map((t) => t.total), 1)
  return (
    <Panel title={`爬虫效率（近 ${d.days} 天）`}>
      <div style={{ display: 'flex', gap: 4, alignItems: 'end', height: 44, marginBottom: 6 }}>
        {d.trend.map((t) => (
          <div key={t.date} title={`${t.date} 共 ${t.total} 错 ${t.errors}`}
            style={{ flex: 1, height: Math.max(3, (t.total / max) * 40), background: t.errors > 0 ? 'var(--red, #dc2626)' : 'var(--green, #059669)', borderRadius: 2, opacity: 0.8 }} />
        ))}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11 }}>
        {d.totals.map((t) => (
          <div key={t.type} style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span>{t.type}</span>
            <span style={{ color: 'var(--t-mute)' }}>{t.total} 次 · 成功率 {t.success_rate}%</span>
          </div>
        ))}
      </div>
      {d.top_errors.length > 0 && (
        <div style={{ fontSize: 10, color: 'var(--t-mute)', marginTop: 6 }}>
          主要错误：{d.top_errors.slice(0, 3).map((e) => `${e.msg}×${e.count}`).join(' / ')}
        </div>
      )}
    </Panel>
  )
}

function NotifyPanel() {
  const [d, setD] = useState<Awaited<ReturnType<typeof api.notificationHealth>> | null>(null)
  useEffect(() => {
    let alive = true
    api.notificationHealth().then((r) => { if (alive) setD(r) }).catch(() => {})
    return () => { alive = false }
  }, [])
  if (!d) return <Panel title="通知健康度"><Loading /></Panel>
  return (
    <Panel title={`通知健康度（近 ${d.days} 天）`}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 11 }}>
        {d.items.map((it) => (
          <div key={it.channel} style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span>{it.channel}</span>
            <span style={{ color: it.fail_rate === 0 ? 'var(--green, #059669)' : 'var(--red, #dc2626)' }}>
              {it.total} 次 · 失败率 {it.fail_rate}%
            </span>
          </div>
        ))}
        {d.items.length === 0 && <span style={{ color: 'var(--t-mute)' }}>无通知记录</span>}
      </div>
      {d.recent_failures.length > 0 && (
        <div style={{ fontSize: 10, color: 'var(--t-mute)', marginTop: 6 }}>
          最近失败：{d.recent_failures.slice(0, 3).map((f) => `${f.channel}: ${f.message}`).join(' / ')}
        </div>
      )}
    </Panel>
  )
}

function RankingPanel() {
  const [d, setD] = useState<Awaited<ReturnType<typeof api.rankingTrends>> | null>(null)
  useEffect(() => {
    let alive = true
    api.rankingTrends().then((r) => { if (alive) setD(r) }).catch(() => {})
    return () => { alive = false }
  }, [])
  if (!d) return <Panel title="榜单趋势"><Loading /></Panel>
  return (
    <Panel title={`榜单趋势（近 ${d.days} 天）`}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11 }}>
        {d.top_risers.slice(0, 6).map((r) => (
          <div key={r.code} style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span>{r.code}</span>
            <span style={{ color: 'var(--green, #059669)' }}>{r.from} → {r.to}（+{r.change}）</span>
          </div>
        ))}
        {d.top_risers.length === 0 && <span style={{ color: 'var(--t-mute)' }}>近 {d.days} 天榜单数据不足</span>}
      </div>
    </Panel>
  )
}

function GapsPanel() {
  const [d, setD] = useState<Awaited<ReturnType<typeof api.wishlistGaps>> | null>(null)
  const [busy, setBusy] = useState(false)
  const toastOk = useToastOk()
  const toastErr = useToastErr()
  useEffect(() => {
    let alive = true
    api.wishlistGaps().then((r) => { if (alive) setD(r) }).catch(() => setD(null))
    return () => { alive = false }
  }, [])
  if (!d) return <Panel title="观看缺口"><Loading /></Panel>
  const pushAll = async () => {
    const ids = d.items.filter((i) => i.has_magnet).map((i) => i.task_id)
    if (ids.length === 0) return
    setBusy(true)
    try {
      const r = await api.tasks.batchPush(ids)
      toastOk(`已推送 ${r.pushed} 项${r.skipped ? `，跳过 ${r.skipped}` : ''}`)
    } catch (e) { toastErr(String((e as Error).message)) }
    setBusy(false)
  }
  return (
    <Panel title={`观看缺口 · ${d.total} 部想看`}>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
        {d.items.slice(0, 10).map((t) => (
          <span key={t.task_id} style={{ fontSize: 11, border: '1px solid var(--line, #eee)', borderRadius: 99, padding: '2px 8px' }}>
            {t.video_code}
            {!t.has_magnet && <span style={{ color: 'var(--red, #dc2626)' }}> ⚠无磁力</span>}
          </span>
        ))}
        {d.items.length === 0 && <span style={{ fontSize: 11, color: 'var(--t-mute)' }}>没有想看但未下载的作品</span>}
      </div>
      <button className="btn btn--gold btn--sm" onClick={pushAll} disabled={busy}>
        {busy ? '推送中…' : '批量推送可下载项'}
      </button>
    </Panel>
  )
}

function AuditPanel() {
  const [d, setD] = useState<Awaited<ReturnType<typeof api.mediaAudit>> | null>(null)
  const [busy, setBusy] = useState(false)
  const toastOk = useToastOk()
  const toastErr = useToastErr()
  const run = async () => {
    setBusy(true)
    try { const r = await api.mediaAudit(); setD(r) }
    catch (e) { toastErr(String((e as Error).message)) }
    setBusy(false)
  }
  return (
    <Panel title="Emby 反向审计">
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8, flexWrap: 'wrap' }}>
        <button className="btn btn--ghost btn--sm" onClick={run} disabled={busy}>{busy ? '审计中…' : '运行审计'}</button>
        {d?.ok && <span style={{ fontSize: 11, color: 'var(--t-mute)' }}>Emby {d.emby_total} 项 / 本地 {d.local_total} 项</span>}
      </div>
      {d?.ok && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 11 }}>
          {d.emby_only && d.emby_only.length > 0 && (
            <div>Emby 有但库无记录：<span style={{ color: 'var(--gold, #d97706)' }}>{d.emby_only.slice(0, 8).join(' / ')}</span>{d.emby_only.length > 8 ? ` 等 ${d.emby_only.length} 项` : ''}</div>
          )}
          {d.dup_codes && d.dup_codes.length > 0 && (
            <div>本地重复番号：<span style={{ color: 'var(--red, #dc2626)' }}>{d.dup_codes.map((x) => `${x.code}×${x.count}`).join(' / ')}</span></div>
          )}
          {d.in_lib_missing_from_emby && d.in_lib_missing_from_emby.length > 0 && (
            <div>在库但 Emby 缺失：<span style={{ color: 'var(--blue, #2563eb)' }}>{d.in_lib_missing_from_emby.slice(0, 8).join(' / ')}</span></div>
          )}
          {!(d.emby_only?.length || d.dup_codes?.length || d.in_lib_missing_from_emby?.length) && (
            <span style={{ color: 'var(--t-mute)' }}>未发现遗漏</span>
          )}
        </div>
      )}
      {d && !d.ok && <div style={{ fontSize: 11, color: 'var(--red, #dc2626)' }}>{d.message}</div>}
    </Panel>
  )
}

export function Analytics() {
  return (
    <div className="page">
      <PageHead eyebrow="Analytics" title={<>分析<em>中心</em></>}
        sub="库健康度、观看偏好、下载与爬虫效率、通知与榜单趋势。">
      </PageHead>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 14 }}>
        <HealthPanel />
        <ProfilePanel />
        <DownloadStatsPanel />
        <CrawlPanel />
        <NotifyPanel />
        <RankingPanel />
        <GapsPanel />
        <AuditPanel />
        <SeriesPanel />
      </div>
    </div>
  )
}

function SeriesPanel() {
  const [d, setD] = useState<Awaited<ReturnType<typeof api.seriesProgress>> | null>(null)
  useEffect(() => {
    let alive = true
    api.seriesProgress().then((r) => { if (alive) setD(r) }).catch(() => {})
    return () => { alive = false }
  }, [])
  if (!d) return <Panel title="系列概览"><Loading /></Panel>
  return (
    <Panel title={`系列概览 · ${d.total_series} 个系列`}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 5, fontSize: 11 }}>
        {d.items.slice(0, 8).map((x) => (
          <div key={x.series}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{x.series}</span>
              <span style={{ color: 'var(--t-mute)' }}>{x.total} 部 · 已看 {x.viewed_rate}%{x.avg_rating ? ` · 均分 ${x.avg_rating}` : ''}</span>
            </div>
            <div style={{ height: 3, background: 'var(--bg-raised, #f3f4f6)', borderRadius: 2, marginTop: 2 }}>
              <div style={{ width: `${x.viewed_rate}%`, height: '100%', background: 'var(--gold, #d97706)', borderRadius: 2 }} />
            </div>
          </div>
        ))}
        {d.items.length === 0 && <span style={{ color: 'var(--t-mute)' }}>暂无系列数据</span>}
      </div>
    </Panel>
  )
}
