import { create } from 'zustand'
import type { ListSourceWithStats } from '../api/types'
import { audio } from '../audio/engine'

export type ImgMode = 'normal' | 'blur' | 'hidden'
export type ThemeMode = 'auto' | 'light' | 'boudoir'
export type CopyTier = 0 | 1 | 2  /* 耳语文案档：0 克制 / 1 大胆 / 2 露骨 */

/* ── 主题 / 密室 / 体温 的 DOM 应用 ──
   theme: auto 跟随系统深浅色；boudoir = 暗夜丝绒调色板（[data-theme]）
   mood:  烛光密室交互层（[data-mode="mood"]），与主题、隐私模式正交可叠加 */
function applyTheme(t: ThemeMode) {
  const el = document.documentElement
  if (t === 'auto') {
    const dark = window.matchMedia('(prefers-color-scheme: dark)').matches
    if (dark) el.dataset.theme = 'boudoir'
    else delete el.dataset.theme
  } else if (t === 'boudoir') {
    el.dataset.theme = 'boudoir'
  } else {
    delete el.dataset.theme
  }
}
function applyMood(on: boolean) {
  if (on) document.documentElement.dataset.mode = 'mood'
  else delete document.documentElement.dataset.mode
}
function applyHeat(v: number) {
  document.documentElement.style.setProperty('--heat', (v / 100).toFixed(2))
  audio.setHeat(v / 100)
}
function applyExcite(v: number) {
  document.documentElement.style.setProperty('--excite', (v / 100).toFixed(2))
  /* 喘息 >60：界面呼吸轻颤（CSS data-breath 开关） */
  if (v > 60) document.documentElement.dataset.breath = 'on'
  else delete document.documentElement.dataset.breath
}

interface AppState {
  /** 图片显示模式：normal 正常 / blur 模糊悬停解除 / hidden 隐藏 */
  imgMode: ImgMode
  setImgMode: (m: ImgMode) => void

  /** 主题：auto 跟随系统 / light 欲焰亮色 / boudoir 暗夜丝绒 */
  theme: ThemeMode
  setTheme: (t: ThemeMode) => void

  /** 烛光密室模式（暗场聚光交互层） */
  moodMode: boolean
  toggleMood: () => void

  /** 耳语文案档：0 克制 / 1 大胆 / 2 露骨（独立于密室，设置页可选） */
  copyTier: CopyTier
  setCopyTier: (t: CopyTier) => void
  /** 深夜加成（22–5 点文案自动升一档） */
  nightBoost: boolean
  setNightBoost: (on: boolean) => void

  /** 侧边栏文字模式：normal 正常 / whisper 情话（v4.1 切换按钮） */
  navMode: 'normal' | 'whisper'
  toggleNavMode: () => void

  /** 声色层：WebAudio 音效总开关（默认静音） */
  soundOn: boolean
  setSoundOn: (on: boolean) => void

  /** 喘息值 0–100：10 秒级快累积（与体温正交），顶点触发 climax 颤栗后回落 */
  excite: number
  addExcite: (n: number) => void
  /** 娇嗔态：快速跳片触发，温柔停留 3s 触发融化并解除 */
  sulk: boolean
  markDwell: () => void
  calmDown: () => void
  /** 跳片时间戳（内部） */
  _dwellLog: number[]

  /** 体温 0–100：交互累积、闲置衰减；映射 CSS 变量 --heat(0~1) 驱动全站升温 */
  heat: number
  addHeat: (n: number) => void

  /** Toast 消息 */
  toast: { msg: string; err: boolean; key: number } | null
  toastOk: (msg: string) => void
  toastErr: (msg: string) => void

  /** 应用内确认弹窗（替代原生 confirm） */
  confirmDialog: { open: boolean; title: string; message: string; danger: boolean; resolve: ((ok: boolean) => void) | null }
  confirm: (title: string, message: string, danger?: boolean) => Promise<boolean>
  resolveConfirm: (ok: boolean) => void

  /** 侧栏统计角标（从 dashboard 拉取后填充） */
  stats: { total: number; favorites: number; actors: number } | null
  setStats: (s: AppState['stats']) => void

  /** 列表源缓存（Library / Crawl / ListSources 三页共享） */
  listSources: ListSourceWithStats[] | null
  setListSources: (s: ListSourceWithStats[]) => void
}

const storedTheme = (localStorage.getItem('theme') as ThemeMode) || 'auto'
applyTheme(storedTheme)
const storedMood = localStorage.getItem('moodMode') === '1'
if (storedMood) applyMood(true)

