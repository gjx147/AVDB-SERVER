import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import type { Actor } from '../api/types'
import { PageHead, Loading, Empty, ErrorEmpty } from '../components/States'
import { Icon } from '../components/Icons'
import { useStore } from '../store/useStore'
import { avatarUrl } from '../api/client'

export function Actors() {
  const [actors, setActors] = useState<Actor[] | null>(null)
  const [kw, setKw] = useState('')
  const [adding, setAdding] = useState(false)
  const [url, setUrl] = useState('')
  const [error, setError] = useState<string | null>(null)
  const toastOk = useStore((s) => s.toastOk)
  const toastErr = useStore((s) => s.toastErr)

  const load = useCallback((keyword?: string) => {
    setActors(null)
    setError(null)
    const p = keyword?.trim() ? api.actors.search(keyword.trim()) : api.actors.list(0, 120)
    p.then(setActors).catch((e) => { setError(String((e as Error).message)); setActors([]) })
  }, [])
  useEffect(() => { load() }, [load])

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
      </div>

      {error ? <ErrorEmpty message={error} onRetry={() => load(kw)} /> :
       actors === null ? <Loading /> : actors.length === 0 ? (
        <Empty icon="○" title="暂无演员" sub="请通过搜索或 URL 添加。" />
      ) : (
        <div className="actor-grid">
          {actors.map((a) => (
            <div className="actor" key={a.id} tabIndex={0} role="button"
              aria-label={`查看演员 ${a.name}`}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault() } }}>
              <div className="actor-photo">
                {a.avatar_url ? <img src={avatarUrl(a.id)} alt={a.name} /> : <div style={{ width: '100%', height: '100%', background: 'var(--bg-page)' }} />}
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
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
