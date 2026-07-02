import { lazy, Suspense, useEffect, useState } from 'react'
import { Routes, Route, useLocation } from 'react-router-dom'
import { Sidebar } from './components/Sidebar'
import { Toast } from './components/Toast'
import { Loading } from './components/States'
import { ErrorBoundary } from './components/ErrorBoundary'
import { useStore } from './store/useStore'
import { api } from './api/client'

// P1: 代码拆分 —— 10 个页面按需懒加载，减少首屏打包体积
const Dashboard   = lazy(() => import('./pages/Dashboard').then(m => ({ default: m.Dashboard })))
const Library     = lazy(() => import('./pages/Library').then(m => ({ default: m.Library })))
const Favorites   = lazy(() => import('./pages/Favorites').then(m => ({ default: m.Favorites })))
const Actors      = lazy(() => import('./pages/Actors').then(m => ({ default: m.Actors })))
const Rankings    = lazy(() => import('./pages/Rankings').then(m => ({ default: m.Rankings })))
const ListSources = lazy(() => import('./pages/ListSources').then(m => ({ default: m.ListSources })))
const Crawl       = lazy(() => import('./pages/Crawl').then(m => ({ default: m.Crawl })))
const Downloaders = lazy(() => import('./pages/Downloaders').then(m => ({ default: m.Downloaders })))
const Downloads   = lazy(() => import('./pages/Downloads').then(m => ({ default: m.Downloads })))
const Settings    = lazy(() => import('./pages/Settings').then(m => ({ default: m.Settings })))
const TaskDetail  = lazy(() => import('./pages/TaskDetail').then(m => ({ default: m.TaskDetail })))

export default function App() {
  const { pathname } = useLocation()
  const setStats = useStore((s) => s.setStats)
  const imgMode = useStore((s) => s.imgMode)
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)

  useEffect(() => {
    api.dashboard.stats().then((s) => {
      setStats({ total: s.total_tasks, favorites: s.favorite_count, actors: s.actor_count })
    }).catch(() => {})
  }, [setStats])

  useEffect(() => { window.scrollTo(0, 0); setMobileMenuOpen(false) }, [pathname])

  return (
    <div className={`app img-mode-${imgMode}`}>
      {/* 移动端汉堡菜单按钮 */}
      <button className="mobile-menu-btn" onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
        aria-label={mobileMenuOpen ? '关闭菜单' : '打开菜单'}>
        {mobileMenuOpen ? '✕' : '☰'}
      </button>
      {/* 移动端侧栏遮罩 */}
      {mobileMenuOpen && <div className="sidebar-overlay" onClick={() => setMobileMenuOpen(false)} />}
      <Sidebar open={mobileMenuOpen} onClose={() => setMobileMenuOpen(false)} />
      <main className="main">
        <ErrorBoundary>
        <Suspense fallback={<div className="page"><Loading /></div>}>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/library" element={<Library />} />
            <Route path="/favorites" element={<Favorites />} />
            <Route path="/actors" element={<Actors />} />
            <Route path="/rankings" element={<Rankings />} />
            <Route path="/sources" element={<ListSources />} />
            <Route path="/crawl" element={<Crawl />} />
            <Route path="/downloaders" element={<Downloaders />} />
            <Route path="/downloads" element={<Downloads />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/task/:id" element={<TaskDetail />} />
          </Routes>
        </Suspense>
        </ErrorBoundary>
      </main>
      <Toast />
    </div>
  )
}