export const useStore = create<AppState>((set, get) => ({
  imgMode: (localStorage.getItem('imgMode') as ImgMode) || 'normal',
  setImgMode: (m) => {
    localStorage.setItem('imgMode', m)
    set({ imgMode: m })
  },

  theme: storedTheme,
  setTheme: (t) => {
    localStorage.setItem('theme', t)
    applyTheme(t)
    set({ theme: t })
  },

  moodMode: storedMood,
  toggleMood: () => {
    const next = !get().moodMode
    localStorage.setItem('moodMode', next ? '1' : '0')
    applyMood(next)
    set({ moodMode: next })
    // 声色：密室开→烛火环境音；关→停
    if (audio.enabled) next ? audio.startAmbient() : audio.stopAmbient()
  },

  copyTier: (parseInt(localStorage.getItem('copyTier') ?? '', 10) as CopyTier)
    || (storedMood ? 1 : 0),
  setCopyTier: (t) => {
    localStorage.setItem('copyTier', String(t))
    set({ copyTier: t })
  },
  nightBoost: localStorage.getItem('nightBoost') !== '0',
  setNightBoost: (on) => {
    localStorage.setItem('nightBoost', on ? '1' : '0')
    set({ nightBoost: on })
  },

  navMode: (localStorage.getItem('navMode') as 'normal' | 'whisper') || 'normal',
  toggleNavMode: () => {
    const next = get().navMode === 'whisper' ? 'normal' : 'whisper'
    localStorage.setItem('navMode', next)
    set({ navMode: next })
  },

  soundOn: audio.enabled,
  setSoundOn: (on) => {
    audio.setEnabled(on)
    if (on && get().moodMode) audio.startAmbient()
    set({ soundOn: on })
  },

  excite: 0,
  addExcite: (n) => {
    const v = Math.max(0, Math.min(100, get().excite + n))
    if (v === get().excite) return
    applyExcite(v)
    set({ excite: v })
    if (v >= 100) {
      // 顶点：一次性颤栗，随后线性回落（3s 内平复）
      document.documentElement.classList.add('climax')
      window.setTimeout(() => document.documentElement.classList.remove('climax'), 400)
      window.setTimeout(() => { applyExcite(0); set({ excite: 0 }) }, 3000)
    }
  },
  sulk: false,
  _dwellLog: [],
  markDwell: () => {
    const now = performance.now()
    const log = get()._dwellLog.filter((t) => now - t < 10_000)
    log.push(now)
    const sulk = log.length >= 5
    if (sulk !== get().sulk) document.documentElement.dataset.sulk = sulk ? 'true' : 'false'
    set({ _dwellLog: log, sulk })
  },
  calmDown: () => {
    if (!get().sulk) return
    document.documentElement.classList.add('melt')
    window.setTimeout(() => document.documentElement.classList.remove('melt'), 1400)
    delete document.documentElement.dataset.sulk
    set({ sulk: false, _dwellLog: [] })
  },

  heat: 0,
  addHeat: (n) => {
    const v = Math.max(0, Math.min(100, get().heat + n))
    if (v === get().heat) return
    applyHeat(v)
    set({ heat: v })
  },

  toast: null,
  toastOk: (msg) => set({ toast: { msg, err: false, key: Date.now() } }),
  toastErr: (msg) => set({ toast: { msg, err: true, key: Date.now() } }),

  confirmDialog: { open: false, title: '', message: '', danger: true, resolve: null },
  confirm: (title, message, danger = true) =>
    new Promise<boolean>((resolve) => {
      set({ confirmDialog: { open: true, title, message, danger, resolve } })
    }),
  resolveConfirm: (ok) => {
    const d = useStore.getState().confirmDialog
    d.resolve?.(ok)
    set({ confirmDialog: { open: false, title: '', message: '', danger: false, resolve: null } })
  },

  stats: null,
  setStats: (s) => set({ stats: s }),

  listSources: null,
  setListSources: (s) => set({ listSources: s }),
}))

/* 体温闲置衰减：每 10s -1（会话级，不持久化）；喘息 2s -5；页面隐藏时暂停并静音 */
if (typeof window !== 'undefined') {
  window.setInterval(() => {
    if (document.hidden) return
    const { heat, addHeat } = useStore.getState()
    if (heat > 0) addHeat(-1)
  }, 10_000)
  window.setInterval(() => {
    if (document.hidden) return
    const { excite, addExcite } = useStore.getState()
    if (excite > 0) addExcite(-5)
  }, 2_000)
  // 页面隐藏：心跳/喘息即停；回前台按当前体温整体恢复（heartbeat + moan）
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) audio.stopHeartbeat()
    else {
      const h = useStore.getState().heat
      if (h > 0) audio.setHeat(h / 100)  // 同步心跳与喘息层
    }
  })
  // 系统深浅色变化时，auto 主题实时跟随
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (useStore.getState().theme === 'auto') applyTheme('auto')
  })
}
