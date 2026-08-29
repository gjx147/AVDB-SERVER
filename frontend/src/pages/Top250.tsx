import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { Task } from '../api/types'
import { PosterCard } from '../components/PosterCard'
import { PageHead, Empty } from '../components/States'
import { SkeletonGallery } from '../components/Skeleton'
import { Icon } from '../components/Icons'
import { useStore } from '../store/useStore'

const CATS = [
  { kind: 6, label: '总榜' },
  { kind: 7, label: '有码' },
  { kind: 8, label: '无码' },
  { kind: 9, label: '欧美' },
  { kind: 10, label: 'FC2' },
]
const YEARS = Array.from({ length: 18 }, (_, i) => 2025 - i)

interface Entry {
  id: number
  kind: number
  rank: number
  number: string
  name: string
  date: string | null
  poster_url: string | null
  magnet_version: string | null
  task_id: number | null
  in_library: boolean
}

function asTask(e: Entry): Task {
  return {
    id: e.task_id ?? -(e.id),
    video_code: e.number,
    title: e.name,
    poster_url: e.poster_url ?? undefined,
    status: e.task_id ? 'visited' : 'pending',
    release_date: e.date ?? undefined,
    updated_at: '',
  } as unknown as Task
}

export function Top250View({ mode }: { mode: 'cat' | 'year' }) {
  const nav = useNavigate()
  const toastOk = useStore((s) => s.toastOk)
  const toastErr = useStore((s) => s.toastErr)
  const [kind, setKind] = useState(mode === 'cat' ? 6 : 2025)
  const [list, setList] = useState<Entry[] | null>(null)
  const [view, setView] = useState<'grid' | 'row'>('grid')
  const [searchQ, setSearchQ] = useState('')
  const [filterStatus, setFilterStatus] = useState<'all' | 'visited' | 'pending'>('all')
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const csvRef = useRef<HTMLInputElement>(null)
  const magRef = useRef<HTMLInputElement>(null)

  const load = useCallback(async (k: number) => {
    try {
      const r = await api.top250.list(k)
      setList(r.items)
    } catch (e) { toastErr(String((e as Error).message)) }
  }, [toastErr])

  useEffect(() => { load(kind) }, [kind, load])

  const doQuery = async () => {
    setBusy('query')
    try {
      const r = await api.top250.query(kind)
      toastOk(`${r.label}：${r.grand_total} 部已就绪`)
      setMsg(`${r.label} 查询完成`)
      await load(kind)
    } catch (e) { toastErr(String((e as Error).message)) } finally { setBusy('') }
  }

  const tryImport = async (cf: File | undefined, mf: File | undefined) => {
    if (!cf || !mf) return
    setBusy('import')
    try {
      const r = await api.top250.import(kind, cf, mf)
      toastOk(`导入完成：csv ${r.csv_rows} 行，磁力匹配 ${r.magnet_matched}/${r.magnet_rows}`)
      setMsg('导入完成')
      await load(kind)
    } catch (e) { toastErr(String((e as Error).message)) } finally { setBusy('') }
  }

  const doCrawl = async () => {
    setBusy('crawl')
    try {
      const r = await api.top250.crawlMissing(kind)
      toastOk(r.message)
      setMsg(r.message)
      await load(kind)
    } catch (e) { toastErr(String((e as Error).message)) } finally { setBusy('') }
  }

  const addOne = async (e: Entry) => {
    try {
      const r = await api.top250.addTask(e.id)
      toastOk(r.message)
      await load(kind)
    } catch (err) { toastErr(String((err as Error).message)) }
  }

  const entryClick = (e: Entry) => {
    if (e.task_id) nav(`/task/${e.task_id}`)
    else addOne(e)
  }

  const entries = list ?? []
  const filtered = entries.filter((e) => {
    const inLib = e.task_id != null
    if (searchQ && !e.number.toUpperCase().includes(searchQ.toUpperCase())) return false
    if (filterStatus === 'visited' && !inLib) return false
    if (filterStatus === 'pending' && inLib) return false
    return true
  })
  const showPodium = view === 'grid' && !searchQ.trim() && filterStatus === 'all'
  const podiumEntries = showPodium ? filtered.slice(0, 3) : []
  const restEntries = showPodium ? filtered.slice(3) : filtered

  return (
    <div className="page">
      <PageHead eyebrow="Top250" title={mode === 'cat' ? <>TOP250 · <em>类别</em></> : <>TOP250 · <em>年份</em></>}
        sub={mode === 'cat'
          ? 'JavDB 分类 TOP250——总榜/有码/无码/欧美/FC2，一键入库走完整链路。'
          : 'JavDB 年度 TOP250——按年份浏览历届高分片单，一键入库走完整链路。'}>
        <button className="btn btn--ghost btn--sm" onClick={doQuery} disabled={busy !== ''}>
          <Icon.refresh />{busy === 'query' ? '查询中…' : '查询数据源'}
        </button>
        <button className="btn btn--ghost btn--sm" disabled={busy !== ''}
          onClick={() => csvRef.current?.click()}>
          <Icon.upload />导入 csv
        </button>
        <button className="btn btn--ghost btn--sm" disabled={busy !== ''}
          onClick={() => magRef.current?.click()}>
          导入磁力
        </button>
        <input ref={csvRef} type="file" accept=".csv" style={{ display: 'none' }}
          onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ''; tryImport(f, magRef.current?.files?.[0]) }} />
        <input ref={magRef} type="file" accept=".txt" style={{ display: 'none' }}
          onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ''; tryImport(csvRef.current?.files?.[0], f) }} />
      </PageHead>

      <div className="gallery-toolbar">
        {mode === 'cat' && (
          <div className="seg">
            {CATS.map((c) => (
              <button key={c.kind} className={kind === c.kind ? 'on' : ''}
                onClick={() => setKind(c.kind)}>{c.label}</button>
            ))}
          </div>
        )}
        <div className="search">
          <Icon.search />
          <input placeholder="搜索番号…" value={searchQ}
            onChange={(e) => setSearchQ(e.target.value)} />
        </div>
        <select className="select" value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value as typeof filterStatus)}>
          <option value="all">全部状态</option>
          <option value="visited">已入库</option>
          <option value="pending">待入库</option>
        </select>
        <div className="seg">
          <button className={view === 'grid' ? 'on' : ''} onClick={() => setView('grid')}>画廊</button>
          <button className={view === 'row' ? 'on' : ''} onClick={() => setView('row')}>列表</button>
        </div>
        <button className="btn btn--sm" onClick={doCrawl} disabled={busy !== ''}>
          {busy === 'crawl' ? '启动中…' : '爬取未入库'}
        </button>
      </div>

      {mode === 'year' && (
        <div className="gallery-toolbar" style={{ marginTop: -8 }}>
          <div className="seg" style={{ flexWrap: 'wrap' }}>
            {YEARS.map((y) => (
              <button key={y} className={kind === y ? 'on' : ''} onClick={() => setKind(y)}>{y}</button>
            ))}
          </div>
          <button className="btn btn--ghost btn--sm" onClick={doQuery} disabled={busy !== ''}>
            <Icon.refresh />查询 {kind} 榜
          </button>
        </div>
      )}
      {msg ? <div className="hint" style={{ margin: '4px 0 10px' }}>{msg}</div> : null}

      {list === null ? <SkeletonGallery /> : entries.length === 0 ? (
        <Empty icon="○" title="暂无数据"
          sub="先点「查询数据源」（jinjier 数据包）或通过页头按钮手动导入 csv 与磁力文件。" />
      ) : filtered.length === 0 ? (
        <Empty icon="○" title="无匹配结果" sub="尝试更换筛选条件或搜索关键词。" />
      ) : view === 'grid' ? (
        <>
          {podiumEntries.length > 0 && (
            <div className="podium">
              {podiumEntries.map((e) => (
                <PosterCard key={e.id} task={asTask(e)} rank={e.rank}
                  onClick={() => entryClick(e)} />
              ))}
            </div>
          )}
          <div className="gallery">
            {restEntries.map((e) => (
              <PosterCard key={e.id} task={asTask(e)} rank={e.rank <= 10 ? e.rank : undefined}
                onClick={() => entryClick(e)} />
            ))}
          </div>
        </>
      ) : (
        <div className="card">
          {filtered.map((e) => (
            <div className="row-item" key={e.id} role="button" tabIndex={0}
              onClick={() => entryClick(e)}
              onKeyDown={(ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); entryClick(e) } }}>
              <img className="row-thumb" src={e.poster_url ?? ''} alt={`${e.number} 封面`}
                referrerPolicy="no-referrer"
                onError={(ev) => { ev.currentTarget.style.visibility = 'hidden' }} />
              <div>
                <div className="row-code">
                  {e.rank <= 3 ? (
                    <span className={`rank-badge rank-badge--inline rb-${e.rank}`}>{e.rank}</span>
                  ) : <span>#{e.rank} </span>}
                  {e.number}
                </div>
                <div className="row-title">{e.name}</div>
              </div>
              <div className="row-tags">
                {e.task_id ? <span className="inlib">已在影片库</span> : <span className="miss">待入库</span>}
                {e.magnet_version ? <span className="ver">{e.magnet_version}</span> : null}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export function Top250Cats() { return <Top250View mode="cat" /> }
export function Top250Years() { return <Top250View mode="year" /> }
