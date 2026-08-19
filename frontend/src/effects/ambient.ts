/** 桃夭互动特效引擎 —— 纯视觉，不改任何功能。
 *  1. 鼠标追随光晕（lerp 缓动跟随，静止停表）
 *  2. 点击涟漪（按钮/chip/tag/海报）
 *  3. 卡片光斑跟随（指针局部高光）
 *  4. 海报 3D 倾斜（hover 按指针位置 ±6°）
 *  5. 红心爆击（收藏点击六瓣飞散）
 *  6. 体温累积（hover/深 hover 加热，store 衰减；Boudoir）
 *  7. 烛光尘埃粒子（单 canvas ≤40，密度随体温；Boudoir）
 *  8. 胶片颗粒层（静态 SVG 噪点；Boudoir）
 *  9. 声色解锁与丝绸音（V3；引擎在 audio/，首次手势 unlock）
 *  10. 抚摸轨迹（指针划过海报留渐隐红晕；V3）
 *  11. 喘息追踪（10s 窗口交互频次 → excite；V3）
 *  性能保护：reduced-motion / 触屏（coarse pointer）自动关闭；全部 rAF 节流。
 */

import { useStore } from '../store/useStore'
import { audio } from '../audio/engine'

const prefersReduced = typeof window !== 'undefined' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches
const coarse = typeof window !== 'undefined' &&
  window.matchMedia('(pointer: coarse)').matches
const lowPower = coarse || (typeof navigator !== 'undefined' && (navigator as any).deviceMemory <= 4)
const enabled = !prefersReduced && !coarse

export function initAmbientEffects() {
  if (typeof document === 'undefined') return
  initGrain()
  initHeat()
  initSoundWiring()
  initCaress()
  initExcite()
  if (!enabled) return
  initAura()
  initRipple()
  initSpotlight()
  if (!lowPower) initTilt()
  initHeartBurst()
  initPulseBand()
  initScrollReveal()
  if (!lowPower && window.innerWidth > 768) initMotes()
}

/* 9. 声色接线：首次手势 unlock AudioContext；海报 hover 播丝绸音（2.5s 节流） */
function initSoundWiring() {
  document.addEventListener('pointerdown', () => {
    audio.unlock()
    // 解锁后立即恢复持续层（心跳/喘息/密室烛火），不等下一次交互
    const { heat, moodMode } = useStore.getState()
    if (heat > 0) audio.setHeat(heat / 100)
    if (moodMode && audio.enabled) audio.startAmbient()
  }, { once: true, capture: true, passive: true })
  let lastSilk = 0
  document.addEventListener('pointerover', (e) => {
    if (!audio.enabled) return
    const el = (e.target as HTMLElement).closest('.poster') as HTMLElement | null
    if (!el) return
    const now = performance.now()
    if (now - lastSilk < 2500) return
    lastSilk = now
    audio.play('silk', 0.8)
  }, { passive: true })
}

/* 10. 抚摸轨迹：指针在海报内划过留渐隐红晕（60ms 节流，速度反比尺寸；触屏 150ms） */
function initCaress() {
  if (prefersReduced || lowPower) return
  let last = 0
  let lastX = 0, lastY = 0
  document.addEventListener('pointermove', (e) => {
    const el = (e.target as HTMLElement).closest('.poster, .detail-cover, .actor-photo') as HTMLElement | null
    if (!el) return
    const now = performance.now()
    const gap = coarse ? 150 : 60
    if (now - last < gap) return
    last = now
    const host = (el.closest('.poster') || el) as HTMLElement
    const rect = host.getBoundingClientRect()
    if (e.clientX < rect.left || e.clientX > rect.right || e.clientY < rect.top || e.clientY > rect.bottom) return
    const dist = Math.hypot(e.clientX - lastX, e.clientY - lastY)
    lastX = e.clientX; lastY = e.clientY
    if (dist < 3) return  // 抖动忽略
    // 划得越慢红晕越大越深
    const size = Math.max(14, Math.min(64, 900 / Math.max(dist, 8)))
    const dot = document.createElement('span')
    dot.className = 'caress-dot'
    dot.style.width = dot.style.height = `${size}px`
    dot.style.left = `${e.clientX - rect.left - size / 2}px`
    dot.style.top = `${e.clientY - rect.top - size / 2}px`
    dot.style.setProperty('--cs', String(Math.min(1, 90 / Math.max(dist, 10))))
    host.appendChild(dot)
    dot.addEventListener('animationend', () => dot.remove(), { once: true })
  }, { passive: true })
}

/* 12. 喘息追踪：10s 滑窗内 ≥5 次 pointerdown → excite +20；页面隐藏不计 */
function initExcite() {
  if (prefersReduced) return
  const stamps: number[] = []
  document.addEventListener('pointerdown', () => {
    if (document.hidden) return
    const now = performance.now()
    while (stamps.length && now - stamps[0] > 10_000) stamps.shift()
    stamps.push(now)
    if (stamps.length >= 5) {
      useStore.getState().addExcite(20)
      stamps.length = 0
    }
  }, { passive: true })
}

