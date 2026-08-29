import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { Loading, ErrorEmpty } from '../components/States'
import { useStore } from '../store/useStore'

type KindGroup = 'type' | 'year'
const TYPE_KINDS = [
  { kind: 6, label: '总榜 TOP250' },
  { kind: 7, label: '有码 TOP250' },
  { kind: 8, label: '无码 TOP250' },
  { kind: 9, label: '欧美 TOP250' },
  { kind: 10, label: 'FC2 TOP250' },
]
const YEAR_KINDS = Array.from({ length: 18 }, (_, i) => 2025 - i)

interface Entry {
  id: number
  rank: number
  number: string
  name: string
  date: string | null
  magnet_version: string | null
  task_id: number | null
  in_library: boolean
}

const verColor = (v: string | null) => {
  if (v === '-UC') return 'bg-purple-100 text-purple-700'
  if (v === '-C') return 'bg-blue-100 text-blue-700'
  if (v === '-BD') return 'bg-amber-100 text-amber-700'
  return 'bg-gray-100 text-gray-600'
}

export function Top250() {
  const toastOk = useStore((st) => st.toastOk)
  const toastErr = useStore((st) => st.toastErr)
  const [group, setGroup] = useState<KindGroup>('type')
  const [kind, setKind] = useState(6)
  const [label, setLabel] = useState('')
  const [items, setItems] = useState<Entry[] | null>(null)
  const [q, setQ] = useState('')
  const [status, setStatus] = useState<'all' | 'in' | 'missing' | 'nomagnet'>('all')
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [csvF, setCsvF] = useState<File | null>(null)
  const [magF, setMagF] = useState<File | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = useCallback(async (k = kind, qq = q, st: string = status) => {
    try {
      const r = await api.top250.list(k, qq, st)
      setItems(r.items)
      setLabel(r.label)
    } catch (e) { toastErr(String((e as Error).message)) }
  }, [kind, q, status])

  useEffect(() => { load() }, [load])

  const doQuery = async () => {
    setBusy('query')
    try {
      const r = await api.top250.query(kind)
      toastOk(`${r.label}：${r.total} 部已入库条目池（缺番号 ${r.no_code}，已同步在库 ${r.in_library_synced}）`)
      setMsg(`${r.label} 查询完成：共 ${r.total} 部`)
      await load()
    } catch (e) { toastErr(String((e as Error).message)) } finally { setBusy('') }
  }

  const doImport = async () => {
    if (!csvF || !magF) { toastErr('请选择 top250-code.csv 和 top250-magnet.txt 两个文件'); return }
    setBusy('import')
    try {
      const r = await api.top250.import(kind, csvF, magF)
      toastOk(`导入完成：csv ${r.csv_rows} 行，磁力 ${r.magnet_matched}/${r.magnet_rows} 匹配`)
      setMsg(`手动导入完成：${r.label}（${r.csv_rows} 行，磁力匹配 ${r.magnet_matched}）`)
      await load()
    } catch (e) { toastErr(String((e as Error).message)) } finally { setBusy('') }
  }

  const doCrawl = async () => {
    setBusy('crawl')
    try {
      const r = await api.top250.crawlMissing(kind)
      toastOk(r.message)
      setMsg(r.message)
      if (timer.current) clearTimeout(timer.current)
      const poll = async () => {
        await load()
        timer.current = setTimeout(poll, 15000)
      }
      timer.current = setTimeout(poll, 15000)
    } catch (e) { toastErr(String((e as Error).message)) } finally { setBusy('') }
  }

  const doAdd = async (id: number) => {
    try {
      const r = await api.top250.addTask(id)
      toastOk(r.message)
      await load()
    } catch (e) { toastErr(String((e as Error).message)) }
  }

  const kinds = group === 'type' ? TYPE_KINDS : YEAR_KINDS.map((k) => ({ kind: k, label: `${k} TOP250` }))

  return (
    <div className="page">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-bold">Top250{label ? <span className="text-base text-gray-500 ml-2">{label}</span> : null}</h1>
      </div>

      <div className="card mb-4 p-4 space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex rounded-lg overflow-hidden border">
            <button className={`px-3 py-1.5 text-sm ${group === 'type' ? 'bg-blue-600 text-white' : 'bg-white'}`} onClick={() => { setGroup('type'); setKind(6) }}>按类型</button>
            <button className={`px-3 py-1.5 text-sm ${group === 'year' ? 'bg-blue-600 text-white' : 'bg-white'}`} onClick={() => { setGroup('year'); setKind(2025) }}>按年份</button>
          </div>
          <select className="input text-sm" value={kind} onChange={(e) => setKind(Number(e.target.value))}>
            {kinds.map((k) => <option key={k.kind} value={k.kind}>{k.label}</option>)}
          </select>
          <button className="btn text-sm" disabled={busy !== ''} onClick={doQuery}>
            {busy === 'query' ? '查询中…' : '从数据源查询'}
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-gray-500">手动导入：</span>
          <input type="file" accept=".csv" className="input text-xs py-1" onChange={(e) => setCsvF(e.target.files?.[0] ?? null)} />
          <input type="file" accept=".txt" className="input text-xs py-1" onChange={(e) => setMagF(e.target.files?.[0] ?? null)} />
          <button className="btn btn--sm" disabled={busy !== '' || !csvF || !magF} onClick={doImport}>
            {busy === 'import' ? '导入中…' : '导入 csv + 磁力'}
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <input className="input text-sm w-40" placeholder="番号搜索" value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') load() }} />
          <select className="input text-sm" value={status} onChange={(e) => { setStatus(e.target.value as typeof status); load(kind, q, e.target.value) }}>
            <option value="all">全部</option>
            <option value="in">已在影片库</option>
            <option value="missing">未入库</option>
            <option value="nomagnet">仅显示有磁力</option>
          </select>
          <button className="btn btn--sm" disabled={busy !== ''} onClick={doCrawl}>
            {busy === 'crawl' ? '启动中…' : '爬取未入库'}
          </button>
          <button className="btn btn--ghost btn--sm" onClick={() => load()}>刷新</button>
        </div>
        {msg ? <div className="text-xs text-gray-500">{msg}</div> : null}
      </div>

      {items === null ? <Loading /> : items.length === 0 ? (
        <ErrorEmpty message="暂无数据——先用「从数据源查询」或「手动导入」获取 TOP250 列表" />
      ) : (
        <div className="space-y-1.5">
          {items.map((it) => (
            <div key={it.id} className="card flex items-center gap-3 p-2.5">
              <span className="w-10 text-center font-bold text-gray-400">#{it.rank}</span>
              <span className="font-mono font-semibold w-32">{it.number}</span>
              <span className="flex-1 truncate text-sm text-gray-600" title={it.name}>{it.name}</span>
              {it.date ? <span className="text-xs text-gray-400">{it.date}</span> : null}
              {it.magnet_version ? (
                <span className={`text-xs px-2 py-0.5 rounded ${verColor(it.magnet_version)}`}>{it.magnet_version}</span>
              ) : null}
              {it.in_library ? (
                <Link to="/library" className="text-xs px-2 py-1 rounded bg-green-100 text-green-700">已在影片库</Link>
              ) : (
                <button className="btn btn--sm text-xs" disabled={busy !== ''} onClick={() => doAdd(it.id)}>入库</button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
