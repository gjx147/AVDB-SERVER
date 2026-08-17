import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'

export default function Login() {
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const navigate = useNavigate()

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
            {loading ? '登录中…' : '登录'}
          </button>
        </form>
      </div>
    </div>
  )
}
