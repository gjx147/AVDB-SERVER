import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { Routes, Route, useLocation, useNavigationType, Navigate } from 'react-router-dom'
import { Sidebar } from './components/Sidebar'
import { Toast } from './components/Toast'
import { ConfirmDialog } from './components/ConfirmDialog'
import { Loading } from './components/States'
import { ErrorBoundary } from './components/ErrorBoundary'
import { useStore } from './store/useStore'
import { api } from './api/client'
import { playVeil } from './effects/transition'

// P1: 代码拆分 —— 按需懒加载
const Login       = lazy(() => import('./pages/Login'))
const Wall        = lazy(() => import('./pages/Wall').then(m => ({ default: m.Wall })))
const Dashboard   = lazy(() => import('./pages/Dashboard').then(m => ({ default: m.Dashboard })))
const Library     = lazy(() => import('./pages/Library').then(m => ({ default: m.Library })))
const Favorites   = lazy(() => import('./pages/Favorites').then(m => ({ default: m.Favorites })))
const Actors      = lazy(() => import('./pages/Actors').then(m => ({ default: m.Actors })))
const ActorDetail = lazy(() => import('./pages/ActorDetail').then(m => ({ default: m.ActorDetail })))
const Rankings    = lazy(() => import('./pages/Rankings').then(m => ({ default: m.Rankings })))
const ListSources = lazy(() => import('./pages/ListSources').then(m => ({ default: m.ListSources })))
const Crawl       = lazy(() => import('./pages/Crawl').then(m => ({ default: m.Crawl })))
const Subscriptions = lazy(() => import('./pages/Subscriptions').then(m => ({ default: m.Subscriptions })))
const Downloaders = lazy(() => import('./pages/Downloaders').then(m => ({ default: m.Downloaders })))
const Downloads   = lazy(() => import('./pages/Downloads').then(m => ({ default: m.Downloads })))
const Settings    = lazy(() => import('./pages/Settings').then(m => ({ default: m.Settings })))
const TaskDetail  = lazy(() => import('./pages/TaskDetail').then(m => ({ default: m.TaskDetail })))

// 鉴权守卫：无 token 跳登录
function RequireAuth({ children }: { children: React.ReactNode }) {
  const token = localStorage.getItem('apiToken')
  if (!token) return <Navigate to="/login" replace />
  return <>{children}</>
}

export default function App() {
  const { pathname, search } = useLocation()
  const navType = useNavigationType()
  const setStats = useStore((s) => s.setStats)
  const imgMode = useStore((s) => s.imgMode)
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)

  // 列表页滚动位置记忆：POP（返回/前进）时恢复，新导航回顶
  const scrollMap = useRef(new Map<string, number>())
  const firstRoute = useRef(true)
  useEffect(() => {
    const key = pathname + search
    const onScroll = () => scrollMap.current.set(key, window.scrollY)
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [pathname, search])
  useEffect(() => {
    setMobileMenuOpen(false)
    // 路由纱幕过场：每次切页都是一次「掀纱」（首帧跳过；降级在 transition.ts 内处理）
    if (!firstRoute.current) playVeil()
    firstRoute.current = false
    if (navType === 'POP') {
      const y = scrollMap.current.get(pathname + search)
      // 等页面渲染后再恢复，避免被组件内 scrollTo 覆盖
      requestAnimationFrame(() => window.scrollTo(0, y || 0))
    } else {
      window.scrollTo(0, 0)
    }
  }, [pathname, search, navType])

  useEffect(() => {
    if (localStorage.getItem('apiToken')) {
      api.dashboard.stats().then((s) => {
        setStats({ total: s.total_tasks, favorites: s.favorite_count, actors: s.actor_count })
      }).catch(() => {})
    }
  }, [setStats])

  // 登录页不显示侧栏
  if (pathname === '/login') {
    return (
      <Routes>
        <Route path="/login" element={
          <Suspense fallback={<Loading />}><Login /></Suspense>
        } />
      </Routes>
    )
  }

  return (
    <div className={`app img-mode-${imgMode}`}>
      <button className="mobile-menu-btn" onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
        aria-label={mobileMenuOpen ? '关闭菜单' : '打开菜单'} aria-expanded={mobileMenuOpen}>
        {mobileMenuOpen ? '✕' : '☰'}
      </button>
      {mobileMenuOpen && <div className="sidebar-overlay" onClick={() => setMobileMenuOpen(false)} />}
      <Sidebar open={mobileMenuOpen} onClose={() => setMobileMenuOpen(false)} />
      <main className="main">
        <ErrorBoundary>
        <Suspense fallback={<div className="page"><Loading /></div>}>
          <RequireAuth>
          <Routes>
            <Route path="/wall" element={<Wall />} />
            <Route path="/" element={<Dashboard />} />
            <Route path="/library" element={<Library />} />
            <Route path="/favorites" element={<Favorites />} />
            <Route path="/actors" element={<Actors />} />
            <Route path="/actor/:id" element={<ActorDetail />} />
            <Route path="/rankings" element={<Rankings />} />
            <Route path="/sources" element={<ListSources />} />
            <Route path="/crawl" element={<Crawl />} />
            <Route path="/subscriptions" element={<Subscriptions />} />
            <Route path="/downloaders" element={<Downloaders />} />
            <Route path="/downloads" element={<Downloads />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/task/:id" element={<TaskDetail />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
          </RequireAuth>
        </Suspense>
        </ErrorBoundary>
      </main>
      <Toast />
      <ConfirmDialog />
    </div>
  )
}
