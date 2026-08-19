/** 心动仪式 —— 收藏/关注成功时的感官化反馈。
 *  <Heartburst playKey={n} />：playKey 变化时播一轮（描边爱心升起 + 玫瑰光屑散落）。
 *  挂在 position:relative 容器内，自动消散。 */
import { useEffect, useState } from 'react'

const PETALS = 7

export function Heartburst({ playKey }: { playKey: number }) {
  const [on, setOn] = useState(false)
  useEffect(() => {
    if (!playKey) return
    setOn(true)
    const t = setTimeout(() => setOn(false), 1300)
    return () => clearTimeout(t)
  }, [playKey])
  if (!on) return null
  return (
    <div className="hburst" aria-hidden="true">
      <span className="hb-heart">♥</span>
      {Array.from({ length: PETALS }, (_, i) => {
        const angle = (Math.PI * 2 * i) / PETALS + Math.random() * 0.6
        const dist = 34 + Math.random() * 36
        return (
          <span key={i} className="hb-petal" style={{
            '--px': `${Math.cos(angle) * dist}px`,
            '--py': `${Math.sin(angle) * dist - 18}px`,
            '--pr': `${Math.random() * 360}deg`,
          } as React.CSSProperties} />
        )
      })}
    </div>
  )
}
