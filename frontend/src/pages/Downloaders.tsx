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

  const test = async (kind: 'clouddrive' | 'qbittorrent') => {
    if (!validate()) return
    try {
      // 先保存再测试（因为测试接口从 DB 读取配置）
      await api.settings.update(s)
      setTesting(kind)
      const sp = kind === 'clouddrive' ? s.clouddrive_save_path : s.qbittorrent_save_path
      await api.downloaders.testConnection(kind, sp || undefined)
      toastOk(`${kind === 'clouddrive' ? 'CloudDrive2' : 'qBittorrent'} 连接成功`)
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
    </div>
  )
}
