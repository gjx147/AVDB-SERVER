import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { PageHead, Loading, Empty, ErrorEmpty } from '../components/States'
import { Icon } from '../components/Icons'
import { useStore } from '../store/useStore'

interface Subscription {
  id: number
  name: string
  sub_type: string  // actor | ranking | composite
  rank_type: string | null
  actor_id: number | null
  auto_add: boolean
  enabled: boolean
  check_interval_hours: number
  last_checked_at: string | null
  last_result: string | null
}

/** 精简演员档案（合并对话框用） */
interface ActorLite {
  id: number
  name: string
  name_en?: string | null
  avatar_url?: string | null
}

const TYPE_LABEL: Record<string, string> = {
  actor: '演员',
  ranking: '榜单',
  composite: '组合',
}

/** 全部补齐作品后台任务状态 */
interface FillStatus {
  running: boolean
  total: number
  idx: number
  current_actor_id: number | null
  current_name: string | null
  done: number
  skipped: number
  failed: number
  wait_limit_min: number
  last_summary: string | null
}

export function Subscriptions() {
  const nav = useNavigate()
  const [subs, setSubs] = useState<Subscription[] | null>(null)
  const [avatars, setAvatars] = useState<Map<number, string>>(new Map())  // actor_id → avatar_url
  const [filledIds, setFilledIds] = useState<Set<number>>(new Set())  // 已补齐作品的演员 id
  const [error, setError] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)
  // 全部补齐作品：后台任务（后端线程串行执行，切走页面/刷新不中断），前端只轮询进度
  const [fillStatus, setFillStatus] = useState<FillStatus | null>(null)
  const fillPollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [waitLimitMin, setWaitLimitMin] = useState<number>(() => {
    const v = parseInt(localStorage.getItem('subCrawlWaitLimitMin') ?? '', 10)
    return Number.isFinite(v) && v > 0 ? v : 60
  })
  const setWaitLimit = (v: number) => {
    const n = Math.max(1, Math.min(2880, Math.round(v)))
    setWaitLimitMin(n)
    localStorage.setItem('subCrawlWaitLimitMin', String(n))
  }
  // 最大共演人数限制（与演员详情页共用同一配置）
  const [maxCoStar, setMaxCoStar] = useState<number>(() => {
    const v = parseInt(localStorage.getItem('maxCoStarLimit') ?? '', 10)
    return Number.isFinite(v) && v > 0 ? v : 0
  })
  const setMaxCoStarVal = (v: number) => {
    const n = Math.max(0, Math.min(99, Math.round(v)))
    setMaxCoStar(n)
    localStorage.setItem('maxCoStarLimit', String(n))
  }
  // 发行日期下限（YYYY-MM-DD，留空=不过滤；仅本次全部补齐生效）
  const [sinceDate, setSinceDate] = useState('')
  const toastOk = useStore((s) => s.toastOk)
  const toastErr = useStore((s) => s.toastErr)
  // 演员合并对话框
  const [mergeOpen, setMergeOpen] = useState(false)
  const [actorsAll, setActorsAll] = useState<ActorLite[]>([])

  // 后台任务进度轮询：运行中每 3s 拉一次，结束弹总结并刷新
  const startPolling = () => {
    if (fillPollRef.current) clearInterval(fillPollRef.current)
    fillPollRef.current = setInterval(async () => {
      try {
        const s = await api.subscriptions.fillWorksStatus()
        setFillStatus(s)
        if (!s.running) {
          if (fillPollRef.current) clearInterval(fillPollRef.current)
          fillPollRef.current = null
          if (s.last_summary) toastOk(s.last_summary)
          load()
        }
      } catch {
        if (fillPollRef.current) clearInterval(fillPollRef.current)
        fillPollRef.current = null
        setFillStatus(null)
      }
    }, 3000)
  }
  // 挂载/刷新页面时恢复展示进行中的后台任务
  useEffect(() => {
    api.subscriptions.fillWorksStatus().then((s) => {
      if (s?.running) { setFillStatus(s); startPolling() }
    }).catch(() => {})
    return () => { if (fillPollRef.current) clearInterval(fillPollRef.current) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const filling = fillStatus?.running ?? false

  const load = () => {
    setSubs(null); setError(null)
    api.subscriptions.list(undefined).then((r) => {
      setSubs(r || [])
    }).catch((e) => { setError(String((e as Error).message)); setSubs([]) })
    // 演员头像/补齐标记映射：自动翻页拉全量演员，建 actor_id → avatar_url / works_fetched
    api.actors.listAll().then((list) => {
      const m = new Map<number, string>()
      const f = new Set<number>()
      for (const a of list) {
        if (a.avatar_url) m.set(a.id, a.avatar_url)
        if (a.works_fetched) f.add(a.id)
      }
      setAvatars(m)
      setFilledIds(f)
      setActorsAll(list.map((a) => a as ActorLite))
    }).catch(() => {})
  }
  useEffect(() => { load() }, [])

  // N9: 演员状态徽标（最后作品距今，休止/久无新作检测）
  const [statusMap, setStatusMap] = useState<Record<number, { last_release: string; days_since: number | null }>>({})
  useEffect(() => {
    const ids = (subs ?? []).filter((x) => x.sub_type === 'actor' && x.actor_id).map((x) => x.actor_id as number)
    if (ids.length === 0) return
    api.actorStatusSummary(ids.join(',')).then((r) => setStatusMap(r.items)).catch(() => {})
  }, [subs])

  const toggle = async (s: Subscription) => {
    try {
      const r = await api.subscriptions.toggle(s.id)
      setSubs((prev) => prev ? prev.map((x) => x.id === s.id ? { ...x, enabled: r.enabled } : x) : prev)
      toastOk(r.enabled ? '已启用' : '已停用')
    } catch (e) { toastErr(String((e as Error).message)) }
  }
  const remove = async (s: Subscription) => {
    if (!(await useStore.getState().confirm(`删除订阅「${s.name}」`, '删除后不再巡检该订阅，可重新创建。确定删除？'))) return
    try {
      await api.subscriptions.delete(s.id)
      setSubs((prev) => prev ? prev.filter((x) => x.id !== s.id) : prev)
      toastOk('已删除')
    } catch (e) { toastErr(String((e as Error).message)) }
  }

  // 演员合并：把重复档案并入保留者后删除
  const doMerge = async (keepId: number, sourceIds: number[]) => {
    if (!(await useStore.getState().confirm('合并演员',
      `将把 ${sourceIds.length} 个重复档案的作品/订阅并入保留者并删除重复记录，操作不可撤销。确定合并？`))) return
    try {
      const r = await api.actors.merge(keepId, sourceIds)
      const bits = [`迁移 ${r.moved_movies} 部作品`]
      if (r.moved_subs > 0) bits.push('订阅已转移')
      if (r.aliases_added.length > 0) bits.push(`别名记录 ${r.aliases_added.join('、')}`)
      toastOk(`合并完成：${bits.join('，')}`)
      api.actors.invalidateListAllCache()
      setMergeOpen(false)
      load()
    } catch (e) { toastErr(String((e as Error).message)) }
  }

  const checkAll = async () => {
    setChecking(true)
    try {
      const r = await api.newReleases.checkAll()
      const res = r.result || {}
      toastOk(`巡检完成：${res.checked_actors || 0} 位演员，发现 ${res.total_new || 0} 部新作，已推送 ${res.total_pushed || 0} 部`)
    } catch (e) { toastErr(String((e as Error).message)) }
    finally { setChecking(false) }
  }

  // ── 全部补齐作品：触发后端后台任务 + 轮询进度 ──
  const fillAllWorks = async () => {
    if (filling) return
    try {
      await api.subscriptions.fillAllWorks(waitLimitMin, maxCoStar, sinceDate)
      toastOk(maxCoStar > 0 ? `已启动全部补齐作品（最大共演 ${maxCoStar} 人，后台串行执行）` : '已启动全部补齐作品（后台串行执行，切走页面不中断）')
      setFillStatus({ running: true, total: 0, idx: 0, current_actor_id: null, current_name: null, done: 0, skipped: 0, failed: 0, wait_limit_min: waitLimitMin, last_summary: null })
      startPolling()
    } catch (e) {
      toastErr(String((e as Error).message))
    }
  }

  const fmtTime = (t: string | null) => {
    if (!t) return '从未检查'
    try {
      const d = new Date(t)
      return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
    } catch { return t }
  }

  return (
    <div className="page">
      <PageHead eyebrow={`Subscriptions · ${subs?.length ?? 0} 条`} title={<>订<em>阅</em></>}
        sub="订阅演员，有新作自动通知/下载。巡检时与 Emby 媒体库比对，避免重复入库。">
        <button className="btn btn--ghost btn--sm" onClick={() => setMergeOpen(true)}
          title="同一演员被建了多个档案时，把重复档案并入保留者：作品、订阅、头像、备注迁移，重复记录删除">
          合并演员
        </button>
        <button className="btn btn--ghost btn--sm" onClick={checkAll} disabled={checking || filling}>
          <Icon.refresh />{checking ? '巡检中…' : '立即巡检全部'}
        </button>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--t-mute)', whiteSpace: 'nowrap', cursor: 'pointer' }}
          title="每位演员补齐作品的等待上限，超时跳过继续下一位（仅保存在本机浏览器）">
          每演员等待上限
          <input className="input" type="number" min={1} max={2880} value={waitLimitMin}
            style={{ width: 62, padding: '5px 8px', textAlign: 'center' }}
            onChange={(e) => setWaitLimit(+e.target.value)}
            onBlur={(e) => { if (!e.target.value) setWaitLimit(60) }} />
          分钟
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--t-mute)', whiteSpace: 'nowrap', cursor: 'pointer' }}
          title="最大共演人数：作品女演员数超过此值则跳过，0=不限（与演员详情页共用配置）">
          最大共演
          <input className="input" type="number" min={0} max={99} value={maxCoStar}
            style={{ width: 48, padding: '5px 8px', textAlign: 'center' }}
            onChange={(e) => setMaxCoStarVal(+e.target.value)}
            onBlur={(e) => { if (!e.target.value) setMaxCoStarVal(0) }} />
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--t-mute)', whiteSpace: 'nowrap', cursor: 'pointer' }}
          title="发行日期下限：只补齐该日期（含）之后发行的作品，早于该日期的跳过；留空=不过滤。仅本次全部补齐生效。">
          日期下限
          <input className="input" type="date" value={sinceDate}
            onChange={(e) => setSinceDate(e.target.value)}
            style={{ width: 128, padding: '5px 8px' }} />
        </label>
        <button className="btn btn--gold btn--sm" onClick={fillAllWorks} disabled={filling}
          title="后台逐位为未补齐的订阅演员补齐作品（已补齐标记的演员自动跳过；串行执行，切走页面/刷新不中断）">
          <Icon.download />{filling
            ? `补齐中 · ${fillStatus?.current_name || '准备中'} (${fillStatus?.idx || 0}/${fillStatus?.total || '…'})…`
            : '全部补齐作品'}
        </button>
      </PageHead>

      {/* 订阅列表：卡片式（演员订阅显示头像） */}
      {error ? <ErrorEmpty message={error} onRetry={load} /> :
       subs === null ? <Loading /> : subs.length === 0 ? (
        <Empty icon="◌" title="暂无订阅" sub="前往演员库，点击演员卡片的「订阅」按钮即可添加。" />
      ) : (
        <div className="sub-grid">
          {subs.map((s) => {
            const clickable = s.sub_type === 'actor' && s.actor_id
            const avatar = s.actor_id != null ? avatars.get(s.actor_id) : undefined
            return (
            <div key={s.id} className={`sub-card${s.enabled ? '' : ' off'}`}
              onClick={() => clickable && nav(`/actor/${s.actor_id}`)}
              role={clickable ? 'button' : undefined}
              tabIndex={clickable ? 0 : undefined}
              aria-label={clickable ? `查看演员 ${s.name}` : s.name}
              onKeyDown={clickable ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); nav(`/actor/${s.actor_id}`) } } : undefined}>
              <div className="sub-photo">
                {avatar ? (
                  <img src={avatar} alt={s.name} referrerPolicy="no-referrer" loading="lazy"
                    onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none' }} />
                ) : null}
                <div className="sub-ph">{avatar ? '' : TYPE_LABEL[s.sub_type]?.[0] || '?'}</div>
              </div>
              <div className="sub-body">
                <div className="sub-name">{s.name}</div>
                <div className="sub-meta">
                  <span className="chip chip-rose">{TYPE_LABEL[s.sub_type] || s.sub_type}</span>
                  {s.auto_add && <span className="chip chip-amber">自动下载</span>}
                  {s.sub_type === 'actor' && s.actor_id && statusMap[s.actor_id]?.days_since != null && (
                    statusMap[s.actor_id].days_since! > 180
                      ? <span className="chip chip-red" style={{ fontSize: 10 }}>休止?</span>
                      : statusMap[s.actor_id].days_since! > 90
                        ? <span className="chip chip-amber" style={{ fontSize: 10 }}>久无新作</span>
                        : null
                  )}
                  {s.actor_id != null && filledIds.has(s.actor_id) && <span className="chip chip-green">已补齐</span>}
                  {filling && s.actor_id != null && fillStatus?.current_actor_id === s.actor_id && <span className="chip chip-blue">补齐中…</span>}
                </div>
                <div className="sub-check">每 {s.check_interval_hours}h 检查 · {fmtTime(s.last_checked_at)}</div>
                <div className="sub-actions">
                  <button
                    onClick={(e) => { e.stopPropagation(); toggle(s) }}
                    className={`btn btn--sm ${s.enabled ? 'btn--ghost' : 'btn--gold'}`}
                    style={{ fontSize: 11, flex: 1 }}>{s.enabled ? '停用' : '启用'}</button>
                  <button
                    onClick={(e) => { e.stopPropagation(); remove(s) }}
                    className="btn btn--sm btn--ghost"
                    style={{ fontSize: 11, color: 'var(--red)' }}>删除</button>
                </div>
              </div>
            </div>
            )
          })}
        </div>
      )}
      {mergeOpen && subs && (
        <ActorMergeDialog actors={actorsAll} subs={subs} onClose={() => setMergeOpen(false)} onDone={doMerge} />
      )}
    </div>
  )
}

