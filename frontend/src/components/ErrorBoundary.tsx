import { Component, type ReactNode } from 'react'

interface Props { children: ReactNode }
interface State { error: Error | null }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error) { return { error } }

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
