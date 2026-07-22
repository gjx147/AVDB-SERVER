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

  const test = async (kind: 'clouddrive' | 'qbittorrent' | 'cms' | 'cd2_organize') => {
    if (!validate()) return
    try {
      // 先保存再测试（因为测试接口从 DB 读取配置）
      await api.settings.update(s)
      setTesting(kind)
      const sp = kind === 'clouddrive' ? s.clouddrive_save_path : kind === 'qbittorrent' ? s.qbittorrent_save_path : ''
      await api.downloaders.testConnection(kind, sp || undefined)
      const label = kind === 'clouddrive' ? 'CloudDrive2' : kind === 'qbittorrent' ? 'qBittorrent' : kind === 'cms' ? 'CMS' : 'CD2 迁移'
      toastOk(`${label} 连接成功`)
    } catch (e) {
      const msg = String((e as Error).message)
      toastErr(msg.length > 120 ? msg.slice(0, 120) + '…' : msg)
    } finally { setTesting(null) }
  }

  return (
    <div className="page">
      <PageHead eyebrow="Downloaders" title={<>下载器<em>配置</em></>}
        sub="将磁力链接推送到 CloudDrive2（离线下载）或 qBittorrent。">
        <button className="btn btn--gold" onClick={save}><Icon.download />保存</button>
      </PageHead>

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

      {/* CMS 后处理（推送成功后自动转移 + 生成 strm） */}
      <div className="card" style={{ marginTop: 22 }}>
        <div className="card-head" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div className="card-title"><Icon.refresh /> CMS 自动整理（生成 strm）</div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 13 }}>
            <input
              type="checkbox"
              checked={String(s.cms_enabled) === 'true' || s.cms_enabled === true}
              onChange={(e) => upd({ cms_enabled: e.target.checked })}
            />
            <span>启用</span>
          </label>
        </div>
        <div className="hint" style={{ marginBottom: 12 }}>
          推送成功后延迟触发 CMS auto_organize，CMS 自动扫描云盘新文件、整理到媒体库并生成 .strm
        </div>
        <div className="field">
          <label htmlFor="cms-url">CMS 服务器地址</label>
          <input id="cms-url" className="input" value={s.cms_url || ''} onChange={(e) => upd({ cms_url: e.target.value })} placeholder="http://192.168.1.x:8080" />
        </div>
        <div className="field">
          <label htmlFor="cms-token">API Token</label>
          <input id="cms-token" className="input" value={s.cms_token || ''} onChange={(e) => upd({ cms_token: e.target.value })} placeholder="cloud_media_sync" />
          <div className="hint">由 CMS 启动变量 CMS_API_TOKEN 指定，默认 cloud_media_sync</div>
        </div>
        <div className="field">
          <label htmlFor="cms-delay">延迟触发秒数</label>
          <input
            id="cms-delay" type="number" min={0} className="input"
            value={s.cms_delay_seconds ?? 60}
            onChange={(e) => upd({ cms_delay_seconds: +e.target.value })}
          />
          <div className="hint">推送成功后等待 N 秒再触发整理，给下载器时间下载文件</div>
        </div>
        <button className="btn btn--ghost btn--sm" onClick={() => test('cms')} disabled={testing !== null}>
          {testing === 'cms' ? '测试中…' : '测试连接'}
        </button>
      </div>

      {/* CD2 自动迁移（MoveFile 到媒体库女优子目录 + 通知 CMS） */}
      <div className="card" style={{ marginTop: 22 }}>
        <div className="card-head" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div className="card-title"><Icon.refresh /> CD2 自动迁移到媒体库</div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 13 }}>
            <input
              type="checkbox"
              checked={String(s.cd2_organize_enabled) === 'true' || s.cd2_organize_enabled === true}
              onChange={(e) => upd({ cd2_organize_enabled: e.target.checked })}
            />
            <span>启用</span>
          </label>
        </div>
        <div className="hint" style={{ marginBottom: 12 }}>
          推送成功后，CD2 把下载文件按女优名移动到媒体库子目录，再通知 CMS 入库（strm 由 CMS 生成）
        </div>
        <div className="field">
          <label htmlFor="cd2-src">源文件夹（CD2 离线下载目录）</label>
          <input id="cd2-src" className="input" value={s.cd2_organize_source_folder || ''} onChange={(e) => upd({ cd2_organize_source_folder: e.target.value })} placeholder="/115Cloud/离线下载" />
        </div>
        <div className="field">
          <label htmlFor="cd2-tgt">媒体库根目录（迁移目标，按女优建子目录）</label>
          <input id="cd2-tgt" className="input" value={s.cd2_organize_target_folder || ''} onChange={(e) => upd({ cd2_organize_target_folder: e.target.value })} placeholder="/115Cloud/媒体库" />
        </div>
        <div className="field">
          <label htmlFor="cd2-delay">延迟触发秒数（等 CD2 下载完成）</label>
          <input
            id="cd2-delay" type="number" min={0} className="input"
            value={s.cd2_organize_delay_seconds ?? 120}
            onChange={(e) => upd({ cd2_organize_delay_seconds: +e.target.value })}
          />
          <div className="hint">CD2 下载大文件慢，建议 120-300 秒</div>
        </div>
        <button className="btn btn--ghost btn--sm" onClick={() => test('cd2_organize')} disabled={testing !== null}>
          {testing === 'cd2_organize' ? '测试中…' : '测试（列源文件夹）'}
        </button>
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