/** 合并演员对话框：保留一个主档案，把重复档案并入后删除（结构参照 ShareCardModal 挂载 + cd-card 样式） */
function ActorMergeDialog({ actors, subs, onClose, onDone }: {
  actors: ActorLite[]
  subs: Subscription[]
  onClose: () => void
  onDone: (keepId: number, sourceIds: number[]) => void
}) {
  const [keepId, setKeepId] = useState<number | null>(null)
  const [picked, setPicked] = useState<Set<number>>(new Set())
  const [q, setQ] = useState('')
  const subscribedIds = new Set(subs.filter((x) => x.sub_type === 'actor' && x.actor_id).map((x) => x.actor_id as number))
  const sorted = [...actors].sort((a, b) => {
    const sa = subscribedIds.has(a.id) ? 0 : 1
    const sb = subscribedIds.has(b.id) ? 0 : 1
    return sa - sb || a.name.localeCompare(b.name, 'zh-Hans-CN')
  })
  const kw = q.trim().toLowerCase()
  // 默认只列已订阅（最常见场景：同一人订阅了两个名字）；输入关键字后筛全部档案（含未订阅）
  const match = (a: ActorLite) => kw
    ? a.name.toLowerCase().includes(kw) || (a.name_en || '').toLowerCase().includes(kw)
    : subscribedIds.has(a.id)
  const candidates = sorted.filter((a) => a.id !== keepId && match(a))
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="cd-overlay" onClick={onClose}>
      <div className="cd-card" role="dialog" aria-modal="true" aria-label="合并演员" onClick={(e) => e.stopPropagation()}>
        <div className="cd-title">合并演员</div>
        <div className="cd-message">同一演员被建了多个档案时，把重复档案并入保留者：作品关联、订阅、头像、备注会迁移过去，重复记录删除，不可撤销。</div>
        <div className="field">
          <label>保留谁</label>
          <select className="select" value={keepId ?? ''} onChange={(e) => { setKeepId(+e.target.value); setPicked(new Set()) }}>
            <option value="" disabled>选择保留的档案…</option>
            {sorted.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}{a.name_en ? ` (${a.name_en})` : ''}{subscribedIds.has(a.id) ? ' · 已订阅' : ''}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label>合并谁（可多选，不含保留者）</label>
          <input className="input" style={{ marginBottom: 6 }} placeholder="输入名字筛选重复档案…"
            value={q} onChange={(e) => setQ(e.target.value)} />
          <div style={{ maxHeight: 180, overflowY: 'auto', border: '1px solid var(--line, #ddd)', borderRadius: 8, padding: '4px 10px' }}>
            {candidates.length === 0 ? (
              <div style={{ fontSize: 12, color: 'var(--t-mute)', padding: '8px 0' }}>
                无匹配项{!kw ? '——默认只列已订阅档案，输入关键字可筛选全部演员' : ''}
              </div>
            ) : candidates.map((a) => (
              <label key={a.id} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, padding: '5px 0', cursor: 'pointer' }}>
                <input type="checkbox" checked={picked.has(a.id)}
                  onChange={() => setPicked((prev) => { const n = new Set(prev); if (n.has(a.id)) n.delete(a.id); else n.add(a.id); return n })} />
                {a.avatar_url ? (
                  <img src={a.avatar_url} alt="" width={24} height={32} style={{ borderRadius: 4, objectFit: 'cover' }}
                    referrerPolicy="no-referrer" onError={(e) => { e.currentTarget.style.display = 'none' }} />
                ) : null}
                <span>{a.name}{a.name_en ? ` (${a.name_en})` : ''}</span>
                <span className={`chip ${subscribedIds.has(a.id) ? 'chip-green' : ''}`} style={{ fontSize: 10 }}>
                  {subscribedIds.has(a.id) ? '已订阅' : '未订阅'}
                </span>
              </label>
            ))}
          </div>
        </div>
        <div className="cd-actions">
          <button className="btn btn--ghost btn--sm" onClick={onClose}>取消</button>
          <button className="btn btn--gold btn--sm" disabled={!keepId || picked.size === 0}
            onClick={() => keepId && onDone(keepId, [...picked])}>合并 {picked.size} 个档案</button>
        </div>
      </div>
    </div>
  )
}
