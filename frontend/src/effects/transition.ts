/** 路由纱幕过场 —— 每次切页都是一次「掀纱」。
 *  App.tsx 在 pathname 变化时调用 playVeil()；双层纱幕合拢再揭开（动画在 eros.css）。
 *  降级：reduced-motion / 触屏 / 移动端宽度直接跳过。 */

const reduced = typeof window !== 'undefined' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches
const coarse = typeof window !== 'undefined' &&
  window.matchMedia('(pointer: coarse)').matches

let veilEl: HTMLElement | null = null

export function playVeil() {
  if (reduced || coarse || typeof document === 'undefined') return
  if (window.innerWidth <= 768) return
  if (!veilEl) {
    veilEl = document.createElement('div')
    veilEl.className = 'veil'
    veilEl.setAttribute('aria-hidden', 'true')
    veilEl.innerHTML = '<i></i><i></i>'
    document.body.appendChild(veilEl)
  }
  // 重启动画：移除类 → 强制 reflow → 重新挂类
  veilEl.classList.remove('play')
  void (veilEl as HTMLElement).offsetWidth
  veilEl.classList.add('play')
  window.setTimeout(() => veilEl?.classList.remove('play'), 1400)
}
