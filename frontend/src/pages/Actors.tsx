import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { Actor } from '../api/types'
import { PageHead, Loading, Empty, ErrorEmpty } from '../components/States'
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
  const toastOk = useStore((s) => s.toastOk)
  const toastErr = useStore((s) => s.toastErr)

  const load = useCallback((keyword?: string, withAvatar?: boolean) => {
    setActors(null)
    setError(null)
    const wa = withAvatar !== undefined ? withAvatar : onlyWithAvatar
    const p = keyword?.trim() ? api.actors.search(keyword.trim()) : api.actors.list(0, 120, wa)
    p.then(setActors).catch((e) => { setError(String((e as Error).message)); setActors([]) })
  }, [onlyWithAvatar])
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
  // F9: 关注/取关演员
  const toggleFollow = async (actorId: number, isFollowed?: number) => {
    try {
      if (isFollowed) {
        await api.actorsFollow.unfollow(actorId)
        toastOk('已取消关注')
      } else {
        await api.actorsFollow.follow(actorId)
        toastOk('已关注，有新作将通知你')
      }
      // 更新本地状态
      setActors((prev) => prev ? prev.map((a) => a.id === actorId ? { ...a, is_followed: isFollowed ? 0 : 1 } : a) : prev)
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
  // 一键订阅演员作品（有新作自动入库）
  const subscribe = async (a: Actor) => {
    try {
      await api.subscriptions.create({ sub_type: 'actor', actor_id: a.id, name: a.name, auto_add: true })
      setSubscribedIds((prev) => new Set(prev).add(a.id))
      toastOk(`已订阅 ${a.name}，有新作将自动入库`)
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
          <input type="checkbox" checked={onlyWithAvatar} onChange={(e) => { setOnlyWithAvatar(e.target.checked); load(undefined, e.target.checked) }} />
          只看有头像
        </label>
      </div>

      {error ? <ErrorEmpty message={error} onRetry={() => load(kw)} /> :
       actors === null ? <Loading /> : actors.length === 0 ? (
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
                {/* F9: 关注按钮 */}
                <button
                  onClick={(e) => { e.stopPropagation(); toggleFollow(a.id, a.is_followed) }}
                  style={{
                    position: 'absolute', top: 6, right: 6, border: 'none', borderRadius: '50%',
                    width: 28, height: 28, cursor: 'pointer', fontSize: 14, lineHeight: 1,
                    background: a.is_followed ? 'var(--gold)' : 'rgba(0,0,0,.5)', color: '#fff',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    transition: 'all .2s',
                  }} title={a.is_followed ? '取消关注' : '关注'}>{a.is_followed ? '♥' : '♡'}</button>
              </div>
              <div className="actor-name">{a.name}</div>
              <div className="actor-count">{a.movie_count} 部作品{a.local_movie_count ? ` · 本地 ${a.local_movie_count}` : ''}</div>
              <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
                <button
                  onClick={(e) => { e.stopPropagation(); crawlWorks(a) }}
                  disabled={!a.source_url}
                  title={a.source_url ? '爬取该演员的全部作品并入库' : '无 JavDB URL（需先通过 URL 添加）'}
                  style={{
                    flex: 1, border: '1px solid var(--line-soft)', background: 'var(--bg-page)',
                    color: 'var(--t-body)', borderRadius: 6, padding: '5px 8px', fontSize: 11,
                    cursor: a.source_url ? 'pointer' : 'not-allowed', opacity: a.source_url ? 1 : 0.4,
                    fontFamily: 'var(--ff-sans)', transition: 'all .2s',
                  }}>补齐作品</button>
                <button
                  onClick={(e) => { e.stopPropagation(); subscribe(a) }}
                  disabled={subscribedIds.has(a.id)}
                  title={subscribedIds.has(a.id) ? '已订阅' : '订阅该演员，有新作自动入库'}
                  style={{
                    flex: 1, border: 'none', borderRadius: 6, padding: '5px 8px', fontSize: 11,
                    cursor: subscribedIds.has(a.id) ? 'default' : 'pointer', fontFamily: 'var(--ff-sans)',
                    background: subscribedIds.has(a.id) ? 'var(--gold-wash)' : 'var(--gold)',
                    color: subscribedIds.has(a.id) ? 'var(--gold)' : '#fff', transition: 'all .2s',
                  }}>{subscribedIds.has(a.id) ? '✓ 已订阅' : '订阅'}</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
