/**
 * API 客户端 —— 对接后端全部 56 个端点
 * 基地址走相对路径，生产由后端 SPA 同源服务，开发由 vite proxy 转发
 */
import axios from 'axios'
import type {
  Task, TaskDetail, TaskStats, ListSource, ListSourceWithStats, ListSourceCreate,
  Actor, ActorMovie, Ranking, RankType, DashboardStats, MonthlyStat,
  CrawlStatus, CrawlLogLine, Settings, SettingsUpdate, ApiOk,
  ThumbnailsResponse, DownloadImagesResult, Magnet,
  DownloadRecord, DiskInfo, NotifyTestResult,
} from './types'

const http = axios.create({ baseURL: '', timeout: 60000 })

// P0-1: API Token 认证 —— 自动附加 X-Api-Token 请求头
http.interceptors.request.use((config) => {
  const token = localStorage.getItem('apiToken')
  if (token) {
    config.headers['X-Api-Token'] = token
  }
  return config
})

// 统一错误信息提取
http.interceptors.response.use(
  (r) => r,
  (err) => {
    const detail = err?.response?.data?.detail || err?.message || '请求失败'
    return Promise.reject(new Error(String(detail)))
  },
)

// ── 图片 URL 构造（本地缓存直读）──
export const avatarUrl = (actorId: number) => `/api/images/avatar/${actorId}`


