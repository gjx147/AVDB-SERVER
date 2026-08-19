import { create } from 'zustand'
import type { ListSourceWithStats } from '../api/types'

export type ImgMode = 'normal' | 'blur' | 'hidden'
export type ThemeMode = 'auto' | 'light' | 'boudoir'

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
}

interface AppState {
  /** 图片显示模式：normal 正常 / blur 模糊悬停解除 / hidden 隐藏 */
  imgMode: ImgMode
  setImgMode: (m: ImgMode) => void

  /** 主题：auto 跟随系统 / light 欲焰亮色 / boudoir 暗夜丝绒 */
  theme: ThemeMode
  setTheme: (t: ThemeMode) => void

  /** 烛光密室模式（大胆文案档 + 暗场聚光交互层） */
  moodMode: boolean
  toggleMood: () => void

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

/* 体温闲置衰减：每 10s -1（会话级，不持久化）；页面隐藏时暂停 */
if (typeof window !== 'undefined') {
  window.setInterval(() => {
    if (document.hidden) return
    const { heat, addHeat } = useStore.getState()
    if (heat > 0) addHeat(-1)
  }, 10_000)
  // 系统深浅色变化时，auto 主题实时跟随
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (useStore.getState().theme === 'auto') applyTheme('auto')
  })
}
