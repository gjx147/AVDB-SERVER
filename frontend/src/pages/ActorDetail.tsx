import { useCallback, useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api, coverFileUrl } from '../api/client'
import type { Actor, ActorMovie } from '../api/types'
import { PageHead, Loading, Empty, ErrorEmpty } from '../components/States'
import { Icon } from '../components/Icons'
import { useStore } from '../store/useStore'

const PAGE_SIZE = 30

export function ActorDetail() {
  const { id } = useParams()
  const nav = useNavigate()
  const [actor, setActor] = useState<Actor | null | undefined>(undefined)
  const [movies, setMovies] = useState<ActorMovie[]>([])
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [sort, setSort] = useState<'added' | 'release' | 'rating'>('added')
  const [subscribed, setSubscribed] = useState(false)
  const [autoAdd, setAutoAdd] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const toastOk = useStore((s) => s.toastOk)
  const toastErr = useStore((s) => s.toastErr)

  const loadMovies = useCallback(async (p: number, s: 'added' | 'release' | 'rating') => {
    if (!id) return
    try {
      const r = await api.actors.movies(+id, p, PAGE_SIZE, s)
      setMovies(r.items)
      setTotal(r.total)
      setPage(p)
      setSort(s)
    } catch {
      setMovies([]); setTotal(0)
    }
  }, [id])

  useEffect(() => {
    if (!id) return
    setActor(undefined); setError(null); setMovies([]); setTotal(0); setPage(1)
    Promise.all([
      api.actors.get(+id).catch((e) => { setError(String((e as Error).message)); return null }),
      api.subscriptions.list(true).then((list: unknown) => {
        if (Array.isArray(list)) {
          const s = (list as { sub_type?: string; actor_id?: number; auto_add?: boolean }[])
            .find(x => x.sub_type === 'actor' && x.actor_id === +id)
          return { subscribed: !!s, autoAdd: !!(s && s.auto_add) }
        }
        return { subscribed: false, autoAdd: false }
      }).catch(() => ({ subscribed: false, autoAdd: false })),
    ]).then(([a, sub]) => {
      setActor(a as Actor | null)
      setSubscribed((sub as { subscribed: boolean }).subscribed)
      setAutoAdd((sub as { autoAdd: boolean }).autoAdd)
    })
    loadMovies(1, 'added')
  }, [id, loadMovies])

  const crawlWorks = async () => {
    if (!actor) return
    if (!actor.source_url) { toastErr('该演员无 JavDB URL，需先通过 URL 添加'); return }
    try {
      await api.actors.crawlWorks(actor.id)
      toastOk(`已开始补齐 ${actor.name} 的作品`)
    } catch (e) { toastErr(String((e as Error).message)) }
  }
  // 关注 = 创建 actor 订阅（定时检测+通知）；已关注则取消
  const toggleFollow = async () => {
    if (!actor) return
    try {
      if (subscribed) {
        await api.actors.unfollow(actor.id)
        setSubscribed(false); setAutoAdd(false)
        toastOk('已取消关注')
      } else {
        await api.actors.follow(actor.id)
        setSubscribed(true); setAutoAdd(false)
        toastOk(`已关注 ${actor.name}，有新作将通知你`)
      }
    } catch (e) { toastErr(String((e as Error).message)) }
  }
  // 切换自动入库（auto_add）：开启则有新作自动入库+下载
  const toggleAutoAdd = async () => {
    if (!actor) return
    try {
      const r = await api.actors.toggleAutoAdd(actor.id)
      setAutoAdd(r.auto_add)
      toastOk(r.auto_add ? '已开启自动入库' : '已关闭自动入库')
    } catch (e) { toastErr(String((e as Error).message)) }
  }

  if (actor === undefined) return <div className="page"><Loading /></div>
  if (actor === null) return <div className="page"><Empty title="演员不存在" /></div>
  if (error) return <div className="page"><ErrorEmpty message={error} onRetry={() => nav('/actors')} /></div>

  // 资料行（只显示有值的）
  const meta: [string, string | null][] = [
    ['出生日期', actor.birth_date],
    ['身高', actor.height],
    ['罩杯', actor.cup],
    ['作品数', actor.movie_count != null ? String(actor.movie_count) : null],
  ]
  const metaVisible = meta.filter(([, v]) => v)

  return (
    <div className="page">
      <button className="btn btn--ghost btn--sm" style={{ marginBottom: 20 }}
        onClick={() => { if (window.history.length > 1) nav(-1); else nav('/actors') }}><Icon.back />返回</button>

      {/* 头部：头像 + 信息 */}
      <div style={{ display: 'grid', gridTemplateColumns: '160px 1fr', gap: 28, marginBottom: 32, alignItems: 'start' }}>
        <div style={{
          width: 160, height: 160, borderRadius: 'var(--r-md)', overflow: 'hidden', flex: 'none',
          background: 'var(--bg-page)', border: '1px solid var(--line-hair)',
        }}>
          {actor.avatar_url ? (
            <img src={actor.avatar_url} alt={actor.name} referrerPolicy="no-referrer"
              style={{ width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'center center' }} />
          ) : (
            <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--t-faint)', fontSize: 48, fontWeight: 600 }}>
              {actor.name[0] || '?'}
            </div>
          )}
        </div>
        <div>
          <h1 style={{ fontFamily: 'var(--ff-serif)', fontSize: 32, color: 'var(--t-display)', margin: '0 0 8px', fontWeight: 700 }}>{actor.name}</h1>
          {actor.name_en && <div style={{ color: 'var(--t-mute)', fontSize: 14, marginBottom: 16 }}>{actor.name_en}</div>}

          {/* 操作栏 */}
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 16 }}>
            <button className={`btn ${subscribed ? 'btn--ghost' : 'btn--gold'}`} onClick={toggleFollow}>
              <Icon.heart />{subscribed ? '已关注' : '关注'}
            </button>
            <button className="btn btn--ghost" onClick={crawlWorks} disabled={!actor.source_url}
              title={actor.source_url ? '爬取该演员全部作品并入库' : '无 JavDB URL（需先通过 URL 添加）'}>
              <Icon.download />补齐作品
            </button>
            <button className={`btn ${autoAdd ? 'btn--gold' : 'btn--ghost'}`} onClick={toggleAutoAdd} disabled={!subscribed}
              title={!subscribed ? '请先关注' : (autoAdd ? '点击关闭自动入库' : '点击开启：有新作自动入库+下载')}>
              自动入库{autoAdd ? ' ✓' : ''}
            </button>
            <button className="btn btn--ghost" onClick={() => nav(`/library?q=${encodeURIComponent(actor.name)}`)}>
              <Icon.library />查看作品库
            </button>
          </div>

          {/* 资料元数据 */}
          {metaVisible.length > 0 && (
            <div className="detail-meta-grid">
              {metaVisible.map(([k, v]) => (
                <div key={k}>
                  <div style={{ fontSize: 10, color: 'var(--t-faint)', textTransform: 'uppercase', letterSpacing: '.05em' }}>{k}</div>
                  <div style={{ fontSize: 14, color: 'var(--t-body)', marginTop: 2 }}>{v}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* 作品列表 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14, gap: 12, flexWrap: 'wrap' }}>
        <div className="dm-label">作品（{total}）</div>
        <div className="seg">
          <button className={sort === 'added' ? 'on' : ''} onClick={() => loadMovies(1, 'added')}>加入日期</button>
          <button className={sort === 'release' ? 'on' : ''} onClick={() => loadMovies(1, 'release')}>发行日期</button>
          <button className={sort === 'rating' ? 'on' : ''} onClick={() => loadMovies(1, 'rating')}>评分</button>
        </div>
      </div>
      {total === 0 ? (
        <Empty icon="○" title="暂无关联作品" sub="点击「补齐作品」爬取该演员的作品列表。" />
      ) : (
        <>
        <div className="gallery">
          {movies.map((m) => {
            const remote = m.poster_url || (() => { try { return JSON.parse(m.thumbnail_urls || '[]')[0] } catch { return null } })()
            return (
              <div key={m.id} className="poster" onClick={() => nav(`/task/${m.id}`)}
                style={{ cursor: 'pointer' }} role="button" tabIndex={0}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); nav(`/task/${m.id}`) } }}>
                <div className="poster-frame">
                  <img src={coverFileUrl(m.id)} alt={m.video_code || ''} loading="lazy" referrerPolicy="no-referrer"
                    style={{ width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'right center' }}
                    onError={(e) => { if (remote && e.currentTarget.src !== remote) e.currentTarget.src = remote; else e.currentTarget.style.opacity = '0.2' }}
                    onLoad={(e) => { e.currentTarget.classList.add('loaded') }} />
                  <div className="poster-grad-top">
                    <span className="poster-code">{m.video_code || '—'}</span>
                  </div>
                  <div className="poster-info">
                    <div className="poster-title">{m.title || '未命名'}</div>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
        {/* 分页 */}
        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 16, marginTop: 24 }}>
          <button className="btn btn--ghost btn--sm" disabled={page <= 1} onClick={() => loadMovies(page - 1, sort)}>上一页</button>
          <span style={{ fontSize: 13, color: 'var(--t-mute)' }}>第 {page} / {Math.max(1, Math.ceil(total / PAGE_SIZE))} 页 · 共 {total} 部</span>
          <button className="btn btn--ghost btn--sm" disabled={page * PAGE_SIZE >= total} onClick={() => loadMovies(page + 1, sort)}>下一页</button>
        </div>
        </>
      )}
    </div>
  )
}
