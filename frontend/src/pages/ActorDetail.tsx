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
  const [inLib, setInLib] = useState<'all' | 'in' | 'out'>('all')
  const [subscribed, setSubscribed] = useState(false)
  const [autoAdd, setAutoAdd] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [batchBusy, setBatchBusy] = useState(false)
  // 人物简介/时间线手动编辑
  const [editing, setEditing] = useState(false)
  const [bioDraft, setBioDraft] = useState('')
  const [timelineDraft, setTimelineDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const toastOk = useStore((s) => s.toastOk)
  const toastErr = useStore((s) => s.toastErr)
  const confirmBox = useStore((s) => s.confirm)

  const loadMovies = useCallback(async (p: number, s: 'added' | 'release' | 'rating', lib: 'all' | 'in' | 'out' = 'all') => {
    if (!id) return
    try {
      const r = await api.actors.movies(+id, p, PAGE_SIZE, s, lib === 'all' ? undefined : lib === 'in')
      setMovies(r.items)
      setTotal(r.total)
      setPage(p)
      setSort(s)
      setInLib(lib)
      setSelected(new Set())
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

  // ── 人物简介/时间线手动编辑 ──
  const saveProfile = async () => {
    if (!actor) return
    setSaving(true)
    try {
      await api.actors.update(actor.id, { bio: bioDraft, timeline: timelineDraft })
      toastOk('简介已保存')
      setEditing(false)
      const a = await api.actors.get(actor.id)
      setActor(a)
    } catch (e) {
      toastErr(String((e as Error).message))
    } finally {
      setSaving(false)
    }
  }

  // ── 作品列表多选批量操作 ──
  const toggleSel = (id: number) => {
    setSelected((prev) => {
      const n = new Set(prev)
      n.has(id) ? n.delete(id) : n.add(id)
      return n
    })
  }
  const allSelected = movies.length > 0 && movies.every((m) => selected.has(m.id))
  const toggleAll = () => {
    setSelected((prev) => {
      const allOnPage = movies.every((m) => prev.has(m.id))
      const n = new Set(prev)
      if (allOnPage) movies.forEach((m) => n.delete(m.id))
      else movies.forEach((m) => n.add(m.id))
      return n
    })
  }
  const batch = async (kind: 'favorite' | 'delete') => {
    const ids = [...selected]
    if (!ids.length) return
    if (kind === 'delete') {
      const ok = await confirmBox('批量删除', `将删除 ${ids.length} 个任务及其关联图片缓存，不可恢复。确定继续？`)
      if (!ok) return
    }
    setBatchBusy(true)
    try {
      if (kind === 'favorite') await api.tasks.batchFavorite(ids)
      else await api.tasks.batchDelete(ids)
      toastOk(`已批量${kind === 'favorite' ? '收藏' : '删除'} ${ids.length} 项`)
      setSelected(new Set())
      loadMovies(page, sort, inLib)
    } catch (e) {
      toastErr(String((e as Error).message))
    } finally {
      setBatchBusy(false)
    }
  }

  if (actor === undefined) return <div className="page"><Loading /></div>
  if (actor === null) return <div className="page"><Empty title="演员不存在" /></div>
  if (error) return <div className="page"><ErrorEmpty message={error} onRetry={() => nav('/actors')} /></div>

  // 资料行（只显示有值的）
  const ageStr = (() => {
    const m = actor.birth_date?.match(/(\d{4})年(\d{1,2})月(\d{1,2})日/)
    if (!m) return null
    const today = new Date()
    let a = today.getFullYear() - +m[1]
    const [mo, d] = [+m[2], +m[3]]
    if (today.getMonth() + 1 < mo || (today.getMonth() + 1 === mo && today.getDate() < d)) a--
    return a >= 0 ? `${a} 岁` : null
  })()
  const meta: [string, string | null][] = [
    ['出生日期', actor.birth_date],
    ['年龄', ageStr],
    ['身高', actor.height],
    ['三围', actor.measurements],
    ['罩杯', actor.cup],
    ['血型', actor.blood_type],
    ['星座', actor.zodiac],
    ['出身地', actor.birthplace],
    ['国籍', actor.nationality],
    ['出道', actor.debut_date],
    ['活跃年限', actor.active_years],
    ['事务所', actor.agency],
    ['别名', actor.alias],
    ['出道作', actor.debut_work],
    ['趣味特技', actor.hobbies],
    ['作品数', actor.movie_count != null ? String(actor.movie_count) : null],
  ]
  const metaVisible = meta.filter(([, v]) => v)

  const refreshProfile = async () => {
    if (!actor) return
    setRefreshing(true)
    try {
      const r = await api.actors.refreshProfile(actor.id)
      if (r.ok) {
        const srcName = r.source === 'minnano' ? 'minnano-av' : r.source === 'warashi' ? 'WAPdB 百科' : r.source === 'laoshi' ? '老师图鉴' : r.source
        toastOk(`资料已更新（来源：${srcName}）`)
        // 重新加载演员数据
        const a = await api.actors.get(actor.id)
        setActor(a)
      } else {
        toastErr(r.message || '三源均未查询到该演员资料')
      }
    } catch (e) { toastErr(String((e as Error).message)) }
    finally { setRefreshing(false) }
  }

  return (
    <div className="page">
      <button className="btn btn--ghost btn--sm" style={{ marginBottom: 20 }}
        onClick={() => { if (window.history.length > 1) nav(-1); else nav('/actors') }}><Icon.back />返回</button>

      {/* 头部：头像 + 信息 */}
      <div style={{ display: 'grid', gridTemplateColumns: '160px 1fr', gap: 28, marginBottom: 32, alignItems: 'start' }}>
        <div className="actor-avatar-zoom" style={{
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
            <button className="btn btn--ghost" onClick={refreshProfile} disabled={refreshing}
              title="整合 minnano-av（个人信息）、WAPdB 百科（别名/简介）、老师图鉴（中文简介）三源抓取资料（自动任务也会定期补齐）">
              <Icon.refresh />{refreshing ? '抓取中…' : '刷新资料'}
            </button>
          </div>

          {/* 资料元数据 */}
          {metaVisible.length > 0 && (
            <div className="detail-meta-grid">
              {metaVisible.map(([k, v]) => (
                <div className="dm-item" key={k}>
                  <div className="dm-label">{k}</div>
                  <div className="dm-val">{v}</div>
                </div>
              ))}
            </div>
          )}

          {/* 标签（minnano タグ）+ 社交链接 */}
          {(actor.tags || actor.twitter || actor.website) && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {actor.tags && (
                <div className="tag-row">
                  {actor.tags.split(',').map((t) => t.trim()).filter(Boolean).map((t) => (
                    <span className="tag" key={t}>{t}</span>
                  ))}
                </div>
              )}
              {(actor.twitter || actor.website) && (
                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                  {actor.twitter && (
                    <a className="btn btn--ghost btn--sm" href={actor.twitter} target="_blank" rel="noreferrer" style={{ textDecoration: 'none' }}>
                      𝕏 Twitter
                    </a>
                  )}
                  {actor.website && (
                    <a className="btn btn--ghost btn--sm" href={actor.website} target="_blank" rel="noreferrer" style={{ textDecoration: 'none' }}>
                      <Icon.link />官方网站
                    </a>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* 人物简介 + 职业时间线（三源聚合内容，支持手动编辑） */}
      <div className="detail-main" style={{ marginBottom: 28 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 10 }}>
          <div className="dm-label">人物简介 · 职业时间线</div>
          {editing ? (
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn btn--gold btn--sm" onClick={saveProfile} disabled={saving}>
                {saving ? '保存中…' : '保存'}
              </button>
              <button className="btn btn--ghost btn--sm" onClick={() => setEditing(false)}>取消</button>
            </div>
          ) : (
            <button className="btn btn--ghost btn--sm" onClick={() => { setBioDraft(actor.bio || ''); setTimelineDraft(actor.timeline || ''); setEditing(true) }}>
              <Icon.edit />编辑
            </button>
          )}
        </div>
        {editing ? (
          <>
            <div className="dm-label" style={{ marginBottom: 6 }}>人物简介</div>
            <textarea className="input" rows={4} value={bioDraft}
              onChange={(e) => setBioDraft(e.target.value)}
              placeholder="演员的生平简介（自动抓取或手动填写）…"
              style={{ marginBottom: 14, resize: 'vertical', fontFamily: 'var(--ff-sans)', lineHeight: 1.7 }} />
            <div className="dm-label" style={{ marginBottom: 6 }}>职业时间线</div>
            <textarea className="input" rows={4} value={timelineDraft}
              onChange={(e) => setTimelineDraft(e.target.value)}
              placeholder="如：2018年 出道，2021年 复归…（每行一条）"
              style={{ resize: 'vertical', fontFamily: 'var(--ff-sans)', lineHeight: 1.7 }} />
          </>
        ) : actor.bio || actor.timeline ? (
          <>
            {actor.bio && (
              <>
                <div className="dm-label" style={{ marginBottom: 8 }}>人物简介</div>
                <p style={{ fontSize: 13, lineHeight: 1.8, color: 'var(--t-body)', marginBottom: 18, whiteSpace: 'pre-wrap' }}>{actor.bio}</p>
              </>
            )}
            {actor.timeline && (
              <>
                <div className="dm-label" style={{ marginBottom: 8 }}>职业时间线</div>
                <div style={{ fontSize: 12, lineHeight: 2, color: 'var(--t-mute)', whiteSpace: 'pre-wrap', fontFamily: 'var(--ff-mono)' }}>
                  {actor.timeline}
                </div>
              </>
            )}
          </>
        ) : (
          <div style={{ fontSize: 13, color: 'var(--t-faint)' }}>
            暂无简介——点右上角「编辑」手动添加，或点「刷新资料」从三源自动抓取。
          </div>
        )}
      </div>

      {/* 作品列表 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14, gap: 12, flexWrap: 'wrap' }}>
        <div className="dm-label">作品（{total}）</div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <div className="seg">
            <button className={sort === 'added' ? 'on' : ''} onClick={() => loadMovies(1, 'added', inLib)}>加入日期</button>
            <button className={sort === 'release' ? 'on' : ''} onClick={() => loadMovies(1, 'release', inLib)}>发行日期</button>
            <button className={sort === 'rating' ? 'on' : ''} onClick={() => loadMovies(1, 'rating', inLib)}>评分</button>
          </div>
          <select className="select" value={inLib} onChange={(e) => loadMovies(1, sort, e.target.value as 'all' | 'in' | 'out')} aria-label="媒体库筛选">
            <option value="all">全部媒体库状态</option>
            <option value="in">✓ 在媒体库</option>
            <option value="out">✗ 不在媒体库</option>
          </select>
          {movies.length > 0 && (
            <button className="btn btn--ghost btn--sm" onClick={toggleAll}>{allSelected ? '取消全选' : '全选本页'}</button>
          )}
        </div>
      </div>
      {total === 0 ? (
        <Empty icon="○" title="暂无关联作品"
          sub={inLib !== 'all' ? '没有匹配的在库状态——若从未同步过 Emby，请到 设置→媒体→立即同步' : '点击「补齐作品」爬取该演员的作品列表。'} />
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
                    <div style={{ display: 'flex', gap: 6, marginLeft: 'auto' }}>
                      {m.media_in_library === true && (
                        <span className="badge-lib" title="已在 Emby 媒体库">库</span>
                      )}
                      <div className={`poster-check${selected.has(m.id) ? ' on' : ''}`} role="checkbox"
                        aria-checked={selected.has(m.id)} tabIndex={0}
                        onClick={(e) => { e.stopPropagation(); toggleSel(m.id) }}
                        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); toggleSel(m.id) } }}>
                        {selected.has(m.id) ? '✓' : ''}
                      </div>
                    </div>
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
        <div className="pagination-card" style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 16, marginTop: 24, padding: '10px 16px', width: 'fit-content', margin: '24px auto 0' }}>
          <button className="btn btn--ghost btn--sm" disabled={page <= 1} onClick={() => loadMovies(page - 1, sort, inLib)}>上一页</button>
          <span style={{ fontSize: 13, color: 'var(--t-mute)' }}>第 {page} / {Math.max(1, Math.ceil(total / PAGE_SIZE))} 页 · 共 {total} 部</span>
          <button className="btn btn--ghost btn--sm" disabled={page * PAGE_SIZE >= total} onClick={() => loadMovies(page + 1, sort, inLib)}>下一页</button>
        </div>
        </>
      )}

      {/* 批量操作栏 */}
      <div className={`batchbar${selected.size ? ' show' : ''}`}>
        <span className="sel-count">已选 {selected.size} 项</span>
        <button className="btn btn--gold btn--sm" onClick={() => batch('favorite')} disabled={batchBusy}>批量收藏</button>
        <button className="btn btn--danger btn--sm" onClick={() => batch('delete')} disabled={batchBusy}>批量删除</button>
        <button className="btn btn--ghost btn--icon" onClick={() => setSelected(new Set())}>✕</button>
      </div>
    </div>
  )
}
