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
    { to: '/wall', label: '首页', icon: <Icon.home /> },
    { to: '/new-releases', label: '订阅上新', icon: <Icon.bell /> },
    { to: '/', label: '仪表盘', icon: <Icon.dashboard /> },
    { to: '/library', label: '影片库', icon: <Icon.library />, statKey: 'total' },
    { to: '/favorites', label: '收藏', icon: <Icon.heart />, statKey: 'favorites' },
    { to: '/actors', label: '演员库', icon: <Icon.actor />, statKey: 'actors' },
    { to: '/rankings', label: '排行榜', icon: <Icon.trophy /> },
  ]},
  { section: '采集', items: [
    { to: '/sources', label: '列表源', icon: <Icon.source /> },
    { to: '/crawl', label: '爬取控制台', icon: <Icon.console /> },
    { to: '/subscriptions', label: '订阅', icon: <Icon.refresh /> },
  ]},
  { section: '系统', items: [
    { to: '/downloads', label: '下载历史', icon: <Icon.download /> },
    { to: '/downloaders', label: '下载器', icon: <Icon.settings /> },
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
        <div><span className="dot">●</span> 后端已连接</div>
        {/* 时段问候：她按早晚深夜换语气 */}
        <div className="sidebar-greet">{w(greetingKey())}</div>
        <div>{stats ? `${stats.total} 部作品` : 'AVDB v2.0'}</div>
      </div>
    </aside>
  )
}
