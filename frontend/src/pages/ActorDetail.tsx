import { useCallback, useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api, coverFileUrl } from '../api/client'
import type { Actor, ActorMovie } from '../api/types'
import { PageHead, Loading, Empty, ErrorEmpty } from '../components/States'
import { Icon } from '../components/Icons'
import { useStore } from '../store/useStore'

const PAGE_SIZE = 30

/** 基本资料可手动编辑字段（演员详情页胶囊网格，键对应 Actor 字段） */
const EDIT_FIELDS: { key: string; label: string }[] = [
  { key: 'birth_date', label: '出生日期' },
  { key: 'height', label: '身高' },
  { key: 'measurements', label: '三围' },
  { key: 'cup', label: '罩杯' },
  { key: 'blood_type', label: '血型' },
  { key: 'zodiac', label: '星座' },
  { key: 'birthplace', label: '出身地' },
  { key: 'nationality', label: '国籍' },
  { key: 'debut_date', label: '出道' },
  { key: 'active_years', label: '活跃年限' },
  { key: 'agency', label: '事务所' },
  { key: 'alias', label: '别名' },
  { key: 'debut_work', label: '出道作' },
  { key: 'hobbies', label: '趣味特技' },
  { key: 'twitter', label: 'Twitter' },
  { key: 'website', label: '官网' },
  { key: 'tags', label: '标签' },
]

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
  // 简介（intro）与职业生涯（bio/timeline）手动编辑
  const [editingIntro, setEditingIntro] = useState(false)
  const [introDraft, setIntroDraft] = useState('')
  const [editingCareer, setEditingCareer] = useState(false)
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
      await api.actors.crawlWorks(actor.id, maxCoStar)
      toastOk(maxCoStar > 0 ? `已开始补齐 ${actor.name} 的作品（最大共演 ${maxCoStar} 人）` : `已开始补齐 ${actor.name} 的作品`)
    } catch (e) { toastErr(String((e as Error).message)) }
  }
  const crawlSoloWorks = async () => {
    if (!actor) return
    if (!actor.source_url) { toastErr('该演员无 JavDB URL，需先通过 URL 添加'); return }
    try {
      await api.actors.crawlWorks(actor.id, maxCoStar, true)
      toastOk(`已开始补齐 ${actor.name} 的单体作品（t=s 过滤）`)
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

  // ── 简介/职业生涯手动编辑 ──
  const saveIntro = async () => {
    if (!actor) return
    setSaving(true)
    try {
      await api.actors.update(actor.id, { intro: introDraft })
      toastOk('简介已保存')
      setEditingIntro(false)
      setActor(await api.actors.get(actor.id))
    } catch (e) {
      toastErr(String((e as Error).message))
    } finally {
      setSaving(false)
    }
  }
  const saveCareer = async () => {
    if (!actor) return
    setSaving(true)
    try {
      await api.actors.update(actor.id, { bio: bioDraft, timeline: timelineDraft })
      toastOk('职业生涯已保存')
      setEditingCareer(false)
      setActor(await api.actors.get(actor.id))
    } catch (e) {
      toastErr(String((e as Error).message))
    } finally {
      setSaving(false)
    }
  }
  // 锁定保护：锁定后「刷新资料」与自动任务不覆盖简介/职业生涯文本
  const toggleLock = async () => {
    if (!actor) return
    const next = !actor.profile_locked
    try {
      await api.actors.update(actor.id, { profile_locked: next })
      setActor(await api.actors.get(actor.id))
      toastOk(next ? '已锁定——刷新资料/自动任务不再覆盖任何资料字段' : '已解锁——刷新资料会更新全部资料')
    } catch (e) {
      toastErr(String((e as Error).message))
    }
  }

  // ── 头像手动更换（laoshi / minnano-av / JavDB 三选一）──
  const [avPanelOpen, setAvPanelOpen] = useState(false)
  const [avOptsLoading, setAvOptsLoading] = useState(false)
  const [avatarOpts, setAvatarOpts] = useState<{ current: string | null; options: { key: string; label: string; url: string }[] } | null>(null)
  // ── 基本资料编辑（胶囊网格整块切换为输入框）──
  const [metaEditing, setMetaEditing] = useState(false)
  const [metaDraft, setMetaDraft] = useState<Record<string, string>>({})
  const [metaSaving, setMetaSaving] = useState(false)
  // 最大共演人数限制（补齐作品时作品女演员数超过则跳过；0=不限）
  const [maxCoStar, setMaxCoStar] = useState<number>(() => {
    const v = parseInt(localStorage.getItem('maxCoStarLimit') ?? '', 10)
    return Number.isFinite(v) && v > 0 ? v : 0
  })
  const setMaxCoStarVal = (v: number) => {
    const n = Math.max(0, Math.min(99, Math.round(v)))
    setMaxCoStar(n)
    localStorage.setItem('maxCoStarLimit', String(n))
  }
  const openAvatarPanel = () => {
    if (!actor) return
    if (avPanelOpen) { setAvPanelOpen(false); return }
    setAvPanelOpen(true)
    if (avatarOpts === null && !avOptsLoading) {
      setAvOptsLoading(true)
      api.actors.avatarOptions(actor.id).then((d) => setAvatarOpts(d))
        .catch((e) => toastErr(String((e as Error).message)))
        .finally(() => setAvOptsLoading(false))
    }
  }
  const setAvatar = async (url: string) => {
    if (!actor) return
    try {
      await api.actors.update(actor.id, { avatar_url: url })
      toastOk('头像已更换')
      setAvPanelOpen(false)
      setAvatarOpts(null)
      setActor(await api.actors.get(actor.id))
    } catch (e) {
      toastErr(String((e as Error).message))
    }
  }

  // ── 基本资料编辑：打开时把当前值填入草稿（空字段留空可添加）──
  const openMetaEdit = () => {
    if (!actor) return
    const d: Record<string, string> = {}
    for (const f of EDIT_FIELDS) d[f.key] = ((actor as unknown as Record<string, unknown>)[f.key] as string) || ''
    setMetaDraft(d)
    setMetaEditing(true)
  }
  const saveMeta = async () => {
    if (!actor) return
    setMetaSaving(true)
    try {
      await api.actors.update(actor.id, { ...metaDraft })
      toastOk('资料已保存')
      setMetaEditing(false)
      setActor(await api.actors.get(actor.id))
    } catch (e) {
      toastErr(String((e as Error).message))
    } finally {
      setMetaSaving(false)
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
        toastOk(r.locked_skipped?.length
          ? `资料已更新（来源：${srcName}；已锁定，本次未覆盖任何字段）`
          : `资料已更新（来源：${srcName}）`)
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
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div className="actor-avatar-zoom" style={{
            width: 160, height: 160, borderRadius: 'var(--r-md)', overflow: 'hidden', flex: 'none',
            background: 'var(--bg-page)', border: '1px solid var(--line-hair)',
          }}>
            {actor.avatar_url ? (
              <img src={actor.avatar_url} alt={actor.name} referrerPolicy="no-referrer"
                onError={(e) => { e.currentTarget.style.display = 'none' }}
                style={{ width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'center center' }} />
            ) : (
              <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--t-faint)', fontSize: 48, fontWeight: 600 }}>
                {actor.name[0] || '?'}
              </div>
            )}
          </div>
          <button className="btn btn--ghost btn--sm" onClick={openAvatarPanel}
            title="在老师图鉴（高清）/ minnano-av / JavDB 三个来源中选择头像">
            更换头像
          </button>
          {avPanelOpen && (
            <div className="card" style={{ padding: 12 }}>
              {avOptsLoading ? (
                <div style={{ fontSize: 12, color: 'var(--t-faint)', padding: 6 }}>正在获取候选头像…</div>
              ) : avatarOpts && avatarOpts.options.length === 0 ? (
                <div style={{ fontSize: 12, color: 'var(--t-faint)', padding: 6 }}>三源均无可用头像</div>
              ) : (
                (avatarOpts?.options || []).map((o) => {
                  const isCur = avatarOpts?.current === o.url
                  return (
                    <div key={o.key} onClick={() => setAvatar(o.url)}
                      style={{
                        display: 'flex', gap: 10, alignItems: 'center', cursor: 'pointer', padding: '7px 8px',
                        borderRadius: 8, background: isCur ? 'var(--gold-wash)' : 'transparent',
                        transition: 'background .2s',
                      }}
                      title={`使用 ${o.label} 头像`}>
                      <img src={o.url} alt={o.label} referrerPolicy="no-referrer"
                        onError={(e) => { e.currentTarget.style.visibility = 'hidden' }}
                        style={{ width: 44, height: 44, borderRadius: '50%', objectFit: 'cover', background: 'var(--bg-page)', flex: 'none' }} />
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 12, color: 'var(--t-body)' }}>{o.label}</div>
                        <div style={{ fontSize: 10, color: isCur ? 'var(--gold)' : 'var(--t-faint)' }}>
                          {isCur ? '✓ 当前头像' : '点击更换'}
                        </div>
                      </div>
                    </div>
                  )
                })
              )}
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
            <button className="btn btn--ghost" onClick={crawlSoloWorks} disabled={!actor.source_url}
              title={actor.source_url ? '只爬取单体作品（javdb 演员页 t=s 过滤，?sort_type=0&t=s）' : '无 JavDB URL（需先通过 URL 添加）'}>
              <Icon.download />补齐单体作品
            </button>
            <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, color: 'var(--t-mute)', whiteSpace: 'nowrap', cursor: 'pointer' }}
              title="最大共演人数：作品女演员数超过此值则跳过，0=不限（共演人数=1部作品的女演员数量，仅保存在本机浏览器）">
              最大共演
              <input className="input" type="number" min={0} max={99} value={maxCoStar}
                onChange={(e) => setMaxCoStarVal(+e.target.value)}
                onBlur={(e) => { if (!e.target.value) setMaxCoStarVal(0) }}
                style={{ width: 48, padding: '5px 6px', textAlign: 'center' }} />
            </label>
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
            <button className={`btn btn--sm ${actor.profile_locked ? 'btn--gold' : 'btn--ghost'}`} onClick={toggleLock}
              title={actor.profile_locked ? '已锁定：刷新资料/自动任务不会覆盖任何资料字段。点击解锁' : '点击锁定：防止刷新资料/自动任务覆盖手动编辑的资料（身高/罩杯/简介等全部字段）'}>
              {actor.profile_locked ? '🔒 已锁定' : '🔓 锁定保护'}
            </button>
            {metaEditing ? (
              <>
                <button className="btn btn--gold" onClick={saveMeta} disabled={metaSaving}>
                  {metaSaving ? '保存中…' : '保存资料'}
                </button>
                <button className="btn btn--ghost" onClick={() => setMetaEditing(false)}>取消</button>
              </>
            ) : (
              <button className="btn btn--ghost" onClick={openMetaEdit}><Icon.edit />编辑资料</button>
            )}
          </div>

          {/* 资料元数据（编辑模式：全部字段变为输入框，可修改/添加） */}
          {metaEditing ? (
            <div className="detail-meta-grid" style={{ marginBottom: 16 }}>
              {EDIT_FIELDS.map((f) => (
                <div className="dm-item" key={f.key}>
                  <div className="dm-label">{f.label}</div>
                  <input
                    value={metaDraft[f.key] || ''}
                    onChange={(e) => setMetaDraft((p) => ({ ...p, [f.key]: e.target.value }))}
                    placeholder="未填写"
                    style={{
                      width: '100%', background: 'transparent', border: 'none', outline: 'none',
                      color: 'var(--t-display)', fontSize: 13, fontWeight: 500, fontFamily: 'var(--ff-sans)',
                      borderBottom: '1px dashed var(--line-soft)', padding: '2px 0',
                    }} />
                </div>
              ))}
            </div>
          ) : (
            metaVisible.length > 0 && (
              <div className="detail-meta-grid">
                {metaVisible.map(([k, v]) => (
                  <div className="dm-item" key={k}>
                    <div className="dm-label">{k}</div>
                    <div className="dm-val">{v}</div>
                  </div>
                ))}
              </div>
            )
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

      {/* 简介（手动编辑，自由文字，不被自动抓取覆盖） */}
      <div className="detail-main" style={{ marginBottom: 28 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 10 }}>
          <div className="dm-label">简介</div>
          {editingIntro ? (
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn btn--gold btn--sm" onClick={saveIntro} disabled={saving}>
                {saving ? '保存中…' : '保存'}
              </button>
              <button className="btn btn--ghost btn--sm" onClick={() => setEditingIntro(false)}>取消</button>
            </div>
          ) : (
            <button className="btn btn--ghost btn--sm" onClick={() => { setIntroDraft(actor.intro || ''); setEditingIntro(true) }}>
              <Icon.edit />编辑
            </button>
          )}
        </div>
        {editingIntro ? (
          <textarea className="input" rows={5} value={introDraft}
            onChange={(e) => setIntroDraft(e.target.value)}
            placeholder="用一段文字介绍这位演员…（自由编辑，不会被自动抓取覆盖）"
            style={{ resize: 'vertical', fontFamily: 'var(--ff-sans)', lineHeight: 1.8 }} />
        ) : actor.intro ? (
          <p style={{ fontSize: 13, lineHeight: 1.9, color: 'var(--t-body)', whiteSpace: 'pre-wrap' }}>{actor.intro}</p>
        ) : (
          <div style={{ fontSize: 13, color: 'var(--t-faint)' }}>
            暂无简介——点右上角「编辑」手动添加。
          </div>
        )}
      </div>

      {/* 职业生涯 + 职业时间线（三源聚合内容，支持手动编辑与锁定） */}
      <div className="detail-main" style={{ marginBottom: 28 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 10 }}>
          <div className="dm-label">职业生涯 · 职业时间线</div>
          {editingCareer ? (
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn btn--gold btn--sm" onClick={saveCareer} disabled={saving}>
                {saving ? '保存中…' : '保存'}
              </button>
              <button className="btn btn--ghost btn--sm" onClick={() => setEditingCareer(false)}>取消</button>
            </div>
          ) : (
            <button className="btn btn--ghost btn--sm" onClick={() => { setBioDraft(actor.bio || ''); setTimelineDraft(actor.timeline || ''); setEditingCareer(true) }}>
              <Icon.edit />编辑
            </button>
          )}
        </div>
        {editingCareer ? (
          <>
            <div className="dm-label" style={{ marginBottom: 6 }}>职业生涯</div>
            <textarea className="input" rows={4} value={bioDraft}
              onChange={(e) => setBioDraft(e.target.value)}
              placeholder="演员的职业生涯（自动抓取或手动填写）…"
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
                <div className="dm-label" style={{ marginBottom: 8 }}>职业生涯</div>
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
            暂无职业生涯资料——点右上角「编辑」手动添加，或点「刷新资料」从三源自动抓取。
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
