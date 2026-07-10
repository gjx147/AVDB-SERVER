import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { Task } from '../api/types'
import { PosterCard } from '../components/PosterCard'
import { PageHead, Loading, Empty, ErrorEmpty } from '../components/States'
import { useStore } from '../store/useStore'

interface Collection { id: number; name: string; icon: string; task_count: number }

export function Favorites() {
  const [tasks, setTasks] = useState<Task[] | null>(null)
  const [collections, setCollections] = useState<Collection[]>([])
  const [activeCol, setActiveCol] = useState<number | null>(null)  // null = 全部收藏
  const [error, setError] = useState<string | null>(null)
  const [addingCol, setAddingCol] = useState(false)
  const [newColName, setNewColName] = useState('')
  const toastOk = useStore((s) => s.toastOk)
  const toastErr = useStore((s) => s.toastErr)

  const load = () => {
    setTasks(null); setError(null)
    if (activeCol !== null) {
      api.collections.tasks(activeCol).then((r) => { setTasks(r.tasks); setError(null) }).catch((e) => { setError(String((e as Error).message)); setTasks([]) })
    } else {
      api.tasks.favorites(0, 100).then(setTasks).catch((e) => { setError(String((e as Error).message)); setTasks([]) })
    }
    api.collections.list().then((r) => setCollections(r.collections as unknown as Collection[])).catch(() => {})
  }
  useEffect(() => { load() }, [activeCol])

  const createCol = async () => {
    if (!newColName.trim()) return
    try { await api.collections.create(newColName.trim()); setNewColName(''); setAddingCol(false); toastOk('分组已创建'); load() }
    catch (e) { toastErr(String((e as Error).message)) }
  }
  const delCol = async (id: number) => {
    if (!confirm('删除分组？（不会删除影片本身）')) return
    try { await api.collections.remove(id); if (activeCol === id) setActiveCol(null); toastOk('已删除'); load() }
    catch (e) { toastErr(String((e as Error).message)) }
  }

  return (
    <div className="page">
      <PageHead eyebrow={`Favorites · ${tasks?.length ?? 0} 部`} title={<>收<em>藏</em></>}
        sub="你标记为收藏的作品。可创建分组分类管理。">
        <button className="btn btn--ghost btn--sm" onClick={() => setAddingCol(!addingCol)}>＋ 新建分组</button>
      </PageHead>

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
        <div style={{ display: 'flex', gap: 8, marginBottom: 20, flexWrap: 'wrap' }}>
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

      {error ? <ErrorEmpty message={error} onRetry={load} /> :
       tasks === null ? <Loading /> : tasks.length === 0 ? (
        <Empty icon="♡" title={activeCol !== null ? '该分组暂无影片' : '还没有收藏任何影片'}
          sub={activeCol !== null ? '在详情页将影片加入此分组' : '在影片库点击海报卡片上的收藏按钮即可加入。'} />
      ) : (
        <div className="gallery">
          {tasks.map((t) => <PosterCard key={t.id} task={t} />)}
        </div>
      )}
    </div>
  )
}