/* 8. 胶片颗粒层（静态装饰，任何环境保留；样式在 eros.css） */
function initGrain() {
  if (document.querySelector('.grain')) return
  const g = document.createElement('div')
  g.className = 'grain'
  g.setAttribute('aria-hidden', 'true')
  document.body.appendChild(g)
}

/* 6. 体温累积：hover 海报 +2（全局 2.5s 节流）、深 hover 1.2s 再 +5。
   进详情 / 收藏的加热由页面侧调用 addHeat；衰减在 store 的 10s 定时器。 */
function initHeat() {
  let lastTick = 0
  let deepTimer = 0
  document.addEventListener('pointerover', (e) => {
    const el = (e.target as HTMLElement).closest('.poster') as HTMLElement | null
    if (!el) return
    const now = performance.now()
    if (now - lastTick > 2500) {
      lastTick = now
      useStore.getState().addHeat(2)
    }
    clearTimeout(deepTimer)
    deepTimer = window.setTimeout(() => useStore.getState().addHeat(5), 1200)
  }, { passive: true })
  document.addEventListener('pointerout', (e) => {
    if ((e.target as HTMLElement).closest('.poster')) clearTimeout(deepTimer)
  }, { passive: true })
}

/* 7. 烛光尘埃：暖粉尘埃缓慢上浮 + 呼吸明暗；密度随体温档提升；
   页面隐藏即停摆；单 rAF 循环。 */
function initMotes() {
  const c = document.createElement('canvas')
  c.className = 'motes'
  c.setAttribute('aria-hidden', 'true')
  document.body.appendChild(c)
  const ctx = c.getContext('2d')!
  const fit = () => { c.width = innerWidth; c.height = innerHeight }
  fit()
  window.addEventListener('resize', fit, { passive: true })
  const ps = Array.from({ length: 40 }, () => ({
    x: Math.random() * innerWidth, y: Math.random() * innerHeight,
    r: .5 + Math.random() * 1.8, v: .06 + Math.random() * .15, ph: Math.random() * 7,
  }))
  ;(function loop(t: number) {
    if (!document.hidden) {
      ctx.clearRect(0, 0, c.width, c.height)
      const heat = useStore.getState().heat / 100
      const count = Math.round(16 + 24 * heat)
      for (let i = 0; i < count; i++) {
        const p = ps[i]
        p.y -= p.v * (1 + heat * .8)
        const a = .12 + .2 * Math.sin(t / 1800 + p.ph) + heat * .1
        ctx.fillStyle = `rgba(255,143,179,${Math.max(0, a)})`
        ctx.beginPath()
        ctx.arc(p.x + 8 * Math.sin(t / 4000 + p.ph), p.y, p.r, 0, 7)
        ctx.fill()
        if (p.y < -4) { p.y = innerHeight + 4; p.x = Math.random() * innerWidth }
      }
    }
    requestAnimationFrame(loop)
  })(0)
}

/* 滚动显现：视口外的海报暂停入场动画+隐藏，滚到时再播放。
   首屏直接标记 rv（避免首屏闪烁）；分页/筛选新增的海报由 MutationObserver 追加观察。 */
function initScrollReveal() {
  document.documentElement.classList.add('reveal')
  const io = new IntersectionObserver((entries) => {
    for (const en of entries) {
      if (en.isIntersecting) {
        en.target.classList.add('rv')
        io.unobserve(en.target)
      }
    }
  }, { rootMargin: '60px', threshold: 0.05 })

  const observeAll = () => {
    document.querySelectorAll('.poster:not(.rv)').forEach((el) => io.observe(el))
  }
  observeAll()

  let timer = 0
  const mo = new MutationObserver(() => {
    clearTimeout(timer)
    timer = window.setTimeout(observeAll, 150)
  })
  mo.observe(document.body, { childList: true, subtree: true })
}

/* 顶部心跳光带（纯注入，动画在 CSS） */
function initPulseBand() {
  const band = document.createElement('div')
  band.className = 'pulse-band'
  band.setAttribute('aria-hidden', 'true')
  document.body.appendChild(band)
}

/* 1. 鼠标追随光晕（lerp 缓动跟随；静止停表——到位即 cancel，move 再重启，省常驻 rAF） */
function initAura() {
  const aura = document.createElement('div')
  aura.className = 'aura'
  aura.setAttribute('aria-hidden', 'true')
  document.body.appendChild(aura)
  let tx = window.innerWidth / 2, ty = window.innerHeight / 2
  let x = tx + 1, y = ty + 1, raf = 0
  const step = () => {
    x += (tx - x) * 0.08
    y += (ty - y) * 0.08
    aura.style.transform = `translate(${x - 170}px, ${y - 170}px)`
    if (Math.abs(tx - x) < 0.5 && Math.abs(ty - y) < 0.5) { raf = 0; return }
    raf = requestAnimationFrame(step)
  }
  const kick = () => { if (!raf) raf = requestAnimationFrame(step) }
  window.addEventListener('pointermove', (e) => { tx = e.clientX; ty = e.clientY; kick() }, { passive: true })
  kick()
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { cancelAnimationFrame(raf); raf = 0 }
    else kick()
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
