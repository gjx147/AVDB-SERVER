import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { Task } from '../api/types'
import { PosterCard } from '../components/PosterCard'
import { PageHead, Empty, ErrorEmpty } from '../components/States'
import { SkeletonGallery } from '../components/Skeleton'
import { useStore } from '../store/useStore'

interface Collection { id: number; name: string; icon: string; task_count: number }

export function Favorites() {
  const [tasks, setTasks] = useState<Task[] | null>(null)
  const [collections, setCollections] = useState<Collection[]>([])
  const [activeCol, setActiveCol] = useState<number | null>(null)  // null = 全部收藏
  const [error, setError] = useState<string | null>(null)
  const [addingCol, setAddingCol] = useState(false)
  const [newColName, setNewColName] = useState('')
  const [inLib, setInLib] = useState<'all' | 'in' | 'out'>('all')
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [batchBusy, setBatchBusy] = useState(false)
  const toastOk = useStore((s) => s.toastOk)
  const toastErr = useStore((s) => s.toastErr)
  const confirmBox = useStore((s) => s.confirm)

  const load = (lib: 'all' | 'in' | 'out' = inLib) => {
    setTasks(null); setError(null); setSelected(new Set())
    const inLibrary = lib === 'all' ? undefined : lib === 'in'
    if (activeCol !== null) {
      api.collections.tasks(activeCol, inLibrary).then((r) => { setTasks(r.tasks); setError(null) }).catch((e) => { setError(String((e as Error).message)); setTasks([]) })
    } else {
      api.tasks.favorites(0, 100, inLibrary).then(setTasks).catch((e) => { setError(String((e as Error).message)); setTasks([]) })
    // T17: 超限提示（接口无 total 时按满页判断）
    }
    api.collections.list().then((r) => setCollections(r.collections as unknown as Collection[])).catch(() => {})
  }
  useEffect(() => { load() }, [activeCol, inLib])

  const createCol = async () => {
    if (!newColName.trim()) return
    try { await api.collections.create(newColName.trim()); setNewColName(''); setAddingCol(false); toastOk('分组已创建'); load() }
    catch (e) { toastErr(String((e as Error).message)) }
  }
  const delCol = async (id: number) => {
    if (!(await useStore.getState().confirm('删除分组', '删除分组？（不会删除影片本身）'))) return
    try {
      await api.collections.remove(id)
      if (activeCol === id) {
        setActiveCol(null)  // 切到全部，由 useEffect[activeCol] 负责刷新，避免 load() 用旧 activeCol 请求已删除分组
      } else {
        load()  // 删除的不是当前分组，直接刷新列表
      }
      toastOk('已删除')
    }
    catch (e) { toastErr(String((e as Error).message)) }
  }

  // ── 多选批量操作 ──
  const toggleSel = (id: number) => {
    setSelected((prev) => {
      const n = new Set(prev)
      n.has(id) ? n.delete(id) : n.add(id)
      return n
    })
  }
  const allSelected = tasks !== null && tasks.length > 0 && tasks.every((t) => selected.has(t.id))
  const toggleAll = () => {
    if (!tasks) return
    setSelected((prev) => {
      const allOnPage = tasks.every((t) => prev.has(t.id))
      const n = new Set(prev)
      if (allOnPage) tasks.forEach((t) => n.delete(t.id))
      else tasks.forEach((t) => n.add(t.id))
      return n
    })
  }
  const batch = async (kind: 'unfavorite' | 'delete') => {
    const ids = [...selected]
    if (!ids.length) return
    if (kind === 'delete') {
      const ok = await confirmBox('批量删除', `将删除 ${ids.length} 个任务及其关联图片缓存，不可恢复。确定继续？`)
      if (!ok) return
    }
    setBatchBusy(true)
    try {
      if (kind === 'unfavorite') {
        let n = 0
        const B = 5
        for (let i = 0; i < ids.length; i += B) {
          await Promise.all(ids.slice(i, i + B).map(async (id) => {
            try { await api.tasks.unfavorite(id); n++ } catch { /* 单个失败不中断 */ }
          }))
        }
        toastOk(`已取消收藏 ${n} 项`)
      } else {
        await api.tasks.batchDelete(ids)
        toastOk(`已删除 ${ids.length} 项`)
      }
      setSelected(new Set())
      load()
    } catch (e) {
      toastErr(String((e as Error).message))
    } finally {
      setBatchBusy(false)
    }
  }

  return (
    <div className="page">
      <PageHead eyebrow={`Favorites · ${tasks?.length ?? 0} 部`} title={<>收<em>藏</em></>}
        sub="被你点过心的珍藏，随时翻出来温存。">
        <button className="btn btn--ghost btn--sm" onClick={() => setAddingCol(!addingCol)}>＋ 新建分组</button>
      </PageHead>

      {tasks && tasks.length >= 100 && (
        <div style={{ fontSize: 11, color: "var(--t-mute)", margin: "8px 2px" }}>仅显示前 100 条（收藏超过 100 条时请使用影片库筛选）</div>
      )}

      {addingCol && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div style={{ display: 'flex', gap: 10 }}>
            <input className="input" placeholder="分组名称…" value={newColName} onChange={(e) => setNewColName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && createCol()} autoFocus />
            <button className="btn btn--gold" onClick={createCol}>创建</button>
          </div>
        </div>
      )}

      {/* F13: 分组侧栏 */}
      {collections.length > 0 && (
        <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
          <button className={`chip${activeCol === null ? ' chip-green' : ''}`} style={{ cursor: 'pointer', padding: '6px 14px' }}
            onClick={() => setActiveCol(null)}>全部收藏</button>
          {collections.map((c) => (
            <span key={c.id} className={`chip${activeCol === c.id ? ' chip-green' : ''}`}
              style={{ cursor: 'pointer', padding: '6px 14px', display: 'inline-flex', alignItems: 'center', gap: 6 }}
              onClick={() => setActiveCol(c.id)}>
              {c.icon} {c.name} ({c.task_count})
              <span style={{ opacity: .4, marginLeft: 4 }} onClick={(e) => { e.stopPropagation(); delCol(c.id) }}>✕</span>
            </span>
          ))}
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginBottom: 16 }}>
        {tasks && tasks.length > 0 && (
          <button className="btn btn--ghost btn--sm" onClick={toggleAll}>{allSelected ? '取消全选' : '全选本页'}</button>
        )}
        <select className="select" value={inLib}
          onChange={(e) => setInLib(e.target.value as 'all' | 'in' | 'out')} aria-label="媒体库筛选">
          <option value="all">全部媒体库状态</option>
          <option value="in">✓ 在媒体库</option>
          <option value="out">✗ 不在媒体库</option>
        </select>
      </div>

      {error ? <ErrorEmpty message={error} onRetry={load} /> :
       tasks === null ? <SkeletonGallery /> : tasks.length === 0 ? (
        <Empty icon="♡" title={activeCol !== null ? '该分组暂无影片' : '还没有收藏任何影片'}
          sub={activeCol !== null ? '在详情页将影片加入此分组' : '在影片库点击海报卡片上的收藏按钮即可加入。'} />
      ) : (
        <div className="gallery">
          {tasks.map((t) => <PosterCard key={t.id} task={t} selected={selected.has(t.id)} selectable onToggle={() => toggleSel(t.id)} />)}
        </div>
      )}

      {/* 批量操作栏 */}
      <div className={`batchbar${selected.size ? ' show' : ''}`}>
        <span className="sel-count">已选 {selected.size} 项</span>
        <button className="btn btn--gold btn--sm" onClick={() => batch('unfavorite')} disabled={batchBusy}>批量取消收藏</button>
        <button className="btn btn--danger btn--sm" onClick={() => batch('delete')} disabled={batchBusy}>批量删除</button>
        <button className="btn btn--ghost btn--icon" onClick={() => setSelected(new Set())}>✕</button>
      </div>
    </div>
  )
}
