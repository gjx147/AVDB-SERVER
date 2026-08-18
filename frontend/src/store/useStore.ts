import { create } from 'zustand'
import type { ListSourceWithStats } from '../api/types'

export type ImgMode = 'normal' | 'blur' | 'hidden'

interface AppState {
  /** 图片显示模式：normal 正常 / blur 模糊悬停解除 / hidden 隐藏 */
  imgMode: ImgMode
  setImgMode: (m: ImgMode) => void

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

export const useStore = create<AppState>((set) => ({
  imgMode: (localStorage.getItem('imgMode') as ImgMode) || 'normal',
  setImgMode: (m) => {
    localStorage.setItem('imgMode', m)
    set({ imgMode: m })
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
