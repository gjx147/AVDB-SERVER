import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { Actor } from '../api/types'
import { PageHead, Empty, ErrorEmpty } from '../components/States'
import { SkeletonGallery } from '../components/Skeleton'
import { Icon } from '../components/Icons'
import { useStore } from '../store/useStore'

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
  const toastOk = useStore((s) => s.toastOk)
  const toastErr = useStore((s) => s.toastErr)

  const load = useCallback((keyword?: string, opts?: { withAvatar?: boolean; followed?: boolean }) => {
    setActors(null)
    setError(null)
    const wa = opts?.withAvatar !== undefined ? opts.withAvatar : onlyWithAvatar
    const fd = opts?.followed !== undefined ? opts.followed : onlyFollowed
    const p = keyword?.trim()
      ? api.actors.search(keyword.trim())
      : api.actors.list(0, 120, wa, fd)
    p.then(setActors).catch((e) => { setError(String((e as Error).message)); setActors([]) })
  }, [onlyWithAvatar, onlyFollowed])
  useEffect(() => { load() }, [load])

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
        toastOk(`已关注 ${a.name}，有新作将通知你`)
      }
    } catch (e) { toastErr(String((e as Error).message)) }
  }
  // 一键补齐演员作品
  const crawlWorks = async (a: Actor) => {
    if (!a.source_url) {
      toastErr('该演员无 JavDB URL，需先通过 URL 添加')
      return
    }
    try {
      await api.actors.crawlWorks(a.id)
      toastOk(`已开始补齐 ${a.name} 的作品`)
    } catch (e) { toastErr(String((e as Error).message)) }
  }
  return (
    <div className="page">
      <PageHead eyebrow={`Actors · ${actors?.length ?? 0} 位`} title={<>演员<em>库</em></>}
        sub="按演员浏览作品集合，支持搜索与通过详情页 URL 添加。">
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
            onChange={(e) => setKw(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && load(kw)} />
        </div>
        <button className="btn btn--ghost btn--sm" onClick={() => load(kw)}>搜索</button>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--t-mute)', cursor: 'pointer', userSelect: 'none' }}>
          <input type="checkbox" checked={onlyWithAvatar} onChange={(e) => { setOnlyWithAvatar(e.target.checked); load(undefined, { withAvatar: e.target.checked }) }} />
          只看有头像
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--t-mute)', cursor: 'pointer', userSelect: 'none' }}>
          <input type="checkbox" checked={onlyFollowed} onChange={(e) => { setOnlyFollowed(e.target.checked); load(undefined, { followed: e.target.checked }) }} />
          只看关注
        </label>
      </div>

      {error ? <ErrorEmpty message={error} onRetry={() => load(kw)} /> :
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
                {a.avatar_url ? <img src={a.avatar_url} alt={a.name} referrerPolicy="no-referrer" /> : <div style={{ width: '100%', height: '100%', background: 'var(--bg-page)' }} />}
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
                  disabled={!a.source_url}
                  title={a.source_url ? '爬取该演员的全部作品并入库' : '无 JavDB URL（需先通过 URL 添加）'}
                  style={{ flex: 1 }}>补齐作品</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
