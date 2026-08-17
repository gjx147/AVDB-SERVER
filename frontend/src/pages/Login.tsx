import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'

export default function Login() {
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [wall, setWall] = useState<string[]>([])
  const navigate = useNavigate()

  // 订阅演员作品海报墙（免鉴权接口；失败静默回退纯色背景）
  useEffect(() => {
    fetch('/api/system/login-wall?limit=24')
      .then((r) => (r.ok ? r.json() : { wall: [] }))
      .then((d) => {
        if (!Array.isArray(d.wall) || !d.wall.length) return
        const list = d.wall.slice(0, 24)
        // 拼接墙需要足够磁贴铺满屏：不足 18 张时循环补齐
        while (list.length < 18) list.push(...list.slice(0, Math.min(list.length, 18 - list.length)))
        setWall(list)
      })
      .catch(() => {})
  }, [])

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      const formData = new URLSearchParams()
      formData.append('username', username)
      formData.append('password', password)
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: formData,
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({ detail: '登录失败' }))
        throw new Error(data.detail || '用户名或密码错误')
      }
      const data = await res.json()
      localStorage.setItem('apiToken', data.access_token)
      navigate('/')
    } catch (err) {
      setError(String((err as Error).message))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      {wall.length > 0 && (
        <div className="login-wall" aria-hidden="true">
          {wall.map((u, i) => (
            <img key={i} src={u} alt="" referrerPolicy="no-referrer" loading="lazy"
              onError={(e) => { e.currentTarget.style.visibility = 'hidden' }}
              style={{ '--d': `${(i % 9) * 1.6}s` } as React.CSSProperties} />
          ))}
        </div>
      )}
      <div className="login-card">
        <div className="login-brand">
          <div className="login-mark">AV<em>DB</em></div>
          <div className="login-tag">Cinema Library</div>
        </div>
        <form onSubmit={handleLogin} style={{ width: '100%' }}>
          {error && <div className="login-error">{error}</div>}
          <div className="field">
            <label htmlFor="login-user">用户名</label>
            <input
              id="login-user"
              className="input"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
            />
          </div>
          <div className="field" style={{ marginBottom: 22 }}>
            <label htmlFor="login-pass">密码</label>
            <input
              id="login-pass"
              className="input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              autoFocus
            />
          </div>
          <button type="submit" className="btn btn--gold" disabled={loading} style={{ width: '100%', justifyContent: 'center', padding: '11px 0' }}>
            {loading ? '正在开门…' : '深夜入口'}
          </button>
        </form>
      </div>
    </div>
  )
}
