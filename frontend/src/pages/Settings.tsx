import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { Settings as S } from '../api/types'
import { PageHead, Loading, ErrorEmpty } from '../components/States'
import { useStore } from '../store/useStore'

type Tab = 'crawl' | 'retry' | 'notify' | 'appearance' | 'backup'

export function Settings() {
  const [s, setS] = useState<S | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('crawl')
  const toastOk = useStore((st) => st.toastOk)
  const toastErr = useStore((st) => st.toastErr)

  const load = () => { api.settings.get().then(setS).catch((e) => setError(String((e as Error).message))) }
  useEffect(() => { load() }, [])
  if (error) return <div className="page"><ErrorEmpty message={error} onRetry={load} /></div>
  if (!s) return <div className="page"><Loading /></div>

  const upd = (patch: Partial<S>) => setS({ ...s, ...patch })
  const save = async () => {
    try { await api.settings.update(s); toastOk('设置已保存') } catch (e) { toastErr(String((e as Error).message)) }
  }
  const backup = async () => {
    try {
      const blob = await api.settings.backup()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = `avdb-backup-${new Date().toISOString().slice(0, 10)}.db`
      a.click(); URL.revokeObjectURL(url)
      toastOk('备份已导出')
    } catch (e) { toastErr(String((e as Error).message)) }
  }
  const restore = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (!f) return
    try { await api.settings.restore(f); toastOk('数据库已恢复，请刷新页面') } catch (er) { toastErr(String((er as Error).message)) }
  }
  const cleanFailed = async () => {
    if (!confirm('确定清理所有失败任务？')) return
    try { await api.settings.cleanFailed(); toastOk('已清理') } catch (e) { toastErr(String((e as Error).message)) }
  }

  return (
    <div className="page">
      <PageHead eyebrow="Settings" title={<>系统<em>设置</em></>}
        sub="站点地址、爬取行为、自动重试策略与数据备份。">
        <button className="btn btn--gold" onClick={save}>保存设置</button>
      </PageHead>

      <div className="settings-layout">
        <div className="settings-nav">
          <button className={tab === 'crawl' ? 'on' : ''} onClick={() => setTab('crawl')}>爬取设置</button>
          <button className={tab === 'retry' ? 'on' : ''} onClick={() => setTab('retry')}>自动重试</button>
          <button className={tab === 'notify' ? 'on' : ''} onClick={() => setTab('notify')}>通知配置</button>
          <button className={tab === 'appearance' ? 'on' : ''} onClick={() => setTab('appearance')}>外观</button>
          <button className={tab === 'backup' ? 'on' : ''} onClick={() => setTab('backup')}>备份与恢复</button>
        </div>

        <div>
          {tab === 'crawl' && (
            <div className="card">
              <div className="field"><label htmlFor="site-url">网站地址 <span className="req">*</span></label>
                <input id="site-url" className="input" value={s.javdb_url} onChange={(e) => upd({ javdb_url: e.target.value })} />
                <div className="hint">如部署镜像站可填写自定义地址</div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
                <div className="field"><label htmlFor="crawl-delay-min">爬取延迟下限 (秒)</label><input id="crawl-delay-min" className="input" type="number" value={s.crawl_delay_min} onChange={(e) => upd({ crawl_delay_min: +e.target.value })} /></div>
                <div className="field"><label htmlFor="crawl-delay-max">爬取延迟上限 (秒)</label><input id="crawl-delay-max" className="input" type="number" value={s.crawl_delay_max} onChange={(e) => upd({ crawl_delay_max: +e.target.value })} /></div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
                <div className="field"><label htmlFor="max-pages">默认最大页数</label><input id="max-pages" className="input" type="number" value={s.max_pages_default} onChange={(e) => upd({ max_pages_default: +e.target.value })} /></div>
                <div className="field"><label htmlFor="preferred-suffixes">磁力后缀优先级</label>
                  <select id="preferred-suffixes" className="input" value={s.preferred_suffixes} onChange={(e) => upd({ preferred_suffixes: e.target.value })}>
                    <option value="-UC,-C,-U">无码有字 → 有字 → 无码</option>
                    <option value="-UC,-U,-C">无码有字 → 无码 → 有字</option>
                    <option value="-C,-UC,-U">有字 → 无码有字 → 无码</option>
                    <option value="-U,-UC,-C">无码 → 无码有字 → 有字</option>
                    <option value="-C,-U,-UC">有字 → 无码 → 无码有字</option>
                    <option value="-U,-C,-UC">无码 → 有字 → 无码有字</option>
                  </select>
                </div>
              </div>
            </div>
          )}

          {tab === 'retry' && (
            <div className="card">
              <div className="field">
                <label>启用自动重试</label>
                <div className="seg">
                  <button className={s.auto_retry_enabled ? 'on' : ''} onClick={() => upd({ auto_retry_enabled: true })}>开启</button>
                  <button className={!s.auto_retry_enabled ? 'on' : ''} onClick={() => upd({ auto_retry_enabled: false })}>关闭</button>
                </div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
                <div className="field"><label htmlFor="retry-interval">重试间隔 (秒)</label><input id="retry-interval" className="input" type="number" value={s.auto_retry_interval} onChange={(e) => upd({ auto_retry_interval: +e.target.value })} /></div>
                <div className="field"><label htmlFor="retry-max-count">最大重试次数</label><input id="retry-max-count" className="input" type="number" value={s.auto_retry_max_count} onChange={(e) => upd({ auto_retry_max_count: +e.target.value })} /></div>
              </div>
            </div>
          )}

          {tab === 'notify' && <NotifyTab toastOk={toastOk} toastErr={toastErr} />}

          {tab === 'appearance' && <AppearanceTab />}

          {tab === 'backup' && (
            <div className="card">
              <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                <button className="btn btn--ghost" onClick={backup} style={{ width: 'fit-content' }}>导出备份</button>
                <label className="btn btn--ghost" style={{ width: 'fit-content', cursor: 'pointer' }}>
                  导入备份<input type="file" accept=".db,.sqlite,.json" onChange={restore} style={{ display: 'none' }} />
                </label>
                <button className="btn btn--danger btn--sm" onClick={cleanFailed} style={{ width: 'fit-content' }}>清理所有失败任务</button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function AppearanceTab() {
  const imgMode = useStore((s) => s.imgMode)
  const setImgMode = useStore((s) => s.setImgMode)
  return (
    <div className="card">
      <div className="field">
        <label>图片显示模式</label>
        <div className="seg">
          <button className={imgMode === 'normal' ? 'on' : ''} onClick={() => setImgMode('normal')}>正常显示</button>
          <button className={imgMode === 'blur' ? 'on' : ''} onClick={() => setImgMode('blur')}>模糊悬停解除</button>
          <button className={imgMode === 'hidden' ? 'on' : ''} onClick={() => setImgMode('hidden')}>完全隐藏</button>
        </div>
        <div className="hint">控制封面与演员头像的隐私显示方式</div>
      </div>
    </div>
  )
}

const EVENT_LABELS: Record<string, string> = {
  download_complete: '下载完成', crawl_complete: '爬取完成',
  disk_warning: '磁盘告警', queue_stuck: '队列卡死',
  retry_exhausted: '重试耗尽', auto_added: '自动入库', actor_new_work: '演员新作',
}

function NotifyTab({ toastOk, toastErr }: { toastOk: (m: string) => void; toastErr: (m: string) => void }) {
  const [barkKey, setBarkKey] = useState('')
  const [tgToken, setTgToken] = useState('')
  const [tgChat, setTgChat] = useState('')
  const [webhook, setWebhook] = useState('')
  const [events, setEvents] = useState<string[]>([])
  const [testing, setTesting] = useState(false)

  useEffect(() => {
    api.settings.get().then((s) => {
      const raw = s as unknown as Record<string, string>
      setBarkKey(raw.notify_bark_key || '')
      setTgToken(raw.notify_telegram_token || '')
      setTgChat(raw.notify_telegram_chat_id || '')
      setWebhook(raw.notify_webhook_url || '')
      const ev = raw.notify_events || ''
      setEvents(ev ? ev.split(',').map((e: string) => e.trim()).filter(Boolean) : [])
    }).catch(() => {})
  }, [])

  const save = async () => {
    try {
      await api.settings.update({
        notify_bark_key: barkKey, notify_telegram_token: tgToken,
        notify_telegram_chat_id: tgChat, notify_webhook_url: webhook,
        notify_events: events.join(','),
      } as unknown as Partial<S>)
      toastOk('通知配置已保存')
    } catch (e) { toastErr(String((e as Error).message)) }
  }
  const test = async () => {
    setTesting(true)
    try {
      await save()
      const r = await api.notify.test() as unknown as { results?: Record<string, boolean> }
      const res = r.results || {}
      toastOk(`测试完成：bark=${res.bark} / telegram=${res.telegram} / webhook=${res.webhook}`)
    } catch (e) { toastErr(String((e as Error).message)) }
    finally { setTesting(false) }
  }
  const toggleEvent = (ev: string) => {
    setEvents((prev) => prev.includes(ev) ? prev.filter((e) => e !== ev) : [...prev, ev])
  }

  return (
    <div className="card">
      <div className="field">
        <label>Bark 推送 Key</label>
        <input className="input" placeholder="https://api.day.app/你的Key/" value={barkKey} onChange={(e) => setBarkKey(e.target.value)} />
        <div className="hint">iOS Bark App 获取完整 URL</div>
      </div>
      <div className="field">
        <label>Telegram Bot Token</label>
        <input className="input" placeholder="123456:ABC-DEF..." value={tgToken} onChange={(e) => setTgToken(e.target.value)} />
      </div>
      <div className="field">
        <label>Telegram Chat ID</label>
        <input className="input" placeholder="你的 chat_id" value={tgChat} onChange={(e) => setTgChat(e.target.value)} />
      </div>
      <div className="field">
        <label>通用 Webhook URL</label>
        <input className="input" placeholder="https://..." value={webhook} onChange={(e) => setWebhook(e.target.value)} />
        <div className="hint">POST JSON: {"{ event, title, body }"}</div>
      </div>
      <div className="field">
        <label>启用事件</label>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {Object.entries(EVENT_LABELS).map(([ev, label]) => (
            <button key={ev} className={`chip${events.includes(ev) ? ' chip-green' : ''}`}
              style={{ cursor: 'pointer', padding: '6px 12px' }}
              onClick={() => toggleEvent(ev)}>{label}</button>
          ))}
        </div>
        <div className="hint">选择哪些事件触发通知</div>
      </div>
      <div style={{ display: 'flex', gap: 10 }}>
        <button className="btn btn--gold" onClick={save}>保存通知配置</button>
        <button className="btn btn--ghost" onClick={test} disabled={testing}>{testing ? '发送中…' : '保存并发送测试'}</button>
      </div>
    </div>
  )
}
