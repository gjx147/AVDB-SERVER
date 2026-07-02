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

  stats: null,
  setStats: (s) => set({ stats: s }),

  listSources: null,
  setListSources: (s) => set({ listSources: s }),
}))
