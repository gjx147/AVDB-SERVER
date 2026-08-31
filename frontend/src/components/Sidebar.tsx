import { useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'
import { useStore } from '../store/useStore'
import { Icon } from './Icons'
import { useWhisper, greetingKey, useNavMode } from '../i18n/whisper'

interface NavItem {
  to: string
  label: string
  icon: React.ReactNode
  statKey?: 'total' | 'favorites' | 'actors'
}
interface NavSection {
  section: string
  items: NavItem[]
}

const nav: NavSection[] = [
  { section: '浏览', items: [
    { to: '/preview', label: '预览', icon: <Icon.eye /> },
  { to: '/wall', label: '首页', icon: <Icon.home /> },
    { to: '/new-releases', label: '订阅上新', icon: <Icon.bell /> },
    { to: '/', label: '仪表盘', icon: <Icon.dashboard /> },
    { to: '/library', label: '影片库', icon: <Icon.library />, statKey: 'total' },
    { to: '/favorites', label: '收藏', icon: <Icon.heart />, statKey: 'favorites' },
    { to: '/actors', label: '演员库', icon: <Icon.actor />, statKey: 'actors' },
    { to: '/rankings', label: '排行榜', icon: <Icon.trophy /> },
    { to: '/top250-cats', label: 'TOP250-类别', icon: <Icon.trophy /> },
    { to: '/top250-years', label: 'TOP250-年份', icon: <Icon.clock /> },
  ]},
  { section: '采集', items: [
    { to: '/sources', label: '列表源', icon: <Icon.source /> },
    { to: '/crawl', label: '爬取控制台', icon: <Icon.console /> },
    { to: '/subscriptions', label: '订阅', icon: <Icon.refresh /> },
  ]},
  { section: '系统', items: [
    { to: '/downloads', label: '下载历史', icon: <Icon.download /> },
    { to: '/downloaders', label: '下载器', icon: <Icon.settings /> },
    { to: '/notifications', label: '通知中心', icon: <Icon.bell /> },
    { to: '/analytics', label: '分析中心', icon: <Icon.bell /> },
    { to: '/rules', label: '自动化规则', icon: <Icon.settings /> },
    { to: '/status', label: '系统状态', icon: <Icon.settings /> },
    { to: '/settings', label: '设置', icon: <Icon.settings /> },
  ]},
]

export function Sidebar({ open, onClose }: { open?: boolean; onClose?: () => void }) {
  const stats = useStore((s) => s.stats)
  const moodMode = useStore((s) => s.moodMode)
  const toggleMood = useStore((s) => s.toggleMood)
  const navMode = useStore((s) => s.navMode)
  const toggleNavMode = useStore((s) => s.toggleNavMode)
  const w = useWhisper()
  const nl = useNavMode()
  return (
    <aside className={`sidebar${open ? ' open' : ''}`}>
      <div className="brand">
        <div className="brand-mark">AV<em>DB</em></div>
        <div className="brand-tag">Cinema Library</div>
      </div>
      <nav className="nav" aria-label="主导航">
        {nav.map((sec) => (
          <div className="nav-section" key={sec.section}>
            <div className="nav-label">{nl(sec.section)}</div>
            {sec.items.map((it) => (
              <NavLink
                key={it.to}
                to={it.to}
                end={it.to === '/'}
                className={({ isActive }) => `nav-item${isActive ? ' on' : ''}`}
              >
                {it.icon}
                {nl(it.label)}
                {it.statKey && stats ? (
                  <span className="nav-badge">{stats[it.statKey]}</span>
                ) : null}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>
      <div className="sidebar-foot">
        {/* v4.1 文字模式切换：正常 ↔ 情话 */}
        <button className={`mood-toggle${navMode === 'whisper' ? ' on' : ''}`} onClick={toggleNavMode}
          aria-pressed={navMode === 'whisper'} title={navMode === 'whisper' ? '切回正常文字' : '切换为情话文字'}>
          <span aria-hidden="true">{navMode === 'whisper' ? '💌' : '💬'}</span>
          {navMode === 'whisper' ? '文字 · 情话' : '文字 · 正常'}
        </button>
        {/* 烛光密室开关：暗场聚光 + 大胆文案档 + Wall 慢节奏（烛焰随体温涨落） */}
        <button className={`mood-toggle${moodMode ? ' on' : ''}`} onClick={toggleMood}
          aria-pressed={moodMode} title={moodMode ? '离开密室' : '进入烛光密室'}>
          <span className="flame" aria-hidden="true">🕯</span>
          {moodMode ? '离开密室' : '烛光密室'}
        </button>
        <SidebarStatus />
        {/* 时段问候：她按早晚深夜换语气 */}
        <div className="sidebar-greet">{w(greetingKey())}</div>
        <div>{stats ? `${stats.total} 部作品` : 'AVDB v2.0'}</div>
      </div>
    </aside>
  )
}

function SidebarStatus() {
  const [online, setOnline] = useState<boolean | null>(null)
  useEffect(() => {
    let alive = true
    const check = () => {
      const token = localStorage.getItem('apiToken') || ''
      fetch('/api/health', { headers: { Authorization: `Bearer ${token}` } })
        .then((r) => { if (alive) setOnline(r.ok) })
        .catch(() => { if (alive) setOnline(false) })
    }
    check()
    const t = setInterval(check, 30000)
    return () => { alive = false; clearInterval(t) }
  }, [])
  return (
    <div>
      <span className="dot" style={{ color: online === null ? 'var(--t-faint, #999)' : online ? 'var(--green, #059669)' : 'var(--red, #dc2626)' }}>●</span>
      {online === null ? '检测中…' : online ? '后端已连接' : '后端离线'}
    </div>
  )
}
