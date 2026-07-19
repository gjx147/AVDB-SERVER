import { useEffect, useRef, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api, thumbFileUrl, coverFileUrl, backdropUrl } from '../api/client'
import type { TaskDetail as Task, ThumbnailsResponse } from '../api/types'
import { Loading, Empty } from '../components/States'
import { Icon } from '../components/Icons'
import { MetaItem } from '../components/MetaItem'
import { useStore } from '../store/useStore'

export function TaskDetail() {
  const { id } = useParams()
  const nav = useNavigate()
  const [task, setTask] = useState<Task | null | undefined>(undefined)
  const [thumbs, setThumbs] = useState<string[]>([])
  const [activeThumb, setActiveThumb] = useState(0)
  const [dlOpen, setDlOpen] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [similar, setSimilar] = useState<import('../api/types').Task[]>([])
  const toastOk = useStore((s) => s.toastOk)
  const toastErr = useStore((s) => s.toastErr)

  const [hasLocal, setHasLocal] = useState(false)
  const [imgVersion, setImgVersion] = useState(0)
  const [dlDownloader, setDlDownloader] = useState('')  // '' = 使用默认
  const reqSeqRef = useRef(0)  // P1-6: 请求序号防竞态
  const timersRef = useRef<number[]>([])  // P0-4: 用 ref 跟踪 timers
  const downloadingRef = useRef(false)  // P0-4: ref 避免自我取消

  const load = () => {
    if (!id) return
    downloadingRef.current = false
    setHasLocal(false)
    setDownloading(false)
    // P1-8: 切换任务时清理 pending 定时器
    timersRef.current.forEach(clearTimeout)
    timersRef.current = []
    api.tasks.get(+id).then(async (t) => {
      // 单独加载磁力列表（TaskOut 不含 magnets 字段）
      try {
        const resp = await api.tasks.magnets(+id)
        t.magnets = (resp && Array.isArray(resp.magnets)) ? resp.magnets : []
      } catch { /* ignore */ }
      setTask(t); loadThumbs(+id)
    }).catch(() => setTask(null))
  }
  const loadThumbs = (tid: number) => {
    api.images.thumbnails(tid).then((r: ThumbnailsResponse) => { setThumbs(r.thumbnails); setActiveThumb(0) }).catch(() => setThumbs([]))
    // 检查是否有本地高清缓存
    api.images.hasLocalThumbs(tid).then((r) => setHasLocal(r.has_local)).catch(() => setHasLocal(false))
  }
  useEffect(load, [id])

  // 磁力优先级后缀：从 settings 读取，fallback 到默认 -UC,-C,-U
  const [preferredSuffixes, setPreferredSuffixes] = useState<string[]>(['-uc', '-c', '-u'])
  useEffect(() => {
    api.settings.get().then((s) => {
      if (s.preferred_suffixes) {
        const arr = s.preferred_suffixes.split(',').map((x: string) => x.trim().toLowerCase()).filter(Boolean)
        if (arr.length > 0) setPreferredSuffixes(arr)
      }
    }).catch(() => {})
  }, [])
  const PREFERRED_SUFFIXES = preferredSuffixes
  const magnetPriority = (link: string): number => {
    const low = link.toLowerCase()
    for (let i = 0; i < PREFERRED_SUFFIXES.length; i++) {
      if (PREFERRED_SUFFIXES[i] && low.includes(PREFERRED_SUFFIXES[i])) return i
    }
    return PREFERRED_SUFFIXES.length
  }
  const topMagnets = (() => {
    if (!task?.magnets || task.magnets.length === 0) return []
    const seen = new Set<string>()
    const items: { link: string; size?: string; name?: string; priority: number }[] = []
    const strVal = (v: unknown): string | undefined => (typeof v === 'string' && v) ? v : undefined
    for (const m of task.magnets) {
      const link = m.link || m.magnet || ''
      if (!link || seen.has(link)) continue
      seen.add(link)
      items.push({ link, size: strVal(m.size) || strVal(m.file_size), name: strVal(m.name), priority: typeof m.priority === 'number' ? m.priority : magnetPriority(link) })
    }
    // best_magnet 确保在列表且排第一
    if (task.best_magnet) {
      const bm = task.best_magnet
      if (!seen.has(bm)) {
        items.unshift({ link: bm, priority: magnetPriority(bm) })
      }
    }
    items.sort((a, b) => a.priority - b.priority)
    return items.slice(0, 3)
  })()

  // F15: 加载相似影片推荐
  useEffect(() => {
    if (!id) return
    setSimilar([])
    api.v2.similar(+id).then((r) => setSimilar(r.tasks)).catch(() => setSimilar([]))
  }, [id])

  // 自动缓存已禁用：用户手动点「重新下载高清图片」才下载
  useEffect(() => {
    return () => { timersRef.current.forEach(clearTimeout); timersRef.current = [] }
  }, [])

  if (task === undefined) return <div className="page"><Loading /></div>
  if (task === null) return <div className="page"><Empty title="任务不存在" /></div>

  const tags = task.tags ? task.tags.split(',').map((t) => t.trim()).filter(Boolean) : []
  const actors = task.actors ? task.actors.split(',').map((a) => a.trim()).filter(Boolean) : []

  const fav = async () => {
    try {
      task.is_favorite ? await api.tasks.unfavorite(task.id) : await api.tasks.favorite(task.id)
      toastOk(task.is_favorite ? '已取消收藏' : '已收藏')
      load()
    } catch (e) { toastErr(String((e as Error).message)) }
  }
  const extract = async () => {
    try { await api.tasks.extract(task.id); toastOk('已开始提取磁力') } catch (e) { toastErr(String((e as Error).message)) }
  }
  const copyMagnet = (m: string) => {
    navigator.clipboard.writeText(m).then(() => toastOk('磁力已复制')).catch(() => toastErr('复制失败'))
  }
  const download = async (magnet: string) => {
    try {
      await api.downloaders.download(magnet, dlDownloader || undefined, undefined, task.id)
      toastOk(`已推送${dlDownloader ? '到 ' + dlDownloader : ''}`)
      load()  // 刷新下载状态
    } catch (e) { toastErr(String((e as Error).message)) }
  }
  // 图片爬取：重新抓取页面高清预览图，下载到本地缓存（自动/手动共用）
  const fetchImages = async (silent = false) => {
    downloadingRef.current = true
    setDownloading(true)
    try {
      const r = await api.images.downloadHires(task.id)
      setImgVersion((v) => v + 1)
      setHasLocal(true)  // P1#7: 直接标记已有本地缓存，而非调 load()（load 会重置 hasLocal→触发自动缓存 effect 重复下载）
      loadThumbs(task.id)
      if (!silent) toastOk(r.message || `已下载 ${r.downloaded.thumbnails} 张高清预览图`)
      return true
    } catch (e) {
      if (!silent) toastErr(String((e as Error).message))
      return false
    } finally { downloadingRef.current = false; setDownloading(false) }
  }

  // 自动缓存逻辑已移到条件 return 之前（React Hooks 规则）

  // 远程图片 URL（本地无缓存时 fallback）
  const remoteImgs = (() => {
    try { return JSON.parse(task.thumbnail_urls || '[]') } catch { return [] }
  })()
  const remoteCover = task.poster_url || remoteImgs[0] || ''
  const remoteBackdrop = task.poster_url || remoteImgs[0] || ''

  return (
    <div className="page">
      {/* emby 风格背景：gallery-1（index 0）全屏模糊 */}
      <div className="detail-bg">
        <img src={`${backdropUrl(task.id)}?v=${imgVersion}`} alt=""
          onError={(e) => { if (remoteBackdrop) e.currentTarget.src = remoteBackdrop; else e.currentTarget.style.opacity = '0' }} />
      </div>

      <button className="btn btn--ghost btn--sm" style={{ marginBottom: 20, position: 'relative', zIndex: 1 }}
        onClick={() => { if (window.history.length > 1) nav(-1); else nav('/library') }}><Icon.back />返回</button>

      {/* 紧凑头部：左海报（gallery-2 index 1）+ 右信息 */}
      <div className="detail-head" style={{ position: 'relative', zIndex: 1 }}>
        <div className="detail-cover">
          <img
            src={`${coverFileUrl(task.id)}?v=${imgVersion}`}
            alt={`${task.video_code || '作品'} 海报`}
            onError={(e) => { if (remoteCover) e.currentTarget.src = remoteCover; else e.currentTarget.style.opacity = '0' }}
            onLoad={(e) => { e.currentTarget.style.opacity = '1' }}
          />
          {downloading && <div className="detail-cover-empty" style={{ position: 'absolute', inset: 0 }}>缓存中…</div>}
        </div>
        <div className="detail-info">
          <div className="detail-code">{task.video_code || '—'}</div>
          <h1 className="detail-title">{task.title || '未命名作品'}</h1>

          {/* 操作栏 —— 含图片下载（图片爬取） */}
          <div className="detail-actions">
            <button className={`btn ${task.is_favorite ? 'btn--ghost' : 'btn--gold'}`} onClick={fav}>
              <Icon.heart />{task.is_favorite ? '取消收藏' : '收藏'}
            </button>
            <button className="btn btn--ghost" onClick={extract}><Icon.link />提取磁力</button>
            <button className="btn btn--ghost" onClick={() => fetchImages()} disabled={downloading}>
              <Icon.download />{downloading ? '下载中…' : (hasLocal ? '重新下载图片' : '重新下载高清图片')}
            </button>
            {task.best_magnet && <button className="btn btn--ghost" onClick={() => copyMagnet(task.best_magnet!)}><Icon.copy />复制最佳</button>}
          </div>
          {downloading && !hasLocal && (
            <div style={{ fontSize: 12, color: 'var(--gold)', marginBottom: 20, padding: '8px 12px', background: 'var(--gold-wash)', borderRadius: 'var(--r-sm)' }}>
              正在下载高清图片，请稍候…
            </div>
          )}

          {/* 元数据（移到头部信息区，首屏可见） */}
          <div className="detail-meta-grid" style={{ marginBottom: 20 }}>
            <MetaItem label="演员" val={task.actors} />
            <MetaItem label="发行日期" val={task.release_date} />
            <MetaItem label="时长" val={task.duration} />
            <MetaItem label="评分" val={task.rating} />
            <MetaItem label="片商" val={task.maker} />
            <MetaItem label="厂牌" val={task.label} />
            <MetaItem label="系列" val={task.series} />
            <MetaItem label="导演" val={task.director} />
          </div>

          {(tags.length > 0 || actors.length > 0) && (
            <div className="tag-row">{[...actors, ...tags].map((t) => <span className="tag" key={t}>{t}</span>)}</div>
          )}
        </div>
      </div>

      <div className="detail-body" style={{ position: 'relative', zIndex: 1 }}>
        <div>

          {/* 预览图画廊 —— 显示所有缩略图（本地优先，远程 fallback） */}
          {thumbs.length > 0 && (
            <div style={{ marginBottom: 28 }}>
              <div className="dm-label" style={{ marginBottom: 12 }}>预览图（{thumbs.length}）</div>
              {/* 大图查看：自适应高度，原图原比例 */}
              <div style={{
                position: 'relative', borderRadius: 'var(--r-lg)', overflow: 'hidden',
                marginBottom: 10, background: 'var(--bg-surface)',
              }}>
                <img
                  src={hasLocal ? `${thumbFileUrl(task.id, activeThumb)}?v=${imgVersion}` : thumbs[activeThumb]}
                  alt={`${task.video_code || '作品'} 预览图 ${activeThumb + 1}`}
                  style={{ display: 'block', width: '100%', height: 'auto', objectFit: 'cover', imageRendering: 'auto' }}
                  onError={(e) => { e.currentTarget.style.opacity = '0.2' }}
                />
                {/* 手动选择海报按钮（仅本地缓存时可用） */}
                {hasLocal && (
                  <button
                    onClick={async (ev) => {
                      ev.stopPropagation()
                      try { await api.images.setPoster(task.id, activeThumb); toastOk('已设为海报'); setImgVersion(v => v + 1) }
                      catch (e) { toastErr(String((e as Error).message)) }
                    }}
                    style={{ position: 'absolute', top: 8, left: 8, zIndex: 3, background: 'var(--gold)', color: '#fff', border: 'none', borderRadius: 6, padding: '4px 10px', fontSize: 11, cursor: 'pointer', fontWeight: 600 }}
                  >设为海报</button>
                )}
                {thumbs.length > 1 && <>
                  <button onClick={() => setActiveThumb((i) => (i - 1 + thumbs.length) % thumbs.length)}
                    style={thumbNavBtn('left')}>‹</button>
                  <button onClick={() => setActiveThumb((i) => (i + 1) % thumbs.length)}
                    style={thumbNavBtn('right')}>›</button>
                </>}
              </div>
              {/* 缩略图条 */}
              {thumbs.length > 1 && (
                <div style={{ display: 'flex', gap: 6, overflowX: 'auto', paddingBottom: 4 }}>
                  {thumbs.map((u, i) => (
                    <img key={i} src={hasLocal ? `${thumbFileUrl(task.id, i)}?v=${imgVersion}` : u} alt={`${task.video_code || '作品'} 预览图 ${i + 1}`} loading="lazy" onClick={() => setActiveThumb(i)}
                      style={{
                        width: 72, height: 48, objectFit: 'cover', borderRadius: 6, cursor: 'pointer', flex: 'none',
                        border: i === activeThumb ? '2px solid var(--gold)' : '2px solid transparent', opacity: i === activeThumb ? 1 : .6,
                      }} />
                  ))}
                </div>
              )}
            </div>
          )}

          {/* 补充元数据（头部未含的） */}
          <div className="detail-meta-grid" style={{ marginBottom: 20 }}>
            <MetaItem label="文件大小" val={task.file_size} />
            <MetaItem label="重试次数" val={task.retry_count > 0 ? String(task.retry_count) : null} />
          </div>

          {task.synopsis && (
            <div style={{ marginBottom: 24 }}>
              <div className="dm-label" style={{ marginBottom: 8 }}>简介</div>
              <p style={{ fontSize: 13, lineHeight: 1.8, color: 'var(--t-body)' }}>{task.synopsis}</p>
            </div>
          )}
          {task.description && (
            <div style={{ marginBottom: 24 }}>
              <div className="dm-label" style={{ marginBottom: 8 }}>描述</div>
              <p style={{ fontSize: 13, lineHeight: 1.8, color: 'var(--t-mute)' }}>{task.description}</p>
            </div>
          )}

          {task.note && (
            <div className="card" style={{ marginTop: 20 }}>
              <div className="dm-label" style={{ marginBottom: 6 }}>备注</div>
              <p style={{ fontSize: 13, color: 'var(--t-body)' }}>{task.note}</p>
            </div>
          )}

          {task.error_message && (
            <div className="magnet-box" style={{ marginTop: 20, color: 'var(--red)' }}>错误：{task.error_message}</div>
          )}
        </div>

        {/* 右栏：磁力 */}
        <div>
          <div className="card">
            <div className="card-head">
              <div className="card-title">磁力链接</div>
              {task.magnets && task.magnets.length > 0 && (
                <button className="btn btn--ghost btn--sm" onClick={() => setDlOpen(!dlOpen)}>推送下载</button>
              )}
            </div>
            {task.download_status && (
              <div style={{ marginBottom: 10, padding: '6px 12px', borderRadius: 'var(--r-sm)',
                background: task.download_status === 'completed' ? 'rgba(76,175,80,.1)' :
                  task.download_status === 'failed' ? 'rgba(244,67,54,.08)' : 'var(--gold-wash)',
                fontSize: 12, color: 'var(--t-body)' }}>
                {task.download_status === 'completed' ? '✅ 已下载完成' :
                 task.download_status === 'downloading' ? '⏳ 正在下载中' :
                 task.download_status === 'failed' ? '❌ 下载失败' :
                 '📤 已推送到下载器'}
              </div>
            )}
            {dlOpen && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 12, color: 'var(--t-mute)' }}>下载器:</span>
                <select className="select" value={dlDownloader} onChange={(e) => setDlDownloader(e.target.value)}>
                  <option value="">默认</option>
                  <option value="clouddrive">CloudDrive2</option>
                  <option value="qbittorrent">qBittorrent</option>
                </select>
              </div>
            )}
            {!task.best_magnet && topMagnets.length === 0 ? <Empty icon="○" title="暂无磁力链接" sub="点击「提取磁力」获取。" /> : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {/* 第 1 条：best_magnet（最优）单独强调显示 */}
                {topMagnets.map((m, i) => (
                  <div key={i}>
                    <div className="magnet-box" style={i === 0 ? { borderColor: 'var(--gold)', background: 'var(--gold-wash)' } : undefined}>
                      {m.link}
                      {/* 优先级 / 大小标签 */}
                      <span style={{ marginLeft: 8, whiteSpace: 'nowrap' }}>
                        {m.priority < PREFERRED_SUFFIXES.length && (
                          <span className="chip chip-green" style={{ fontSize: 10, marginRight: 4 }}>
                            {PREFERRED_SUFFIXES[m.priority].toUpperCase()}
                          </span>
                        )}
                        {i === 0 && <span className="chip" style={{ fontSize: 10, background: 'var(--gold)', color: '#fff', marginRight: 4 }}>最佳</span>}
                        {m.size && <span style={{ fontSize: 11, color: 'var(--t-mute)' }}>({m.size})</span>}
                      </span>
                    </div>
                    <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
                      <button className="btn btn--ghost btn--sm" onClick={() => copyMagnet(m.link)}><Icon.copy />复制</button>
                      {dlOpen && <button className="btn btn--gold btn--sm" onClick={() => download(m.link)}>推送</button>}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* F15: 相似影片推荐 */}
      {similar.length > 0 && (
        <div style={{ position: 'relative', zIndex: 1, marginTop: 28 }}>
          <div className="dm-label" style={{ marginBottom: 12 }}>相似影片</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))', gap: 14 }}>
            {similar.map((s) => {
              // 与详情页封面统一：poster_url 优先（横版裁剪主角），兜底 thumbnail_urls[0]
              const sRemote = s.poster_url || (s.thumbnail_urls ? (() => { try { return JSON.parse(s.thumbnail_urls)[0] as string } catch { return null } })() : null)
              return (
              <div key={s.id} onClick={() => nav(`/task/${s.id}`)}
                style={{ cursor: 'pointer', transition: 'transform .2s' }}
                onMouseEnter={(e) => e.currentTarget.style.transform = 'translateY(-3px)'}
                onMouseLeave={(e) => e.currentTarget.style.transform = ''}>
                <img src={`${coverFileUrl(s.id)}?v=0`} alt={s.video_code || ''}
                  style={{ width: '100%', aspectRatio: '7/10', objectFit: 'cover', objectPosition: 'right center', borderRadius: 'var(--r-md)' }}
                  onError={(e) => { if (sRemote && e.currentTarget.src !== sRemote) e.currentTarget.src = sRemote; else e.currentTarget.style.opacity = '0.2' }} />
                <div style={{ fontSize: 11, marginTop: 4, fontFamily: 'var(--ff-mono)', color: 'var(--t-mute)' }}>{s.video_code}</div>
                {s.rating && <div style={{ fontSize: 10, color: 'var(--gold)' }}>★ {s.rating}</div>}
              </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

const thumbNavBtn = (side: 'left' | 'right'): React.CSSProperties => ({
  position: 'absolute', top: '50%', [side]: 8,
  transform: 'translateY(-50%)',
  width: 36, height: 36, borderRadius: '50%',
  border: 'none', background: 'rgba(20,17,16,.6)', color: '#fff',
  fontSize: 22, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
  backdropFilter: 'blur(4px)',
})
