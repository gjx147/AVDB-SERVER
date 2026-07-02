import { NavLink } from 'react-router-dom'
import { useStore } from '../store/useStore'
import { Icon } from './Icons'

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
    { to: '/', label: '仪表盘', icon: <Icon.dashboard /> },
    { to: '/library', label: '影片库', icon: <Icon.library />, statKey: 'total' },
    { to: '/favorites', label: '收藏', icon: <Icon.heart />, statKey: 'favorites' },
    { to: '/actors', label: '演员库', icon: <Icon.actor />, statKey: 'actors' },
    { to: '/rankings', label: '排行榜', icon: <Icon.trophy /> },
  ]},
  { section: '采集', items: [
    { to: '/sources', label: '列表源', icon: <Icon.source /> },
    { to: '/crawl', label: '爬取控制台', icon: <Icon.console /> },
  ]},
  { section: '系统', items: [
    { to: '/downloads', label: '下载历史', icon: <Icon.download /> },
    { to: '/downloaders', label: '下载器', icon: <Icon.settings /> },
    { to: '/settings', label: '设置', icon: <Icon.settings /> },
  ]},
]

export function Sidebar({ open, onClose }: { open?: boolean; onClose?: () => void }) {
  const stats = useStore((s) => s.stats)
  return (
    <aside className={`sidebar${open ? ' open' : ''}`}>
      <div className="brand">
        <div className="brand-mark">AV<em>DB</em></div>
        <div className="brand-tag">Cinema Library</div>
      </div>
      <nav className="nav" aria-label="主导航">
        {nav.map((sec) => (
          <div className="nav-section" key={sec.section}>
            <div className="nav-label">{sec.section}</div>
            {sec.items.map((it) => (
              <NavLink
                key={it.to}
                to={it.to}
                end={it.to === '/'}
                className={({ isActive }) => `nav-item${isActive ? ' on' : ''}`}
              >
                {it.icon}
                {it.label}
                {it.statKey && stats ? (
                  <span className="nav-badge">{stats[it.statKey]}</span>
                ) : null}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>
      <div className="sidebar-foot">
        <div><span className="dot">●</span> 后端已连接</div>
        <div>{stats ? `${stats.total} 部作品` : 'AVDB v2.0'}</div>
      </div>
    </aside>
  )
}
