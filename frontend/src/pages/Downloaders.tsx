import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { Settings as S } from '../api/types'
import { PageHead, Loading, ErrorEmpty } from '../components/States'
import { Icon } from '../components/Icons'
import { useStore } from '../store/useStore'

export function Downloaders() {
  const [s, setS] = useState<S | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [testing, setTesting] = useState<string | null>(null)
  const [validation, setValidation] = useState<Record<string, string>>({})
  const [renaming, setRenaming] = useState(false)
  const toastOk = useStore((st) => st.toastOk)
  const toastErr = useStore((st) => st.toastErr)

  const load = () => { api.settings.get().then(setS).catch((e) => setError(String((e as Error).message))) }
  useEffect(() => { load() }, [])
  if (error) return <div className="page"><ErrorEmpty message={error} onRetry={load} /></div>
  if (!s) return <div className="page"><Loading /></div>

  const upd = (patch: Partial<S>) => { setS({ ...s, ...patch }); setValidation({}) }

  const validate = (): boolean => {
    const errs: Record<string, string> = {}
    if (!s.clouddrive_url && !s.qbittorrent_url) errs.general = '至少配置一个下载器'
    if (s.clouddrive_url && !s.clouddrive_url.includes(':')) errs.cdUrl = '格式: host:port'
    if (s.qbittorrent_url && !s.qbittorrent_url.startsWith('http')) errs.qbUrl = '需以 http:// 或 https:// 开头'
    setValidation(errs)
    return Object.keys(errs).length === 0
  }

  const save = async () => {
    if (!validate()) return toastErr('请修正表单中的错误')
    try { await api.settings.update(s); toastOk('设置已保存') } catch (e) { toastErr(String((e as Error).message)) }
  }

  const test = async (kind: 'clouddrive' | 'qbittorrent' | 'cd2_rename') => {
    if (!validate()) return
    try {
      // 先保存再测试（因为测试接口从 DB 读取配置）
      await api.settings.update(s)
      setTesting(kind)
      const sp = kind === 'clouddrive' ? s.clouddrive_save_path : kind === 'qbittorrent' ? s.qbittorrent_save_path : ''
      await api.downloaders.testConnection(kind, sp || undefined)
      const label = kind === 'clouddrive' ? 'CloudDrive2' : kind === 'cd2_rename' ? 'CD2 整理' : 'qBittorrent'
      toastOk(`${label} 连接成功`)
    } catch (e) {
      const msg = String((e as Error).message)
      toastErr(msg.length > 120 ? msg.slice(0, 120) + '…' : msg)
    } finally { setTesting(null) }
  }

  const renameAll = async () => {
    setRenaming(true)
    try {
      const r = await api.cd2Rename.renameAll()
      toastOk(`CD2 整理完成：${r.organized}/${r.total} 项成功`)
    } catch (e) { toastErr(String((e as Error).message)) } finally { setRenaming(false) }
  }

  return (
    <div className="page">
      <PageHead eyebrow="Downloaders" title={<>下载器<em>配置</em></>}
        sub="将磁力链接推送到 CloudDrive2（离线下载）或 qBittorrent。">
        <button className="btn btn--gold" onClick={save}><Icon.download />保存</button>
      </PageHead>

      <Quota115Card />
      <StrategyCard />

      {validation.general && <div style={{ color: 'var(--red)', fontSize: 13, marginBottom: 16 }}>⚠ {validation.general}</div>}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 22 }}>
        {/* CloudDrive2 */}
        <div className="card">
          <div className="card-head"><div className="card-title"><Icon.download /> CloudDrive2</div></div>
          <div className="field">
            <label htmlFor="cd-url">服务器地址</label>
            <input id="cd-url" className="input" value={s.clouddrive_url} onChange={(e) => upd({ clouddrive_url: e.target.value })} placeholder="host:port" />
            {validation.cdUrl && <span style={{ color: 'var(--red)', fontSize: 11 }}>{validation.cdUrl}</span>}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
            <div className="field"><label htmlFor="cd-username">用户名</label><input id="cd-username" className="input" value={s.clouddrive_username} onChange={(e) => upd({ clouddrive_username: e.target.value })} /></div>
            <div className="field"><label htmlFor="cd-password">密码</label><input id="cd-password" className="input" type="password" value={s.clouddrive_password} onChange={(e) => upd({ clouddrive_password: e.target.value })} /></div>
          </div>
          <div className="field"><label htmlFor="cd-token">或 Token（二选一）</label><input id="cd-token" className="input" value={s.clouddrive_token} onChange={(e) => upd({ clouddrive_token: e.target.value })} placeholder="eyJhbGciOi..." /></div>
          <div className="field"><label htmlFor="cd-save-path">离线下载目录</label><input id="cd-save-path" className="input" value={s.clouddrive_save_path} onChange={(e) => upd({ clouddrive_save_path: e.target.value })} /></div>
          <button className="btn btn--ghost btn--sm" onClick={() => test('clouddrive')} disabled={testing !== null}>
            {testing === 'clouddrive' ? '测试中…' : '测试连接'}
          </button>
        </div>

        {/* qBittorrent */}
        <div className="card">
          <div className="card-head"><div className="card-title"><Icon.download /> qBittorrent</div></div>
          <div className="field">
            <label htmlFor="qb-url">WebUI 地址</label>
            <input id="qb-url" className="input" value={s.qbittorrent_url} onChange={(e) => upd({ qbittorrent_url: e.target.value })} placeholder="http://host:8080" />
            {validation.qbUrl && <span style={{ color: 'var(--red)', fontSize: 11 }}>{validation.qbUrl}</span>}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
            <div className="field"><label htmlFor="qb-username">用户名</label><input id="qb-username" className="input" value={s.qbittorrent_username} onChange={(e) => upd({ qbittorrent_username: e.target.value })} /></div>
            <div className="field"><label htmlFor="qb-password">密码</label><input id="qb-password" className="input" type="password" value={s.qbittorrent_password} onChange={(e) => upd({ qbittorrent_password: e.target.value })} /></div>
          </div>
          <div className="field"><label htmlFor="qb-save-path">下载保存路径</label><input id="qb-save-path" className="input" value={s.qbittorrent_save_path} onChange={(e) => upd({ qbittorrent_save_path: e.target.value })} /></div>
          <div className="hint">路径以 / 开头视为绝对路径，否则拼接默认目录</div>
          <button className="btn btn--ghost btn--sm" onClick={() => test('qbittorrent')} disabled={testing !== null}>
            {testing === 'qbittorrent' ? '测试中…' : '测试连接'}
          </button>
        </div>
      </div>

      <div className="card" style={{ marginTop: 22 }}>
        <div className="card-head"><div className="card-title">默认下载器</div></div>
        <div className="seg">
          <button className={s.default_downloader === 'clouddrive' ? 'on' : ''} onClick={() => upd({ default_downloader: 'clouddrive' })}>CloudDrive2</button>
          <button className={s.default_downloader === 'qbittorrent' ? 'on' : ''} onClick={() => upd({ default_downloader: 'qbittorrent' })}>qBittorrent</button>
        </div>
        <div className="hint" style={{ marginTop: 10 }}>推送到下载器时，若未单独指定则使用此默认项</div>
      </div>

      {/* CD2 下载文件整理（推送成功后原地重命名 + 清理） */}
      <div className="card" style={{ marginTop: 22 }}>
        <div className="card-head" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div className="card-title">CD2 下载文件整理</div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 13 }}>
            <input
              type="checkbox"
              checked={String(s.cd2_rename_enabled) === 'true' || s.cd2_rename_enabled === true}
              onChange={(e) => upd({ cd2_rename_enabled: e.target.checked })}
            />
            <span>启用</span>
          </label>
        </div>
        <div className="hint" style={{ marginBottom: 12 }}>
          推送 CloudDrive2 成功后延迟整理下载文件夹：≥200MB 的视频文件原地重命名为番号（如 ABC-123.mp4，多文件加 -2/-3），其余文件（小视频/剧照/txt 等）删除
        </div>
        <div className="field">
          <label htmlFor="cd2-dl-folder">CD2 下载文件夹（整理范围）</label>
          <input id="cd2-dl-folder" className="input" value={s.cd2_download_folder || ''} onChange={(e) => upd({ cd2_download_folder: e.target.value })} placeholder="/115Cloud/离线下载" />
        </div>
        <div className="field">
          <label htmlFor="cd2-rename-delay">延迟触发秒数（等 CD2 下载完成）</label>
          <input
            id="cd2-rename-delay" type="number" min={0} className="input"
            value={s.cd2_rename_delay_seconds ?? 300}
            onChange={(e) => { const v = e.target.value; if (v !== '') upd({ cd2_rename_delay_seconds: +v }) }}
          />
          <div className="hint">CD2 离线下载大文件慢，建议 300-600 秒；超时未下完会跳过（下次推送另一番号不会误伤）</div>
        </div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <button className="btn btn--ghost btn--sm" onClick={() => test('cd2_rename')} disabled={testing !== null}>
            {testing === 'cd2_rename' ? '测试中…' : '测试（列下载文件夹）'}
          </button>
          <button className="btn btn--gold btn--sm" onClick={renameAll} disabled={renaming}>
            {renaming ? '整理中…' : '一键整理全部'}
          </button>
        </div>
      </div>

      <DownloaderLog />
    </div>
  )
}

