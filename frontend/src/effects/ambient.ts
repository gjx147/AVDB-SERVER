/** 桃夭互动特效引擎 —— 纯视觉，不改任何功能。
 *  1. 鼠标追随光晕（lerp 缓动跟随）
 *  2. 点击涟漪（按钮/chip/tag/海报）
 *  3. 卡片光斑跟随（指针局部高光）
 *  4. 海报 3D 倾斜（hover 按指针位置 ±6°）
 *  5. 红心爆击（收藏点击六瓣飞散）
 *  性能保护：reduced-motion / 触屏（coarse pointer）自动关闭；全部 rAF 节流。
 */

const prefersReduced = typeof window !== 'undefined' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches
const coarse = typeof window !== 'undefined' &&
  window.matchMedia('(pointer: coarse)').matches
const enabled = !prefersReduced && !coarse

export function initAmbientEffects() {
  if (!enabled || typeof document === 'undefined') return
  initAura()
  initRipple()
  initSpotlight()
  initTilt()
  initHeartBurst()
  initPulseBand()
}

/* 顶部心跳光带（纯注入，动画在 CSS） */
function initPulseBand() {
  const band = document.createElement('div')
  band.className = 'pulse-band'
  band.setAttribute('aria-hidden', 'true')
  document.body.appendChild(band)
}

/* 1. 鼠标追随光晕 */
function initAura() {
  const aura = document.createElement('div')
  aura.className = 'aura'
  aura.setAttribute('aria-hidden', 'true')
  document.body.appendChild(aura)
  let tx = window.innerWidth / 2, ty = window.innerHeight / 2
  let x = tx, y = ty, raf = 0
  const loop = () => {
    x += (tx - x) * 0.08
    y += (ty - y) * 0.08
    aura.style.transform = `translate(${x - 170}px, ${y - 170}px)`
    raf = requestAnimationFrame(loop)
  }
  window.addEventListener('pointermove', (e) => { tx = e.clientX; ty = e.clientY }, { passive: true })
  raf = requestAnimationFrame(loop)
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) cancelAnimationFrame(raf)
    else raf = requestAnimationFrame(loop)
  })
}

/* 2. 点击涟漪（事件委托，按钮/chip/tag/海报卡） */
const RIPPLE_TARGETS = '.btn, .chip, .tag, .seg button, .nav-item'
function initRipple() {
  document.addEventListener('pointerdown', (e) => {
    const el = (e.target as HTMLElement).closest(RIPPLE_TARGETS) as HTMLElement | null
    if (!el) return
    const rect = el.getBoundingClientRect()
    if (rect.width < 8) return
    const dot = document.createElement('span')
    dot.className = 'ripple'
    const size = Math.max(rect.width, rect.height)
    dot.style.width = dot.style.height = `${size}px`
    dot.style.left = `${e.clientX - rect.left - size / 2}px`
    dot.style.top = `${e.clientY - rect.top - size / 2}px`
    const prevPos = getComputedStyle(el).position
    if (prevPos === 'static') el.style.position = 'relative'
    el.style.overflow = prevPos === 'static' ? 'hidden' : el.style.overflow
    el.appendChild(dot)
    dot.addEventListener('animationend', () => dot.remove())
  }, { passive: true })
}

/* 3. 卡片光斑跟随：给海报/统计卡/普通卡挂 pointer 高光 */
const SPOT_TARGETS = '.poster, .stat, .card'
function initSpotlight() {
  document.addEventListener('pointermove', (e) => {
    const el = (e.target as HTMLElement).closest(SPOT_TARGETS) as HTMLElement | null
    if (!el) return
    if (!el.classList.contains('spotlight')) el.classList.add('spotlight')
    const rect = el.getBoundingClientRect()
    el.style.setProperty('--mx', `${e.clientX - rect.left}px`)
    el.style.setProperty('--my', `${e.clientY - rect.top}px`)
  }, { passive: true })
}

/* 4. 海报 3D 倾斜（±6°，离开回弹） */
function initTilt() {
  const MAX = 6
  let current: HTMLElement | null = null
  let raf = 0
  const apply = (el: HTMLElement, rx: number, ry: number) => {
    cancelAnimationFrame(raf)
    raf = requestAnimationFrame(() => {
      el.style.transform = `perspective(800px) rotateX(${rx}deg) rotateY(${ry}deg) translateY(-6px)`
    })
  }
  document.addEventListener('pointermove', (e) => {
    const el = (e.target as HTMLElement).closest('.poster') as HTMLElement | null
    if (!el) {
      if (current) { current.style.transform = ''; current.classList.remove('tilt'); current = null }
      return
    }
    if (current !== el) {
      if (current) { current.style.transform = ''; current.classList.remove('tilt') }
      current = el
      el.classList.add('tilt')
    }
    const rect = el.getBoundingClientRect()
    const px = (e.clientX - rect.left) / rect.width - 0.5
    const py = (e.clientY - rect.top) / rect.height - 0.5
    apply(el, -py * MAX * 2, px * MAX * 2)
  }, { passive: true })
  document.addEventListener('pointerleave', () => {
    if (current) { current.style.transform = ''; current.classList.remove('tilt'); current = null }
  }, true)
}

/* 5. 红心爆击：含心形图标的按钮点击时六瓣飞散 */
function initHeartBurst() {
  document.addEventListener('pointerdown', (e) => {
    const btn = (e.target as HTMLElement).closest('.btn') as HTMLElement | null
    if (!btn) return
    const icon = btn.querySelector('.ic')
    const txt = btn.textContent || ''
    if (!icon && !/收藏|关注/.test(txt)) return
    const rect = btn.getBoundingClientRect()
    const cx = e.clientX - rect.left
    const cy = e.clientY - rect.top
    const prevPos = getComputedStyle(btn).position
    if (prevPos === 'static') btn.style.position = 'relative'
    for (let i = 0; i < 12; i++) {
      const kiss = i % 3 === 1  // 每三瓣混入一枚唇印
      const dot = document.createElement('span')
      dot.className = kiss ? 'burst-kiss' : 'burst-dot'
      if (kiss) dot.textContent = '💋'
      const angle = (Math.PI / 6) * i - Math.PI / 2
      const dist = 24 + Math.random() * 20
      dot.style.setProperty('--bx', `${Math.cos(angle) * dist}px`)
      dot.style.setProperty('--by', `${Math.sin(angle) * dist - 10}px`)
      dot.style.left = `${cx - 3}px`
      dot.style.top = `${cy - 3}px`
      btn.appendChild(dot)
      dot.addEventListener('animationend', () => dot.remove())
    }
  }, { passive: true })
}