// ════════ Dashboard ════════
export const api = {
  dashboard: {
    stats: () => http.get<DashboardStats>('/api/dashboard/stats').then((r) => r.data),
    recent: (limit = 12) =>
      http.get<Task[]>('/api/dashboard/recent', { params: { limit } }).then((r) => r.data),
    monthly: () =>
      http.get<MonthlyStat[]>('/api/dashboard/monthly').then((r) => r.data),
  },

  // ════════ Tasks ════════
  tasks: {
    list: (params: {
      list_source_id?: number
      list_code?: string
      status?: string
      is_favorite?: 0 | 1
      skip?: number
      limit?: number
    } = {}) => http.get<Task[]>('/api/tasks', { params }).then((r) => r.data),

    search: (q: string, status?: string, skip = 0, limit = 50) =>
      http.get<Task[]>('/api/tasks/search', { params: { q, status, skip, limit } }).then((r) => r.data),

    searchCount: (q: string, status?: string) =>
      http.get<{ count: number }>('/api/tasks/search/count', { params: { q, status } }).then((r) => r.data.count),

    get: (id: number) =>
      http.get<TaskDetail>(`/api/tasks/${id}`).then((r) => r.data),

    createByCode: (list_code: string, video_code: string) =>
      http.post<Task>('/api/tasks/create-by-code', { list_code, video_code }).then((r) => r.data),

    extract: (id: number) =>
      http.post<ApiOk>(`/api/tasks/${id}/extract`).then((r) => r.data),

    magnets: (id: number) =>
      http.get<Magnet[]>(`/api/tasks/${id}/magnets`).then((r) => r.data),

    favorite: (id: number) =>
      http.post<ApiOk>(`/api/tasks/${id}/favorite`).then((r) => r.data),

    unfavorite: (id: number) =>
      http.delete<ApiOk>(`/api/tasks/${id}/favorite`).then((r) => r.data),

    note: (id: number, note: string) =>
      http.patch<ApiOk>(`/api/tasks/${id}/note`, { note }).then((r) => r.data),

    setStatus: (id: number, status: string) =>
      http.patch<ApiOk>(`/api/tasks/${id}/status`, { status }).then((r) => r.data),

    remove: (id: number) =>
      http.delete<ApiOk>(`/api/tasks/${id}`).then((r) => r.data),

    favorites: (skip = 0, limit = 50) =>
      http.get<Task[]>('/api/tasks/favorites/list', { params: { skip, limit } }).then((r) => r.data),

    batchDelete: (task_ids: number[]) =>
      http.post<ApiOk>('/api/tasks/batch/delete', { task_ids }).then((r) => r.data),

    batchRetry: (task_ids: number[]) =>
      http.post<ApiOk>('/api/tasks/batch/retry', { task_ids }).then((r) => r.data),

    batchFavorite: (task_ids: number[]) =>
      http.post<ApiOk>('/api/tasks/batch/favorite', { task_ids }).then((r) => r.data),

    stats: (list_source_id?: number) =>
      http.get<TaskStats[]>('/api/tasks/stats', { params: { list_source_id } }).then((r) => r.data),

    export: () => '/api/tasks/export',
  },

  // ════════ List Sources ════════
  listSources: {
    list: () =>
      http.get<ListSourceWithStats[]>('/api/list-sources').then((r) => r.data),
    create: (body: ListSourceCreate) =>
      http.post<ListSource>('/api/list-sources', body).then((r) => r.data),
    get: (id: number) =>
      http.get<ListSourceWithStats>(`/api/list-sources/${id}`).then((r) => r.data),
    remove: (id: number) =>
      http.delete<ApiOk>(`/api/list-sources/${id}`).then((r) => r.data),
    magnets: (id: number) =>
      http.get<string[]>(`/api/list-sources/${id}/magnets`).then((r) => r.data),
    recentMagnets: () =>
      http.get<string[]>('/api/list-sources/magnets/recent').then((r) => r.data),
    searchActor: (keyword: string) =>
      http.post<ListSource>('/api/list-sources/search-actor', { keyword }).then((r) => r.data),
  },

  // ════════ Actors ════════
  actors: {
    list: (skip = 0, limit = 100) =>
      http.get<Actor[]>('/api/actors', { params: { skip, limit } }).then((r) => r.data),
    search: (keyword: string) =>
      http.get<Actor[]>('/api/actors/search', { params: { q: keyword } }).then((r) => r.data),
    get: (id: number) =>
      http.get<Actor>(`/api/actors/${id}`).then((r) => r.data),
    movies: (id: number) =>
      http.get<ActorMovie[]>(`/api/actors/${id}/movies`).then((r) => r.data),
    crawl: (actor_url: string, list_source_id?: number) =>
      http.post<ApiOk>('/api/crawl/actor', { actor_url, list_source_id }).then((r) => r.data),
    crawlSearch: (actor_name: string) =>
      http.post<ApiOk>('/api/crawl/actor-search', { actor_name }).then((r) => r.data),
    refresh: (id: number) =>
      http.post<ApiOk>(`/api/actors/${id}/refresh`).then((r) => r.data),
  },

  // ════════ Rankings ════════
  rankings: {
    list: (rank_type: RankType = 'hot', rank_date?: string, skip = 0, limit = 100) =>
      http.get<Ranking[]>('/api/rankings', { params: { rank_type, rank_date, skip, limit } }).then((r) => r.data),
    latest: () =>
      http.get<Record<string, string>>('/api/rankings/latest').then((r) => r.data),
    crawl: (rank_type: RankType, max_pages = 5) =>
      http.post<ApiOk>('/api/crawl/ranking', { rank_type, max_pages }).then((r) => r.data),
    addTask: (ranking_id: number) =>
      http.post<ApiOk>(`/api/rankings/${ranking_id}/add-task`).then((r) => r.data),
    batchAddTasks: (ranking_ids: number[]) =>
      http.post<{ ok: boolean; results: { ranking_id: number; task_id: number | null; error?: string }[] }>(
        '/api/rankings/batch-add-tasks', { ranking_ids }
      ).then((r) => r.data),
  },

  // ════════ Crawl ════════
  crawl: {
    scan: (body: { list_code?: string; list_source_id?: number; update?: boolean }, background = true) =>
      http.post<ApiOk>('/api/crawl/scan', body, { params: { background } }).then((r) => r.data),
    extract: (body: { list_code?: string; list_source_id?: number; limit?: number }, background = true) =>
      http.post<ApiOk>('/api/crawl/extract', body, { params: { background } }).then((r) => r.data),
    extractFailed: (body: { list_code?: string; list_source_id?: number; limit?: number }, background = true) =>
      http.post<ApiOk>('/api/crawl/extract-failed', body, { params: { background } }).then((r) => r.data),
    status: () => http.get<CrawlStatus>('/api/crawl/status').then((r) => r.data),
    logs: () => http.get<CrawlLogLine>('/api/crawl/logs').then((r) => r.data),
    pause: () => http.post<ApiOk>('/api/crawl/pause').then((r) => r.data),
    resume: () => http.post<ApiOk>('/api/crawl/resume').then((r) => r.data),
    stop: () => http.post<ApiOk>('/api/crawl/stop').then((r) => r.data),
  },

  // ════════ Downloaders ════════
  downloaders: {
    download: (magnet: string, downloader?: string, save_path?: string, task_id?: number) =>
      http.post<ApiOk & { task_id?: number }>('/api/downloaders/download', { magnet, downloader, save_path, task_id }).then((r) => r.data),
    testConnection: (downloader: string, save_path?: string) =>
      http.post<ApiOk>('/api/downloaders/test-connection', { downloader, save_path }).then((r) => r.data),
  },

  // ════════ Downloads（下载历史 + 状态）════════
  downloads: {
    list: (status?: string, limit = 100, offset = 0) =>
      http.get<{ downloads: DownloadRecord[]; total: number }>('/api/downloads', { params: { status, limit, offset } }).then((r) => r.data),
  },

  // ════════ System ════════
  system: {
    disk: () => http.get<DiskInfo>('/api/system/disk').then((r) => r.data),
  },

  // ════════ Notify ════════
  notify: {
    test: () => http.post<{ ok: boolean; results: NotifyTestResult }>('/api/notify/test').then((r) => r.data),
  },

  // ════════ Actors 增强（F9 关注）════════
  actorsFollow: {
    follow: (actorId: number) =>
      http.post<ApiOk & { actor_id: number; is_followed: number }>(`/api/actors/${actorId}/follow`).then((r) => r.data),
    unfollow: (actorId: number) =>
      http.post<ApiOk & { actor_id: number; is_followed: number }>(`/api/actors/${actorId}/unfollow`).then((r) => r.data),
    followed: () =>
      http.get<{ actors: Array<{ id: number; name: string; name_en: string | null; avatar_url: string | null; movie_count: number; local_movie_count: number }> }>('/api/actors/followed').then((r) => r.data),
  },

  // ════════ Collections 收藏分组（F13）════════
  collections: {
    list: () => http.get<{ collections: { id: number; name: string; icon: string; sort_order: number; task_count: number }[] }>('/api/collections').then((r) => r.data),
    create: (name: string, icon?: string) => http.post<{ ok: boolean; id: number }>('/api/collections', { name, icon }).then((r) => r.data),
    remove: (id: number) => http.delete<ApiOk>(`/api/collections/${id}`).then((r) => r.data),
    addTask: (collectionId: number, taskId: number) => http.post<ApiOk>(`/api/collections/${collectionId}/tasks/${taskId}`).then((r) => r.data),
    removeTask: (collectionId: number, taskId: number) => http.delete<ApiOk>(`/api/collections/${collectionId}/tasks/${taskId}`).then((r) => r.data),
    tasks: (collectionId: number) => http.get<{ tasks: Task[] }>(`/api/collections/${collectionId}/tasks`).then((r) => r.data),
  },

  // ════════ Tasks 编辑（F20）════════
  tasksEdit: {
    update: (taskId: number, fields: Partial<Pick<Task, 'title' | 'actors' | 'tags' | 'release_date' | 'director' | 'maker' | 'label' | 'series' | 'duration' | 'note' | 'video_code'>>) =>
      http.patch<{ ok: boolean; updated: string[] }>(`/api/tasks/${taskId}`, fields).then((r) => r.data),
  },
  v2: {
    tasks: (params: {
      status?: string; list_source_id?: number; actor?: string; tag?: string; date_from?: string; date_to?: string;
      min_rating?: number; sort?: string; limit?: number; offset?: number;
    }) => http.get<{ tasks: Task[]; total: number }>('/api/v2/tasks', { params }).then((r) => r.data),
    searchFts: (q: string, limit = 48) =>
      http.get<{ tasks: Task[]; total: number; engine: string }>('/api/v2/tasks/search-fts', { params: { q, limit } }).then((r) => r.data),
    analytics: () =>
      http.get<{ top_actors: { name: string; count: number }[]; top_tags: { name: string; count: number }[]; top_makers: { name: string; count: number }[]; rating_dist: { bucket: string; count: number }[]; download_stats: Record<string, number>; daily_added: { day: string; count: number }[] }>('/api/v2/dashboard/analytics').then((r) => r.data),
    similar: (taskId: number) =>
      http.get<{ tasks: Task[]; total: number }>(`/api/v2/tasks/${taskId}/similar`).then((r) => r.data),
  },

  // ════════ Settings ════════
  settings: {
    get: () => http.get<Settings>('/api/settings').then((r) => r.data),
    update: (body: SettingsUpdate) => http.put<ApiOk>('/api/settings', body).then((r) => r.data),
    backup: () => http.post<Blob>('/api/settings/backup', {}, { responseType: 'blob' }).then((r) => r.data),
    restore: (file: File) => {
      const fd = new FormData()
      fd.append('file', file)
      return http.post<ApiOk>('/api/settings/restore', fd).then((r) => r.data)
    },
    cleanFailed: () => http.delete<ApiOk>('/api/settings/clean-failed').then((r) => r.data),
  },

  // ════════ Images ════════
  images: {
    thumbnails: (taskId: number) =>
      http.get<ThumbnailsResponse>(`/api/images/thumbnails/${taskId}`).then((r) => r.data),
    download: (taskId: number) =>
      http.post<DownloadImagesResult>(`/api/images/download/${taskId}`).then((r) => r.data),
    /** 重新抓取高清预览图（从页面 tile-item href）并下载到本地 */
    downloadHires: (taskId: number) =>
      http.post<{ ok: boolean; message: string; downloaded: { cover: boolean; thumbnails: number; total_found: number } }>(
        `/api/hires/download-hires/${taskId}`,
      ).then((r) => r.data),
    /** 检查是否有本地高清预览图缓存 */
    hasLocalThumbs: (taskId: number) =>
      http.get<{ has_local: boolean; count: number }>(`/api/hires/has-local-thumbs/${taskId}`).then((r) => r.data),
    /** 获取海报索引（自动检测+手动设置的结果） */
    posterIndex: (taskId: number) =>
      http.get<{ poster_index: number }>(`/api/hires/poster-index/${taskId}`).then((r) => r.data),
    /** 手动选择海报：0=gallery-1, 1=gallery-2, 2=gallery-3... */
    setPoster: (taskId: number, index: number) =>
      http.post<{ ok: boolean; message: string }>(`/api/hires/set-poster/${taskId}/${index}`).then((r) => r.data),
    /** 启动串行队列：逐个处理任务（下载图片+提取磁力） */
    queueStart: (taskIds: number[]) =>
      http.post<{ ok: boolean; message: string; total: number }>(`/api/hires/queue/start`, taskIds).then((r) => r.data),
    /** 查询串行队列状态 */
    queueStatus: () =>
      http.get<{
        running: boolean; total: number; current: number; current_task_id: number | null;
        current_video_code: string | null; stage: string; done: number[]; failed: number[]
      }>(`/api/hires/queue/status`).then((r) => r.data),
  },
}

/** 本地缓存高清预览图文件 URL（download-hires 下载的） */
export const thumbFileUrl = (taskId: number, index: number) => `/api/hires/thumb-file/${taskId}/${index}`

/** gallery-1竖→它就是海报, gallery-1横→gallery-2是海报（自动检测+可手动设置） */
export const coverFileUrl = (taskId: number) => `/api/hires/poster-file/${taskId}`

/** 背景图：优先 backdrop.jpg，回退 gallery-1.jpg */
export const backdropUrl = (taskId: number) => `/api/hires/backdrop-file/${taskId}`
