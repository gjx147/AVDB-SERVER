import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { PageHead, Loading } from '../components/States'
import { useStore } from '../store/useStore'

interface RuleItem { id: number; name: string; conditions_json: string; actions_json: string; enabled: boolean; hit_count: number; last_run_at: string | null }

export function Rules() {
  const [items, setItems] = useState<RuleItem[] | null>(null)
  const [name, setName] = useState('')
  const [actor, setActor] = useState('')
  const [tag, setTag] = useState('')
  const [maker, setMaker] = useState('')
  const [ratingMin, setRatingMin] = useState('')
  const [isNew, setIsNew] = useState(true)
  const [acts, setActs] = useState<Record<string, boolean>>({ notify: true, favorite: false, push: false })
  const [note, setNote] = useState('')
  const toastOk = useStore((s) => s.toastOk)
  const toastErr = useStore((s) => s.toastErr)

  const load = () => {
    api.rules.list().then((r) => setItems(r.items)).catch(() => setItems([]))
  }
  useEffect(() => { load() }, [])

  const create = async () => {
    if (!name.trim()) { toastErr('规则名必填'); return }
    const conditions: Record<string, unknown> = { is_new: isNew }
    if (actor.trim()) conditions.actor = actor.split(',').map((x) => x.trim()).filter(Boolean)
    if (tag.trim()) conditions.tag = tag.split(',').map((x) => x.trim()).filter(Boolean)
    if (maker.trim()) conditions.maker = maker.split(',').map((x) => x.trim()).filter(Boolean)
    const rm = parseFloat(ratingMin)
    if (!isNaN(rm)) conditions.rating_min = rm
    const actions: Record<string, unknown> = { actions: Object.keys(acts).filter((k) => acts[k]) }
    if (note.trim()) actions.note = note.trim()
    try {
      await api.rules.create({ name, conditions, actions })
      toastOk('规则已创建')
      setName(''); setActor(''); setTag(''); setMaker(''); setRatingMin(''); setNote('')
      load()
    } catch (e) { toastErr(String((e as Error).message)) }
  }

  const toggle = async (r: RuleItem) => {
    try { await api.rules.update(r.id, { enabled: !r.enabled }); load() }
    catch (e) { toastErr(String((e as Error).message)) }
  }
  const remove = async (r: RuleItem) => {
    if (!(await useStore.getState().confirm('删除规则', `确定删除规则「${r.name}」？`))) return
    try { await api.rules.remove(r.id); load() } catch (e) { toastErr(String((e as Error).message)) }
  }
  const runNow = async () => {
    try { const r = await api.rules.runNow(); toastOk(`规则求值完成：${r.hits} 次命中`) }
    catch (e) { toastErr(String((e as Error).message)) }
  }

  return (
    <div className="page">
      <PageHead eyebrow="Automation" title={<>自动化<em>规则</em></>}
        sub="IF-THEN：条件命中新入库作品后自动执行动作（每小时求值）。">
        <button className="btn btn--gold btn--sm" onClick={runNow}>立即求值</button>
      </PageHead>

      <div className="card" style={{ marginBottom: 16, padding: '14px 16px' }}>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>新建规则</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 8 }}>
          <div className="field" style={{ margin: 0 }}><label>规则名</label>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="如：高分新作自动收藏" /></div>
          <div className="field" style={{ margin: 0 }}><label>演员（逗号分隔）</label>
            <input className="input" value={actor} onChange={(e) => setActor(e.target.value)} placeholder="可选" /></div>
          <div className="field" style={{ margin: 0 }}><label>标签</label>
            <input className="input" value={tag} onChange={(e) => setTag(e.target.value)} placeholder="可选" /></div>
          <div className="field" style={{ margin: 0 }}><label>厂牌</label>
            <input className="input" value={maker} onChange={(e) => setMaker(e.target.value)} placeholder="可选" /></div>
          <div className="field" style={{ margin: 0 }}><label>最低评分</label>
            <input className="input" value={ratingMin} onChange={(e) => setRatingMin(e.target.value)} placeholder="如 8" /></div>
          <div className="field" style={{ margin: 0 }}><label>动作</label>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', height: 34 }}>
              {(['notify', 'favorite', 'push'] as const).map((a) => (
                <label key={a} style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 4 }}>
                  <input type="checkbox" checked={!!acts[a]} onChange={(e) => setActs((p) => ({ ...p, [a]: e.target.checked }))} />{a === 'notify' ? '通知' : a === 'favorite' ? '收藏' : '推送'}
                </label>
              ))}
            </div></div>
          <div className="field" style={{ margin: 0 }}><label>仅新入库（2 小时内）</label>
            <select className="select" value={isNew ? '1' : '0'} onChange={(e) => setIsNew(e.target.value === '1')}>
              <option value="1">是</option><option value="0">否（全部）</option>
            </select></div>
          <div className="field" style={{ margin: 0 }}><label>通知文案（可选）</label>
            <input className="input" value={note} onChange={(e) => setNote(e.target.value)} /></div>
        </div>
        <button className="btn btn--gold btn--sm" onClick={create} style={{ marginTop: 10 }}>创建规则</button>
      </div>

      <div className="card">
        {items === null ? <Loading /> : items.length === 0 ? (
          <div style={{ fontSize: 12, color: 'var(--t-mute)', textAlign: 'center', padding: 20 }}>暂无规则</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {items.map((r) => {
              let condText = r.conditions_json
              try { condText = JSON.stringify(JSON.parse(r.conditions_json)) } catch { /* 保持原样 */ }
              let actText = r.actions_json
              try { actText = JSON.stringify(JSON.parse(r.actions_json)) } catch { /* 保持原样 */ }
              return (
                <div key={r.id} style={{ display: 'flex', gap: 10, alignItems: 'center', border: '1px solid var(--line, #eee)', borderRadius: 8, padding: '8px 10px', fontSize: 12 }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600 }}>{r.name} <span style={{ fontSize: 10, color: 'var(--t-mute)' }}>命中 {r.hit_count} 次</span></div>
                    <div style={{ color: 'var(--t-mute)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{condText} → {actText}</div>
                  </div>
                  <button className="btn btn--ghost btn--sm" onClick={() => toggle(r)}>{r.enabled ? '停用' : '启用'}</button>
                  <button className="btn btn--danger btn--sm" onClick={() => remove(r)}>删除</button>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
