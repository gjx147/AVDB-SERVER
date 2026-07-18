import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { ListSourceWithStats } from '../api/types'
import { PageHead, Loading, Empty, ErrorEmpty } from '../components/States'
import { Icon } from '../components/Icons'
import { useStore } from '../store/useStore'

export function ListSources() {
  const [sources, setSources] = useState<ListSourceWithStats[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [code, setCode] = useState('')
  const [maxPages, setMaxPages] = useState<number>(0)  // 0 = 用列表源自己的 max_pages
  const toastOk = useStore((s) => s.toastOk)
  const toastErr = useStore((s) => s.toastErr)

  const load = () => {
    api.listSources.list().then((d) => { setSources(d); setError(null) }).catch((e) => { setError(String((e as Error).message)); setSources([]) })
    api.settings.get().then((s) => { setMaxPages(s.max_pages_default || 0) }).catch(() => {})
  }
  useEffect(load, [])

  const create = async () => {
    if (!code.trim()) return
    try {
      await api.listSources.create({ list_code: code.trim().toUpperCase(), max_pages: maxPages || undefined })
      toastOk('已创建列表源')
      setCode(''); setAdding(false); load()
    } catch (e) { toastErr(String((e as Error).message)) }
  }
  const scan = async (s: ListSourceWithStats) => {
    try { await api.crawl.scan({ list_source_id: s.id, pages: maxPages || undefined }); toastOk(`已开始扫描 ${s.list_code}`) } catch (e) { toastErr(String((e as Error).message)) }
  }
  const extract = async (s: ListSourceWithStats) => {
    try { await api.crawl.extract({ list_source_id: s.id }); toastOk(`已开始提取 ${s.list_code}`) } catch (e) { toastErr(String((e as Error).message)) }
  }
  const remove = async (s: ListSourceWithStats) => {
    if (!confirm(`确定删除列表源 ${s.list_code} 及其所有任务？`)) return
    try { await api.listSources.remove(s.id); toastOk('已删除'); load() } catch (e) { toastErr(String((e as Error).message)) }
  }

  return (
    <div className="page">
      <PageHead eyebrow={`Sources · ${sources?.length ?? 0} 个`} title={<>列表源<em>管理</em></>}
        sub="列表源是番号集合的来源。每个源对应 JavDB 的一个列表，可独立扫描与提取。">
        <button className="btn btn--gold" onClick={() => setAdding(!adding)}><Icon.plus />新建列表源</button>
      </PageHead>

      {adding && (
        <div className="card" style={{ marginBottom: 24 }}>
          <div className="field" style={{ marginBottom: 0 }}>
            <label>列表代码 <span className="req">*</span></label>
            <div style={{ display: 'flex', gap: 10 }}>
              <input className="input" placeholder="如 HALT、MIUM…" value={code} onChange={(e) => setCode(e.target.value)} />
              <button className="btn btn--gold" onClick={create}>创建</button>
            </div>
          </div>
        </div>
      )}

      {error ? <ErrorEmpty message={error} onRetry={load} /> :
       sources === null ? <Loading /> : sources.length === 0 ? (
        <Empty icon="○" title="暂无列表源" sub="请新建一个列表源。" />
      ) : (
        <div className="card">
          <table className="table">
            <thead><tr><th>列表代码</th><th>已扫页数</th><th>任务数</th><th>待处理</th><th>失败</th><th>最近扫描</th><th>操作</th></tr></thead>
            <tbody>
              {sources.map((s) => (
                <tr key={s.id}>
                  <td className="mono">{s.list_code}</td>
                  <td>{s.last_scanned_page} / {s.max_pages}</td>
                  <td>{s.visited_count + s.pending_count + s.failed_count}</td>
                  <td>{s.pending_count > 0 ? <span className="chip chip-amber">{s.pending_count}</span> : <span className="chip chip-green">0</span>}</td>
                  <td>{s.failed_count > 0 ? <span className="chip chip-red">{s.failed_count}</span> : <span className="chip chip-green">0</span>}</td>
                  <td>{s.last_scanned_at || '—'}</td>
                  <td>
                    <button className="btn btn--ghost btn--sm" onClick={() => scan(s)}>扫描</button>{' '}
                    <button className="btn btn--ghost btn--sm" onClick={() => extract(s)}>提取</button>{' '}
                    <button className="btn btn--danger btn--sm" onClick={() => remove(s)}>删除</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