function DownloaderLog() {
  const [lines, setLines] = useState<string[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = () => {
    setLines(null); setError(null)
    api.downloaders.logs(100).then((r) => setLines(r.lines)).catch((e) => setError(String((e as Error).message)))
  }
  useEffect(() => { load() }, [])

  return (
    <div className="card" style={{ marginTop: 22 }}>
      <div className="card-head" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div className="card-title"><Icon.console /> 下载器日志</div>
        <button className="btn btn--ghost btn--sm" onClick={load}>刷新</button>
      </div>
      {error ? <div style={{ color: 'var(--red)', fontSize: 13 }}>{error}</div> :
       lines === null ? <div style={{ color: 'var(--t-faint)', fontSize: 13 }}>加载中…</div> :
       lines.length === 0 ? <div style={{ color: 'var(--t-faint)', fontSize: 13 }}>暂无日志</div> :
       <pre style={{
         maxHeight: 360, overflow: 'auto', background: 'var(--bg-page)', borderRadius: 'var(--r-md)',
         padding: 12, fontSize: 12, fontFamily: 'var(--ff-mono)', color: 'var(--t-body)',
         whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0,
       }}>{lines.join('\n')}</pre>}
    </div>
  )
}

function Quota115Card() {
  const [q, setQ] = useState<{ ok: boolean; total?: number | null; used?: number | null; remain?: number | null; message?: string } | null>(null)
  useEffect(() => {
    api.quota115().then(setQ).catch(() => setQ({ ok: false, message: '查询失败' }))
  }, [])
  if (!q) return null
  const total = q.total ?? 0
  const used = q.used ?? 0
  const pct = total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0
  const gb = (n: number | null | undefined) => (n == null ? '?' : (n / 1024 / 1024 / 1024).toFixed(1))
  return (
    <div className="card" style={{ marginBottom: 16, padding: '12px 14px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
        <div style={{ fontSize: 12, fontWeight: 600 }}>115 离线配额</div>
        <div style={{ fontSize: 11, color: 'var(--t-mute)' }}>
          {q.ok ? `已用 ${gb(used)}G / 总 ${gb(total)}G（剩 ${gb(q.remain)}G）` : (q.message || '未授权或不可用')}
        </div>
      </div>
      {q.ok && total > 0 && (
        <div style={{ height: 6, borderRadius: 3, background: 'var(--bg-raised, #f3f4f6)', overflow: 'hidden' }}>
          <div style={{ width: `${pct}%`, height: '100%', background: pct > 90 ? 'var(--red, #dc2626)' : 'var(--gold, #d97706)', borderRadius: 3 }} />
        </div>
      )}
    </div>
  )
}

function StrategyCard() {
  const [text, setText] = useState('')
  const toastOk = useStore((s) => s.toastOk)
  const toastErr = useStore((s) => s.toastErr)
  useEffect(() => {
    api.downloadStrategy.get().then((r) => setText(JSON.stringify(r.strategy || {}, null, 2))).catch(() => {})
  }, [])
  const save = async () => {
    try {
      const strategy = JSON.parse(text || '{}')
      await api.downloadStrategy.set(strategy)
      toastOk('下载策略已保存')
    } catch (e) { toastErr(String((e as Error).message)) }
  }
  return (
    <div className="card" style={{ marginBottom: 16, padding: '12px 14px' }}>
      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>智能下载策略（按演员/厂牌路由下载器）</div>
      <div style={{ fontSize: 10, color: 'var(--t-mute)', marginBottom: 6 }}>
        格式：{"{ actors: { 演员名: qbittorrent 或 clouddrive }, makers: { 厂牌: ... }, default: qbittorrent }"}
      </div>
      <textarea className="input" rows={5} value={text} onChange={(e) => setText(e.target.value)}
        style={{ width: '100%', fontFamily: 'monospace', fontSize: 11 }} />
      <button className="btn btn--gold btn--sm" onClick={save} style={{ marginTop: 8 }}>保存策略</button>
    </div>
  )
}
