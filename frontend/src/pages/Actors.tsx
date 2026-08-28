import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { Actor } from '../api/types'
import { PageHead, Empty, ErrorEmpty } from '../components/States'
import { SkeletonGallery } from '../components/Skeleton'
import { Icon } from '../components/Icons'
import { useStore } from '../store/useStore'

const PAGE = 90

export function Actors() {
  const nav = useNavigate()
  const [actors, setActors] = useState<Actor[] | null>(null)
  const [kw, setKw] = useState('')
  const [adding, setAdding] = useState(false)
  const [url, setUrl] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [subscribedIds, setSubscribedIds] = useState<Set<number>>(new Set())
  const [onlyWithAvatar, setOnlyWithAvatar] = useState(true)  // 默认只显示有头像的演员
  const [onlyFollowed, setOnlyFollowed] = useState(false)  // 只看关注的
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [batchBusy, setBatchBusy] = useState(false)
  const [page, setPage] = useState(0)
  const [total, setTotal] = useState(0)
  // 一键提取演员信息：后台任务 + 轮询进度
  const [profStatus, setProfStatus] = useState<{ running: boolean; total: number; idx: number; current_name: string | null; done: number; skipped: number; failed: number; last_summary: string | null } | null>(null)
  const profPollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const toastOk = useStore((s) => s.toastOk)
  const toastErr = useStore((s) => s.toastErr)
  const confirmBox = useStore((s) => s.confirm)

  // T19: 请求竞态防护（快速切换筛选时丢弃过期响应，对齐 Library）
  const reqSeqRef = useRef(0)
  const load = useCallback((keyword?: string, opts?: { withAvatar?: boolean; followed?: boolean }, pageOverride?: number) => {
    const reqId = ++reqSeqRef.current
    setActors(null)
    setError(null)
    setSelected(new Set())
    const wa = opts?.withAvatar !== undefined ? opts.withAvatar : onlyWithAvatar
    const fd = opts?.followed !== undefined ? opts.followed : onlyFollowed
    const pg = pageOverride !== undefined ? pageOverride : page
    const q = keyword?.trim() || undefined
    api.actors.listPage(pg + 1, PAGE, wa, fd, q).then((r) => {
      if (reqId !== reqSeqRef.current) return
      setActors(r.items)
      setTotal(r.total)
    }).catch((e) => {
      if (reqId !== reqSeqRef.current) return
      setError(String((e as Error).message)); setActors([])
    })
  }, [onlyWithAvatar, onlyFollowed, page])
  useEffect(() => { load() }, [load])

  const goPage = (p: number) => { setPage(p); load(undefined, undefined, p) }
  const resetAndLoad = (keyword?: string, opts?: { withAvatar?: boolean; followed?: boolean }) => {
    setPage(0)
    load(keyword, opts, 0)
  }

  // ── 一键提取演员信息：后台任务 + 轮询 ──
  const startProfPolling = () => {
    if (profPollRef.current) clearInterval(profPollRef.current)
    profPollRef.current = setInterval(async () => {
      try {
        const s = await api.actors.extractProfilesStatus()
        setProfStatus(s)
        if (!s.running) {
          if (profPollRef.current) clearInterval(profPollRef.current)
          profPollRef.current = null
          if (s.last_summary) toastOk(s.last_summary)
          resetAndLoad(kw)
        }
      } catch {
        if (profPollRef.current) clearInterval(profPollRef.current)
        profPollRef.current = null
        setProfStatus(null)
      }
    }, 3000)
  }
  // 挂载时恢复展示进行中的后台任务
  useEffect(() => {
    api.actors.extractProfilesStatus().then((s) => {
      if (s?.running) { setProfStatus(s); startProfPolling() }
    }).catch(() => {})
    return () => { if (profPollRef.current) clearInterval(profPollRef.current) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const extractAllProfiles = async () => {
    if (profStatus?.running) return
    try {
      await api.actors.extractProfiles()
      toastOk('已启动演员信息一键提取（后台执行，切走页面不中断）')
      setProfStatus({ running: true, total: 0, idx: 0, current_name: null, done: 0, skipped: 0, failed: 0, last_summary: null })
      startProfPolling()
    } catch (e) {
      toastErr(String((e as Error).message))
    }
  }

  // 加载已订阅的演员 id 集合（用于按钮状态）
  useEffect(() => {
    api.subscriptions.list(true).then((list: unknown) => {
      const ids = new Set<number>()
      if (Array.isArray(list)) {
        for (const s of list as { sub_type?: string; actor_id?: number }[]) {
          if (s.sub_type === 'actor' && s.actor_id) ids.add(s.actor_id)
        }
      }
      setSubscribedIds(ids)
    }).catch(() => { /* 订阅列表可选，失败不阻塞 */ })
  }, [])

  const submitUrl = async () => {
    if (!url.trim()) return
    try { await api.actors.crawl(url.trim()); toastOk('已开始爬取演员'); setUrl(''); setAdding(false) }
    catch (e) { toastErr(String((e as Error).message)) }
  }
  // 关注/取关演员（关注 = 创建 actor 订阅，定时检测+通知）
  const toggleFollow = async (a: Actor) => {
    try {
      if (subscribedIds.has(a.id)) {
        await api.actors.unfollow(a.id)
        setSubscribedIds((prev) => { const n = new Set(prev); n.delete(a.id); return n })
        toastOk('已取消关注')
      } else {
        await api.actors.follow(a.id)
        setSubscribedIds((prev) => new Set(prev).add(a.id))
        toastOk(`已关注 ${a.name}，已开启新作自动入库，正在后台爬取其 JavDB 作品`)
      }
    } catch (e) { toastErr(String((e as Error).message)) }
  }
  // 一键补齐演员作品
  const crawlWorks = async (a: Actor) => {
    try {
      await api.actors.crawlWorks(a.id)
      toastOk(`已开始补齐 ${a.name} 的作品`)
    } catch (e) { toastErr(String((e as Error).message)) }
  }

  // ── 多选批量操作 ──
  const toggleSel = (id: number) => {
    setSelected((prev) => {
      const n = new Set(prev)
      n.has(id) ? n.delete(id) : n.add(id)
      return n
    })
  }
  const allSelected = actors !== null && actors.length > 0 && actors.every((a) => selected.has(a.id))
  const toggleAll = () => {
    if (!actors) return
    setSelected((prev) => {
      const allOnPage = actors.every((a) => prev.has(a.id))
      const n = new Set(prev)
      if (allOnPage) actors.forEach((a) => n.delete(a.id))
      else actors.forEach((a) => n.add(a.id))
      return n
    })
  }
  const batch = async (kind: 'follow' | 'delete') => {
    const sels = (actors || []).filter((a) => selected.has(a.id))
    if (!sels.length) return
    if (kind === 'delete') {
      const ok = await confirmBox('批量删除演员', `将删除 ${sels.length} 位演员记录（其作品任务不会被删除）。确定继续？`)
      if (!ok) return
    }
    setBatchBusy(true)
    try {
      let n = 0
      const B = 5
      for (let i = 0; i < sels.length; i += B) {
        await Promise.all(sels.slice(i, i + B).map(async (a) => {
          try {
            if (kind === 'follow') await api.actors.follow(a.id)
            else await api.actors.remove(a.id)
            n++
          } catch { /* 单个失败不中断 */ }
        }))
      }
      toastOk(kind === 'follow' ? `已关注 ${n} 位演员` : `已删除 ${n} 位演员`)
      setSelected(new Set())
      resetAndLoad(kw)
    } catch (e) {
      toastErr(String((e as Error).message))
    } finally {
      setBatchBusy(false)
    }
  }
  return (
    <div className="page">
      <PageHead eyebrow={`Actors · ${total} 位`} title={<>演员<em>库</em></>}
        sub="从心动的那张脸开始，补齐她的全部作品。">
        <button className="btn btn--ghost btn--sm" onClick={extractAllProfiles} disabled={profStatus?.running}
          title="一键提取全部待抓演员的信息（minnano/WAPdB/老师图鉴三源，后台串行执行，切走页面不中断）">
          <Icon.download />{profStatus?.running
            ? `提取中 · ${profStatus.current_name || '…'} (${profStatus.idx}/${profStatus.total || '…'})…`
            : '一键提取演员信息'}
        </button>
        <button className="btn btn--gold" onClick={() => setAdding(!adding)}><Icon.plus />粘贴演员 URL</button>
      </PageHead>

      {adding && (
        <div className="card" style={{ marginBottom: 24 }}>
          <div className="field" style={{ marginBottom: 0 }}>
            <label>演员详情页 URL</label>
            <div style={{ display: 'flex', gap: 10 }}>
              <input className="input" placeholder="粘贴 JavDB 演员详情页 URL…" value={url} onChange={(e) => setUrl(e.target.value)} />
              <button className="btn btn--gold" onClick={submitUrl}>添加</button>
            </div>
          </div>
        </div>
      )}

      <div className="gallery-toolbar">
        <div className="search">
          <Icon.search />
          <input placeholder="搜索演员名…" value={kw}
            onChange={(e) => setKw(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && resetAndLoad(kw)} />
        </div>
        <button className="btn btn--ghost btn--sm" onClick={() => resetAndLoad(kw)}>搜索</button>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--t-mute)', cursor: 'pointer', userSelect: 'none' }}>
          <input type="checkbox" checked={onlyWithAvatar} onChange={(e) => { setOnlyWithAvatar(e.target.checked); resetAndLoad(undefined, { withAvatar: e.target.checked }) }} />
          只看有头像
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--t-mute)', cursor: 'pointer', userSelect: 'none' }}>
          <input type="checkbox" checked={onlyFollowed} onChange={(e) => { setOnlyFollowed(e.target.checked); resetAndLoad(undefined, { followed: e.target.checked }) }} />
          只看关注
        </label>
        {actors && actors.length > 0 && (
          <button className="btn btn--ghost btn--sm" onClick={toggleAll}>{allSelected ? '取消全选' : '全选本页'}</button>
        )}
      </div>

      {error ? <ErrorEmpty message={error} onRetry={() => resetAndLoad(kw)} /> :
       actors === null ? <SkeletonGallery square /> : actors.length === 0 ? (
        <Empty icon="○" title="暂无演员" sub="请通过搜索或 URL 添加。" />
      ) : (
        <div className="actor-grid">
          {actors.map((a) => (
            <div className="actor" key={a.id} tabIndex={0} role="button"
              aria-label={`查看演员 ${a.name}`}
              onClick={() => nav(`/actor/${a.id}`)}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); nav(`/actor/${a.id}`) } }}
              style={{ cursor: 'pointer' }}>
              <div className="actor-photo">
                {a.avatar_url ? <img src={a.avatar_url} alt={a.name} referrerPolicy="no-referrer" loading="lazy" decoding="async" fetchPriority="low" /> : (
                  <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center',
                    background: 'linear-gradient(160deg, var(--bg-raised), var(--bg-page))',
                    color: 'var(--t-faint)', fontFamily: 'var(--ff-display)', fontStyle: 'italic', fontSize: 52 }}>
                    {a.name[0] || '?'}
                  </div>
                )}
                {/* 批量勾选（左上角） */}
                <div className={`actor-check${selected.has(a.id) ? ' on' : ''}`} role="checkbox"
                  aria-checked={selected.has(a.id)} tabIndex={0}
                  onClick={(e) => { e.stopPropagation(); toggleSel(a.id) }}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); toggleSel(a.id) } }}>
                  {selected.has(a.id) ? '✓' : ''}
                </div>
                {/* 关注按钮（关注 = actor 订阅） */}
                <button
                  onClick={(e) => { e.stopPropagation(); toggleFollow(a) }}
                  style={{
                    position: 'absolute', top: 6, right: 6, border: 'none', borderRadius: '50%',
                    width: 28, height: 28, cursor: 'pointer', fontSize: 14, lineHeight: 1,
                    background: subscribedIds.has(a.id) ? 'var(--gold)' : 'rgba(0,0,0,.5)', color: '#fff',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    transition: 'all .2s',
                  }} title={subscribedIds.has(a.id) ? '取消关注' : '关注'}>{subscribedIds.has(a.id) ? '♥' : '♡'}</button>
              </div>
              <div className="actor-name">{a.name}</div>
              <div className="actor-count">{a.movie_count} 部作品{a.local_movie_count ? ` · 本地 ${a.local_movie_count}` : ''}</div>
              <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
                <button
                  className="btn btn--ghost btn--sm"
                  onClick={(e) => { e.stopPropagation(); crawlWorks(a) }}
                  disabled={false}
                  title={'爬取该演员的全部作品并入库（无 URL 时按名搜索源站）'}
                  style={{ flex: 1 }}>补齐作品</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 分页 */}
      {total > PAGE && (
        <div className="pager">
          <button disabled={page === 0} onClick={() => goPage(page - 1)}>上一页</button>
          <span style={{ fontFamily: 'var(--ff-mono)', fontSize: 13, color: 'var(--t-mute)', padding: '0 14px' }}>
            {page * PAGE + 1}-{Math.min((page + 1) * PAGE, total)} / 共 {total} 条
          </span>
          <button disabled={(page + 1) * PAGE >= total} onClick={() => goPage(page + 1)}>下一页</button>
        </div>
      )}

      {/* 批量操作栏 */}
      <div className={`batchbar${selected.size ? ' show' : ''}`}>
        <span className="sel-count">已选 {selected.size} 项</span>
        <button className="btn btn--gold btn--sm" onClick={() => batch('follow')} disabled={batchBusy}>批量关注</button>
        <button className="btn btn--danger btn--sm" onClick={() => batch('delete')} disabled={batchBusy}>批量删除</button>
        <button className="btn btn--ghost btn--icon" onClick={() => setSelected(new Set())}>✕</button>
      </div>
    </div>
  )
}
