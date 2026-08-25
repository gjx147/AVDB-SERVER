import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { Settings as S } from '../api/types'
import { PageHead, Loading, ErrorEmpty } from '../components/States'
import { useStore } from '../store/useStore'
import { audio } from '../audio/engine'

type Tab = 'crawl' | 'retry' | 'notify' | 'media' | 'ai' | 'appearance' | 'backup'

export function Settings() {
  const [s, setS] = useState<S | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('crawl')
  const [proxyTesting, setProxyTesting] = useState(false)
  const toastOk = useStore((st) => st.toastOk)
  const toastErr = useStore((st) => st.toastErr)

  const load = () => { api.settings.get().then(setS).catch((e) => setError(String((e as Error).message))) }
  useEffect(() => { load() }, [])
  if (error) return <div className="page"><ErrorEmpty message={error} onRetry={load} /></div>
  if (!s) return <div className="page"><Loading /></div>

  const upd = (patch: Partial<S>) => setS({ ...s, ...patch })
  const save = async () => {
    try {
      // T21: 保存前先拉取服务器最新设置，仅提交本地发生变化的字段，
      // 避免主表单的旧快照覆盖其它 Tab 刚保存的新值（如新密钥）
      let merged: Record<string, unknown> = s as unknown as Record<string, unknown>
      try {
        const latest = await api.settings.get() as unknown as Record<string, unknown>
        merged = { ...latest }
        for (const k of Object.keys(s as unknown as Record<string, unknown>)) {
          const lv = (latest as Record<string, unknown>)[k]
          const sv = (s as unknown as Record<string, unknown>)[k]
          if (JSON.stringify(lv) !== JSON.stringify(sv)) {
            merged[k] = sv
          }
        }
      } catch { /* 拉取失败则全量提交本地值 */ }
      await api.settings.update(merged as never)
      toastOk('设置已保存')
    } catch (e) { toastErr(String((e as Error).message)) }
  }
  const testProxy = async () => {
    setProxyTesting(true)
    try {
      await save()
      const proxyVal = s.http_proxy || ''
      const r = await api.settings.testProxy(proxyVal)
      if (r.ok) toastOk(r.message)
      else toastErr(r.message)
    } catch (e) { toastErr(String((e as Error).message)) }
    finally { setProxyTesting(false) }
  }
  const backup = async () => {
    try {
      const blob = await api.settings.backup()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = `avdb-backup-${new Date().toISOString().slice(0, 10)}.db`
      a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)  // Firefox 下延迟回收，避免下载中断
      toastOk('备份已导出')
    } catch (e) { toastErr(String((e as Error).message)) }
  }
  const restore = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (!f) return
    if (!(await useStore.getState().confirm('恢复数据库', `将用「${f.name}」覆盖现有数据库，现有数据将丢失。确定继续？`))) {
      e.target.value = ''
      return
    }
    try { await api.settings.restore(f); toastOk('数据库已恢复，请刷新页面') } catch (er) { toastErr(String((er as Error).message)) }
  }
  const cleanFailed = async () => {
    if (!(await useStore.getState().confirm('清理失败任务', '将删除所有失败状态的任务记录，确定继续？'))) return
    try { await api.settings.cleanFailed(); toastOk('已清理') } catch (e) { toastErr(String((e as Error).message)) }
  }

  // ── F1 数据导入导出 ──
  const [codesText, setCodesText] = useState('')
  const exportCsv = async () => {
    try {
      const blob = await api.exportTasksCsv()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = `avdb-tasks-${new Date().toISOString().slice(0, 10)}.csv`
      a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (e) { toastErr(String((e as Error).message)) }
  }
  const exportSubs = async () => {
    try {
      const r = await api.exportSubscriptions()
      const blob = new Blob([JSON.stringify(r, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = `avdb-subscriptions-${new Date().toISOString().slice(0, 10)}.json`
      a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (e) { toastErr(String((e as Error).message)) }
  }
  const importCodes = async () => {
    const codes = codesText.split(/\n|,|;|，/).map((c) => c.trim()).filter(Boolean)
    if (!codes.length) return
    try {
      const r = await api.importCodes(codes)
      toastOk(`已导入 ${r.added} 个番号${r.skipped ? `，跳过 ${r.skipped} 个已存在` : ''}`)
      setCodesText('')
    } catch (e) { toastErr(String((e as Error).message)) }
  }

  // ── N17 AI 批量补标签 ──
  const [tagBusy, setTagBusy] = useState(false)
  const runBatchTags = async () => {
    if (tagBusy) return
    setTagBusy(true)
    try {
      const r = await api.batchTags(5)
      toastOk(`批量打标完成：${r.done}/${r.processed} 条${r.errors ? `，失败 ${r.errors}` : ''}`)
    } catch (e) { toastErr(String((e as Error).message)) }
    setTagBusy(false)
  }

  // ── N11 磁力导入 ──
  const [magText, setMagText] = useState('')
  const importMagnets = async () => {
    if (!magText.trim()) return
    try {
      const r = await api.importMagnets(magText)
      toastOk(`已导入 ${r.added} 个磁力${r.skipped ? `，跳过 ${r.skipped} 个重复` : ''}`)
      if (r.ok) setMagText('')
    } catch (e) { toastErr(String((e as Error).message)) }
  }

  // ── F7 下载自动整理配置 ──
  const [orgCfg, setOrgCfg] = useState<Record<string, string>>({
    organize_enabled: 'false', organize_target_dir: '', organize_naming: '{code} - {title}',
  })
  useEffect(() => { api.organize.config().then(setOrgCfg).catch(() => {}) }, [])
  const saveOrgCfg = async () => {
    try { await api.organize.setConfig(orgCfg); toastOk('整理配置已保存') }
    catch (e) { toastErr(String((e as Error).message)) }
  }
  const runOrganizeAll = async () => {
    try {
      const r = await api.organize.runAll()
      toastOk(`整理完成：${r.organized}/${r.total} 项成功`)
    } catch (e) { toastErr(String((e as Error).message)) }
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
          <button className={tab === 'media' ? 'on' : ''} onClick={() => setTab('media')}>媒体库</button>
          <button className={tab === 'ai' ? 'on' : ''} onClick={() => setTab('ai')}>AI 智能</button>
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
              <div className="field">
                <label htmlFor="http-proxy">代理地址</label>
                <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
                  <input id="http-proxy" className="input" placeholder="http://192.168.31.220:20171"
                    value={s.http_proxy || ''}
                    onChange={(e) => upd({ http_proxy: e.target.value })} />
                  <button className="btn btn--ghost btn--sm" onClick={testProxy} disabled={proxyTesting} style={{ whiteSpace: 'nowrap' }}>
                    {proxyTesting ? '测试中…' : '测试连接'}
                  </button>
                </div>
                <div className="hint">用于访问 JavDB（格式：http://host:port 或 http://user:pass@host:port）。留空则不走代理。保存后爬取自动生效。</div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
                <div className="field"><label htmlFor="crawl-delay-min">爬取延迟下限 (秒)</label><input id="crawl-delay-min" className="input" type="number" value={s.crawl_delay_min} onChange={(e) => { const v = e.target.value; if (v !== '') upd({ crawl_delay_min: +v }) }} /></div>
                <div className="field"><label htmlFor="crawl-delay-max">爬取延迟上限 (秒)</label><input id="crawl-delay-max" className="input" type="number" value={s.crawl_delay_max} onChange={(e) => { const v = e.target.value; if (v !== '') upd({ crawl_delay_max: +v }) }} /></div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
                <div className="field"><label htmlFor="max-pages">默认最大页数</label><input id="max-pages" className="input" type="number" value={s.max_pages_default} onChange={(e) => { const v = e.target.value; if (v !== '') upd({ max_pages_default: +v }) }} /></div>
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
                <div className="field"><label htmlFor="retry-interval">重试间隔 (秒)</label><input id="retry-interval" className="input" type="number" value={s.auto_retry_interval} onChange={(e) => { const v = e.target.value; if (v !== '') upd({ auto_retry_interval: +v }) }} /></div>
                <div className="field"><label htmlFor="retry-max-count">最大重试次数</label><input id="retry-max-count" className="input" type="number" value={s.auto_retry_max_count} onChange={(e) => { const v = e.target.value; if (v !== '') upd({ auto_retry_max_count: +v }) }} /></div>
              </div>
            </div>
          )}

          {tab === 'notify' && <NotifyTab toastOk={toastOk} toastErr={toastErr} />}
          {tab === 'media' && <MediaTab toastOk={toastOk} toastErr={toastErr} />}
          {tab === 'ai' && <AiTab toastOk={toastOk} toastErr={toastErr} />}

          {tab === 'appearance' && <AppearanceTab />}

          {tab === 'backup' && (
            <div className="card">
              <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                <button className="btn btn--ghost" onClick={backup} style={{ width: 'fit-content' }}>导出备份</button>
                <label className="btn btn--ghost" style={{ width: 'fit-content', cursor: 'pointer' }}>
                  导入备份<input type="file" accept=".db,.sqlite,.json" onChange={restore} style={{ display: 'none' }} />
                </label>
                <button className="btn btn--danger btn--sm" onClick={cleanFailed} style={{ width: 'fit-content' }}>清理所有失败任务</button>
                <div style={{ borderTop: '1px solid var(--line, #eee)', paddingTop: 14 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>数据导入导出</div>
                  <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                    <button className="btn btn--ghost btn--sm" onClick={exportCsv} style={{ width: 'fit-content' }}>导出任务 CSV</button>
                    <button className="btn btn--ghost btn--sm" onClick={exportSubs} style={{ width: 'fit-content' }}>导出订阅清单</button>
                  </div>
                  <div style={{ marginTop: 10 }}>
                    <textarea className="input" rows={3} placeholder={'每行一个番号，例如:\nABC-123\nXYZ-789'} value={codesText}
                      onChange={(e) => setCodesText(e.target.value)} style={{ width: '100%', fontFamily: 'monospace', fontSize: 12 }} />
                    <button className="btn btn--gold btn--sm" onClick={importCodes} style={{ marginTop: 8 }} disabled={!codesText.trim()}>批量导入番号</button>
                    <button className="btn btn--ghost btn--sm" onClick={runBatchTags} style={{ marginTop: 8 }}>AI 批量补标签（5 条）</button>
                  </div>
                  <div style={{ marginTop: 10 }}>
                    <textarea className="input" rows={3} placeholder={'粘贴磁力链接（可多行，自动去重）'}
                      value={magText} onChange={(e) => setMagText(e.target.value)}
                      style={{ width: '100%', fontFamily: 'monospace', fontSize: 12 }} />
                    <button className="btn btn--gold btn--sm" onClick={importMagnets} style={{ marginTop: 8 }} disabled={!magText.trim()}>批量导入磁力</button>
                  </div>
                </div>
                <div style={{ borderTop: '1px solid var(--line, #eee)', paddingTop: 14, marginTop: 14 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>下载自动整理（硬链接进媒体库）</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, cursor: 'pointer' }}>
                      <input type="checkbox" checked={orgCfg.organize_enabled === 'true'}
                        onChange={(e) => setOrgCfg((c) => ({ ...c, organize_enabled: e.target.checked ? 'true' : 'false' }))} />
                      下载完成后自动整理（需配置整理目录，默认关闭）
                    </label>
                    <div className="field" style={{ margin: 0 }}>
                      <label>媒体库整理目录（Emby 扫描目录，必填）</label>
                      <input className="input" value={orgCfg.organize_target_dir} placeholder="/media/av"
                        onChange={(e) => setOrgCfg((c) => ({ ...c, organize_target_dir: e.target.value }))} />
                    </div>
                    <div className="field" style={{ margin: 0 }}>
                      <label>命名模板（{'{code}'} 番号 / {'{title}'} 标题）</label>
                      <input className="input" value={orgCfg.organize_naming} placeholder="{code} - {title}"
                        onChange={(e) => setOrgCfg((c) => ({ ...c, organize_naming: e.target.value }))} />
                    </div>
                    <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                      <button className="btn btn--gold btn--sm" onClick={saveOrgCfg}>保存整理配置</button>
                      <button className="btn btn--ghost btn--sm" onClick={runOrganizeAll}>整理全部已完成下载</button>
                    </div>
                  </div>
                </div>
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
  const theme = useStore((s) => s.theme)
  const setTheme = useStore((s) => s.setTheme)
  const moodMode = useStore((s) => s.moodMode)
  const toggleMood = useStore((s) => s.toggleMood)
  const copyTier = useStore((s) => s.copyTier)
  const setCopyTier = useStore((s) => s.setCopyTier)
  const nightBoost = useStore((s) => s.nightBoost)
  const setNightBoost = useStore((s) => s.setNightBoost)
  const soundOn = useStore((s) => s.soundOn)
  const setSoundOn = useStore((s) => s.setSoundOn)
  return (
    <>
      <div className="card">
        <div className="field">
          <label>主题</label>
          <div className="seg">
            <button className={theme === 'auto' ? 'on' : ''} onClick={() => setTheme('auto')}>跟随系统</button>
            <button className={theme === 'light' ? 'on' : ''} onClick={() => setTheme('light')}>欲焰 · 亮</button>
            <button className={theme === 'boudoir' ? 'on' : ''} onClick={() => setTheme('boudoir')}>暗夜丝绒</button>
          </div>
          <div className="hint">暗夜丝绒：黑丝绒底 × 鎏金线 × 唇色霓虹，为深夜私享而生</div>
        </div>
        <div className="field">
          <label>烛光密室</label>
          <div className="seg">
            <button className={moodMode ? 'on' : ''} onClick={() => { if (!moodMode) toggleMood() }}>开启</button>
            <button className={!moodMode ? 'on' : ''} onClick={() => { if (moodMode) toggleMood() }}>关闭</button>
          </div>
          <div className="hint">暗场聚光、轮播慢舞、烛火环境音；可与任意主题叠加</div>
        </div>
        <div className="field">
          <label>耳语文案档</label>
          <div className="seg">
            <button className={copyTier === 0 ? 'on' : ''} onClick={() => setCopyTier(0)}>克制</button>
            <button className={copyTier === 1 ? 'on' : ''} onClick={() => setCopyTier(1)}>大胆</button>
            <button className={copyTier === 2 ? 'on' : ''} onClick={() => setCopyTier(2)}>露骨</button>
          </div>
          <div className="hint">她是这座影库的女主人——档位决定她说话有多靠近你的耳朵</div>
        </div>
        <div className="field">
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
            <input type="checkbox" checked={nightBoost} onChange={(e) => setNightBoost(e.target.checked)} />
            深夜自动升温（22:00–05:00 文案升一档）
          </label>
          <div className="hint">夜深了，她们更放肆</div>
        </div>
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
      <div className="card" style={{ marginTop: 22 }}>
        <div className="field">
          <label>声色层</label>
          <div className="seg">
            <button className={soundOn ? 'on' : ''} onClick={() => { if (!soundOn) setSoundOn(true) }}>开启</button>
            <button className={!soundOn ? 'on' : ''} onClick={() => { if (soundOn) setSoundOn(false) }}>静音</button>
          </div>
          <div className="hint">心跳随体温加速、丝绸沙沙、收藏时她的一声轻叹、密室烛火噼啪——全部程序化合成，无音频文件。默认静音，仅在本机播放</div>
        </div>
        {soundOn && <BusVolumeRow bus="physio" label="生理音（心跳/轻叹）" />}
        {soundOn && <BusVolumeRow bus="tex" label="材质音（丝绸/掀纱）" />}
        {soundOn && <BusVolumeRow bus="amb" label="环境音（烛火/杯碰）" />}
      </div>
    </>
  )
}

function BusVolumeRow({ bus, label }: { bus: 'physio' | 'tex' | 'amb'; label: string }) {
  const [v, setV] = useState(audio.busVolume[bus])
  return (
    <div className="field">
      <label htmlFor={`vol-${bus}`}>{label}</label>
      <input id={`vol-${bus}`} type="range" min={0} max={1} step={0.05} value={v} style={{ width: '100%', accentColor: 'var(--gold)' }}
        onChange={(e) => { const nv = +e.target.value; setV(nv); audio.setBusVolume(bus, nv) }} />
    </div>
  )
}

const EVENT_LABELS: Record<string, string> = {
  download_complete: '下载完成', crawl_complete: '爬取完成',
  disk_warning: '磁盘告警', queue_stuck: '队列卡死',
  retry_exhausted: '重试耗尽', auto_added: '自动入库', actor_new_work: '演员新作',
}

/* ── MiniMax 模型预设（OpenAI 兼容，platform.minimaxi.com）── */
const MINIMAX_MODELS = [
  { id: 'MiniMax-M3', label: 'MiniMax-M3（最新 · 1M 上下文）' },
  { id: 'MiniMax-M2.7', label: 'MiniMax-M2.7（推荐 · 快且稳）' },
  { id: 'MiniMax-M2.7-highspeed', label: 'MiniMax-M2.7-highspeed（更快的平替）' },
  { id: 'MiniMax-M2.5', label: 'MiniMax-M2.5' },
  { id: 'MiniMax-M2.1', label: 'MiniMax-M2.1' },
]
const MINIMAX_BASE = 'https://api.minimaxi.com/v1'

function AiTab({ toastOk, toastErr }: { toastOk: (m: string) => void; toastErr: (m: string) => void }) {
  const [enabled, setEnabled] = useState(false)
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('MiniMax-M2.7')
  const [customModel, setCustomModel] = useState('')
  const [testing, setTesting] = useState(false)

  useEffect(() => {
    api.settings.get().then((s) => {
      setEnabled(s.ai_enabled === 'true')
      setBaseUrl(s.ai_base_url || '')
      setApiKey(s.ai_api_key && s.ai_api_key !== '***' ? s.ai_api_key : '')
      const m = s.ai_model || 'MiniMax-M2.7'
      if (MINIMAX_MODELS.some((x) => x.id === m)) { setModel(m); setCustomModel('') }
      else { setModel('_custom'); setCustomModel(m) }
    }).catch(() => {})
  }, [])

  const patch = () => ({
    ai_enabled: enabled ? 'true' : 'false',
    ai_base_url: baseUrl.trim(),
    ai_model: (model === '_custom' ? customModel : model).trim(),
    // *** 哨兵：未改动时不提交，保留后端已存 Key
    ...(apiKey ? { ai_api_key: apiKey.trim() } : {}),
  })

  const save = async () => {
    try {
      await api.settings.update(patch())
      toastOk('AI 配置已保存')
      return true
    } catch (e) { toastErr(String((e as Error).message)); return false }
  }
  const test = async () => {
    setTesting(true)
    try {
      if (!(await save())) return
      const r = await api.ai.test()
      if (r.ok) toastOk(r.message)
      else toastErr(r.message)
    } catch (e) { toastErr(String((e as Error).message)) }
    finally { setTesting(false) }
  }
  const preset = () => {
    setBaseUrl(MINIMAX_BASE)
    setModel('MiniMax-M2.7')
    toastOk('已应用 MiniMax 预设，填入 API Key 即可')
  }

  return (
    <div className="card">
      <div className="field">
        <label>启用 AI</label>
        <div className="seg">
          <button className={enabled ? 'on' : ''} onClick={() => setEnabled(true)}>开启</button>
          <button className={!enabled ? 'on' : ''} onClick={() => setEnabled(false)}>关闭</button>
        </div>
        <div className="hint">AI 耳语（Wall 轮播情话 / 今夜情人落款）、标题翻译、标签生成都走这里</div>
      </div>
      <div className="field">
        <label htmlFor="ai-base">接口地址（OpenAI 兼容）</label>
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
          <input id="ai-base" className="input" placeholder={MINIMAX_BASE} value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
          <button className="btn btn--ghost btn--sm" onClick={preset} style={{ whiteSpace: 'nowrap' }}>MiniMax 预设</button>
        </div>
        <div className="hint">默认走 MiniMax（{MINIMAX_BASE}）；也兼容 OpenAI/DeepSeek/中转站等任何 OpenAI 兼容地址</div>
      </div>
      <div className="field">
        <label htmlFor="ai-key">API Key</label>
        <input id="ai-key" className="input" type="password" placeholder="在 platform.minimaxi.com → 账户管理 → 接口密钥 创建" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
        <div className="hint">仅保存在你的 NAS 本地；留空表示不修改已保存的 Key</div>
      </div>
      <div className="field">
        <label htmlFor="ai-model">模型</label>
        <select id="ai-model" className="input" value={model} onChange={(e) => setModel(e.target.value)}>
          {MINIMAX_MODELS.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
          <option value="_custom">自定义（手动输入）…</option>
        </select>
        {model === '_custom' && (
          <input className="input" style={{ marginTop: 8 }} placeholder="输入模型 id，如 MiniMax-M2.5"
            value={customModel} onChange={(e) => setCustomModel(e.target.value)} />
        )}
        <div className="hint">M2.x 的思考链会内嵌在回复里，服务端已自动剥离；M3 自动关闭 thinking 更快更省</div>
      </div>
      <div style={{ display: 'flex', gap: 10 }}>
        <button className="btn btn--gold" onClick={save}>保存 AI 配置</button>
        <button className="btn btn--ghost" onClick={test} disabled={testing}>{testing ? '测试中…' : '保存并测试连接'}</button>
      </div>
    </div>
  )
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
      setBarkKey(s.notify_bark_key || '')
      setTgToken(s.notify_telegram_token || '')
      setTgChat(s.notify_telegram_chat_id || '')
      setWebhook(s.notify_webhook_url || '')
      const ev = s.notify_events || ''
      setEvents(ev ? ev.split(',').map((e: string) => e.trim()).filter(Boolean) : [])
    }).catch(() => {})
  }, [])

  const save = async () => {
    try {
      await api.settings.update({
        notify_bark_key: barkKey, notify_telegram_token: tgToken,
        notify_telegram_chat_id: tgChat, notify_webhook_url: webhook,
        notify_events: events.join(','),
      })
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

function MediaTab({ toastOk, toastErr }: { toastOk: (m: string) => void; toastErr: (m: string) => void }) {
  const [embyUrl, setEmbyUrl] = useState('')
  const [embyToken, setEmbyToken] = useState('')
  const [embyLibraryId, setEmbyLibraryId] = useState('')
  const [autoSync, setAutoSync] = useState(false)
  const [testing, setTesting] = useState(false)
  const [syncing, setSyncing] = useState(false)

  useEffect(() => {
    api.settings.get().then((s) => {
      setEmbyUrl(s.emby_url || '')
      setEmbyToken(s.emby_token && s.emby_token !== '***' ? s.emby_token : '')
      setEmbyLibraryId(s.emby_library_id || '')
      setAutoSync(s.emby_auto_sync === 'true')
    }).catch(() => {})
  }, [])

  const save = async () => {
    try {
      await api.settings.update({
        emby_url: embyUrl,
        emby_token: embyToken || '***',
        emby_library_id: embyLibraryId,
        emby_auto_sync: autoSync ? 'true' : 'false',
      })
      toastOk('媒体库配置已保存')
    } catch (e) { toastErr(String((e as Error).message)) }
  }
  const test = async () => {
    setTesting(true)
    try {
      await save()
      const r = await api.mediaServer.test()
      if (r.ok) { toastOk(r.message) } else { toastErr(r.message) }
    } catch (e) { toastErr(String((e as Error).message)) }
    finally { setTesting(false) }
  }
  const syncNow = async () => {
    setSyncing(true)
    try {
      await save()
      const r = await api.mediaServer.sync(500, true)
      toastOk(`同步完成：${r.checked} 部已核对，${r.in_library} 部在库${r.failed ? `，${r.failed} 部查询失败（保持原状态）` : ''}`)
    } catch (e) { toastErr(String((e as Error).message)) }
    finally { setSyncing(false) }
  }

  return (
    <div className="card">
      <div className="field">
        <label>Emby 服务器地址</label>
        <input className="input" placeholder="http://192.168.1.x:8096" value={embyUrl} onChange={(e) => setEmbyUrl(e.target.value)} />
        <div className="hint">Emby WebUI 的访问地址（含端口）</div>
      </div>
      <div className="field">
        <label>Emby API Key</label>
        <input className="input" type="password" placeholder="在 Emby 设置 > 高级 > API Keys 创建" value={embyToken} onChange={(e) => setEmbyToken(e.target.value)} />
        <div className="hint">用于订阅巡检时按番号搜索媒体库，避免重复入库</div>
      </div>
      <div className="field">
        <label>媒体库 ID（可选）</label>
        <input className="input" placeholder="留空 = 搜索全部媒体库；多库用逗号分隔" value={embyLibraryId} onChange={(e) => setEmbyLibraryId(e.target.value)} />
        <div className="hint">限定搜索范围到指定媒体库，多个用逗号分隔（如 id1,id2）；ID 见 Emby 库设置页 URL 里的 parentid</div>
      </div>
      <div className="field">
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
          <input type="checkbox" checked={autoSync} onChange={(e) => setAutoSync(e.target.checked)} />
          每日自动同步在库状态
        </label>
        <div className="hint">开启后每天增量核对一次番号是否在库（失败不覆盖已有状态）</div>
      </div>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <button className="btn btn--gold" onClick={save}>保存</button>
        <button className="btn btn--ghost" onClick={test} disabled={testing}>{testing ? '测试中…' : '保存并测试连接'}</button>
        <button className="btn btn--ghost" onClick={syncNow} disabled={syncing}>{syncing ? '同步中…' : '立即同步'}</button>
      </div>
    </div>
  )
}
