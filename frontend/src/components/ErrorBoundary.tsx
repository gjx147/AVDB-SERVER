import { Component, type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'

interface Props { children: ReactNode; resetKey?: string }
interface State { error: Error | null; resetKey?: string }

class ErrorBoundaryBase extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error) { return { error } }

  // 路由路径变化（resetKey 变化）时清除错误态，避免单页偶发错误把整个应用锁死在错误屏
  static getDerivedStateFromProps(props: Props, state: State) {
    if (props.resetKey !== state.resetKey) {
      // 记录当前路径；若正处于错误态则一并清除（等价于监听路径变化 setState({ error: null })）
      return state.error ? { error: null, resetKey: props.resetKey } : { resetKey: props.resetKey }
    }
    return null
  }

  render() {
    if (this.state.error) {
      return (
        <div className="page" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '60vh' }}>
          <div className="empty">
            <div className="em-icon" style={{ color: 'var(--red)' }}>⚠</div>
            <div className="em-title" style={{ color: 'var(--red)' }}>页面出错</div>
            <div style={{ fontSize: 13, color: 'var(--t-mute)', maxWidth: 400, textAlign: 'center', margin: '4px 0 12px' }}>
              {this.state.error.message}
            </div>
            <button className="btn btn--ghost btn--sm" onClick={() => this.setState({ error: null })}>
              重新加载
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
// 对外包装：监听路由路径，路径变化时重置错误态（不卸载子树）
export function ErrorBoundary({ children }: { children: ReactNode }) {
  const location = useLocation()
  return <ErrorBoundaryBase resetKey={location.pathname}>{children}</ErrorBoundaryBase>
}
