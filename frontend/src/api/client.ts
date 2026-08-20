/**
 * API 客户端 —— 对接后端全部 56 个端点
 * 基地址走相对路径，生产由后端 SPA 同源服务，开发由 vite proxy 转发
 */
import axios from 'axios'
import type {
  Task, TaskDetail, TaskStats, ListSource, ListSourceWithStats, ListSourceCreate,
  Actor, ActorMovie, CastMember, Ranking, RankType, DashboardStats, MonthlyStat,
  CrawlStatus, CrawlLogLine, Settings, SettingsUpdate, ApiOk,
  ThumbnailsResponse, DownloadImagesResult, Magnet,
  DownloadRecord, DiskInfo, NotifyTestResult, NewRelease,
} from './types'

const http = axios.create({ baseURL: '', timeout: 60000 })

// JWT Bearer Token 认证 —— 自动附加 Authorization 头
http.interceptors.request.use((config) => {
  const token = localStorage.getItem('apiToken')
  if (token) {
    config.headers['Authorization'] = `Bearer ${token}`
  }
  return config
})

// 401 自动跳转登录页
http.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err?.response?.status === 401 && !window.location.pathname.includes('/login')) {
      localStorage.removeItem('apiToken')
      window.location.href = '/login'
    }
    // 422 的 detail 是数组、部分错误是对象 —— 非字符串统一 JSON.stringify，避免 toast 显示 [object Object]
    const rawDetail = err?.response?.data?.detail
    const detail = rawDetail !== undefined && rawDetail !== null && rawDetail !== ''
      ? (typeof rawDetail === 'string' ? rawDetail : JSON.stringify(rawDetail))
      : (err?.message || '请求失败')
    return Promise.reject(new Error(detail))
  },
)

// ── 图片 URL 构造（本地缓存直读）──


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

    extract: (id: number) =>
      http.post<ApiOk>(`/api/tasks/${id}/extract`).then((r) => r.data),

    magnets: (id: number) =>
      http.get<{ magnets: Magnet[]; video_code: string }>(`/api/tasks/${id}/magnets`).then((r) => r.data),

    cast: (id: number) =>
      http.get<CastMember[]>(`/api/tasks/${id}/cast`).then((r) => r.data),

    favorite: (id: number) =>
      http.post<ApiOk>(`/api/tasks/${id}/favorite`).then((r) => r.data),

    unfavorite: (id: number) =>
      http.delete<ApiOk>(`/api/tasks/${id}/favorite`).then((r) => r.data),

    note: (id: number, note: string) =>
      http.patch<ApiOk>(`/api/tasks/${id}/note`, { note }).then((r) => r.data),

    remove: (id: number) =>
      http.delete<ApiOk>(`/api/tasks/${id}`).then((r) => r.data),

    favorites: (skip = 0, limit = 50, inLibrary?: boolean) =>
      http.get<Task[]>('/api/tasks/favorites/list', { params: { skip, limit, in_library: inLibrary } }).then((r) => {
        const d = r.data as unknown
        return Array.isArray(d) ? d : (d as { items?: Task[] }).items || []
      }),

    delete: (taskId: number) =>
      http.delete<ApiOk>(`/api/tasks/${taskId}`).then((r) => r.data),

    // 后端签名是裸数组 body（task_ids: list[int]），不能包对象，否则 422
    batchDelete: (task_ids: number[]) =>
      http.post<ApiOk>('/api/tasks/batch/delete', task_ids).then((r) => r.data),

    batchRetry: (task_ids: number[]) =>
      http.post<ApiOk>('/api/tasks/batch/retry', task_ids).then((r) => r.data),

    batchFavorite: (task_ids: number[]) =>
      http.post<ApiOk>('/api/tasks/batch/favorite', task_ids).then((r) => r.data),

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
    list: (skip = 0, limit = 100, withAvatar?: boolean, followed?: boolean) =>
      http.get<Actor[]>('/api/actors', { params: { page: Math.floor(skip / limit) + 1, page_size: limit, with_avatar: withAvatar, followed } }).then((r) => {
        const d = r.data as unknown
        return Array.isArray(d) ? d : (d as { items?: Actor[] }).items || []
      }),
    /** 自动翻页拉取全部演员（后端 page_size 上限 200；订阅页建头像映射用） */
    listAll: (withAvatar?: boolean, pageSize = 200): Promise<Actor[]> => {
      const fetchPage = async (page: number): Promise<{ items: Actor[]; total: number }> =>
        http.get<unknown>('/api/actors', { params: { page, page_size: pageSize, with_avatar: withAvatar } }).then((r) => {
          const d = r.data as { items?: Actor[]; total?: number } | Actor[]
          return Array.isArray(d)
            ? { items: d as Actor[], total: (d as Actor[]).length }
            : { items: d.items || [], total: d.total ?? 0 }
        })
      return (async () => {
        const out: Actor[] = []
        let page = 1
        for (;;) {
          const r = await fetchPage(page)
          out.push(...r.items)
          if (r.items.length < pageSize || out.length >= r.total) break
          page++
        }
        return out
      })()
    },
    search: (keyword: string) =>
      http.get<Actor[]>('/api/actors', { params: { q: keyword, page: 1, page_size: 120 } }).then((r) => {
        const d = r.data as unknown
        return Array.isArray(d) ? d : (d as { items?: Actor[] }).items || []
      }),
    get: (id: number) =>
      http.get<Actor>(`/api/actors/${id}`).then((r) => r.data),
    // 关注 = 创建 actor 订阅（auto_add=false，定时检测+通知）
    follow: (actorId: number) =>
      http.post<ApiOk & { actor_id: number; subscribed: boolean }>(`/api/actors/${actorId}/follow`).then((r) => r.data),
    unfollow: (actorId: number) =>
      http.post<ApiOk & { actor_id: number; subscribed: boolean }>(`/api/actors/${actorId}/unfollow`).then((r) => r.data),
    // 切换自动入库（auto_add）
    toggleAutoAdd: (actorId: number) =>
      http.post<ApiOk & { actor_id: number; auto_add: boolean }>(`/api/actors/${actorId}/auto-add`).then((r) => r.data),
    movies: (id: number, page = 1, page_size = 30, sort: 'added' | 'release' | 'rating' = 'added', inLibrary?: boolean) =>
      http.get<{ items: ActorMovie[]; total: number; page: number; page_size: number }>(
        `/api/actors/${id}/movies`, { params: { page, page_size, sort, in_library: inLibrary } }
      ).then((r) => r.data),
    crawl: (actor_url: string, list_source_id?: number) =>
      http.post<ApiOk>('/api/crawl/actor', { actor_url, list_source_id }).then((r) => r.data),
    crawlWorks: (actorId: number) =>
      http.post<{ ok: boolean; pid: number; mode: string; actor_url: string }>(`/api/actors/${actorId}/crawl-works`).then((r) => r.data),
    refreshProfile: (actorId: number) =>
      http.post<{ ok: boolean; source: string | null; fields?: Record<string, string | null>; message?: string; locked_skipped?: string[] }>(`/api/actors/${actorId}/refresh-profile`).then((r) => r.data),
    profileQueueStatus: () =>
      http.get<{ pending: number; fetched: number; failed: number }>('/api/actors/profile-queue/status').then((r) => r.data),
    crawlSearch: (actor_name: string) =>
      http.post<ApiOk>('/api/crawl/actor-search', { actor_name }).then((r) => r.data),
    remove: (actorId: number) =>
      http.delete<ApiOk>(`/api/actors/${actorId}`).then((r) => r.data),
    /** 手动编辑演员资料（未传字段不更新；字符串空值=清空） */
    update: (actorId: number, patch: Record<string, string | null | boolean | undefined>) =>
      http.patch<ApiOk>(`/api/actors/${actorId}`, patch).then((r) => r.data),
    /** 头像手动更换候选（laoshi / minnano-av / JavDB） */
    avatarOptions: (actorId: number) =>
      http.get<{ current: string | null; options: { key: string; label: string; url: string }[] }>(
        `/api/actors/${actorId}/avatar-options`
      ).then((r) => r.data),
  },

  // ════════ Rankings ════════
  rankings: {
    list: (rank_type: RankType = 'daily', rank_date?: string, skip = 0, limit = 100, inLibrary?: boolean) =>
      http.get<Ranking[]>('/api/rankings', { params: { rank_type, rank_date, skip, limit, in_library: inLibrary } }).then((r) => r.data),
    latest: () =>
      http.get<Record<string, string>>('/api/rankings/latest').then((r) => r.data),
    crawl: (rank_type: RankType) =>
      http.post<ApiOk>('/api/crawl/ranking', { rank_type }).then((r) => r.data),
    addTask: (ranking_id: number) =>
      http.post<ApiOk>(`/api/rankings/${ranking_id}/add-task`).then((r) => r.data),
    batchAddTasks: (ranking_ids: number[]) =>
      http.post<{ ok: boolean; added?: number; skipped?: number; results: { ranking_id: number; task_id: number | null; error?: string }[] }>(
        '/api/rankings/batch-add-tasks', { ranking_ids }
      ).then((r) => r.data),
  },

  // ════════ Crawl ════════
  crawl: {
    scan: (body: { list_code?: string; list_source_id?: number; update?: boolean; pages?: number }, background = true) =>
      http.post<ApiOk>('/api/crawl/scan', body, { params: { background } }).then((r) => r.data),
    extract: (body: { list_code?: string; list_source_id?: number; limit?: number }, background = true) =>
      http.post<ApiOk>('/api/crawl/extract', body, { params: { background } }).then((r) => r.data),
    extractFailed: (body: { list_code?: string; list_source_id?: number; limit?: number }, background = true) =>
      http.post<ApiOk>('/api/crawl/extract-failed', body, { params: { background } }).then((r) => r.data),
    refreshMetadata: (limit?: number) =>
      http.post<ApiOk>('/api/crawl/refresh-metadata', { limit }).then((r) => r.data),
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
    logs: (limit = 100) =>
      http.get<{ lines: string[]; total: number }>('/api/downloaders/logs', { params: { limit } }).then((r) => r.data),
  },

  // ════════ Downloads（下载历史 + 状态）════════
  downloads: {
    list: (status?: string, limit = 100, offset = 0) =>
      http.get<{ downloads: DownloadRecord[]; total: number }>('/api/downloads', { params: { status, limit, offset } }).then((r) => {
        const d = r.data as unknown as { downloads?: DownloadRecord[]; items?: DownloadRecord[]; total?: number }
        return { downloads: d.downloads || d.items || [], total: d.total || 0 }
      }),
  },

  // ════════ System ════════
  system: {
    disk: () => http.get<DiskInfo>('/api/system/disk').then((r) => r.data),
    logs: (file: 'app' | 'scraper' | 'downloaders' | 'actor_profile', limit = 300, filter = '') =>
      http.get<{ lines: string[]; total: number; file: string; error?: string }>('/api/system/logs', { params: { file, limit, filter } }).then((r) => r.data),
  },

  // ════════ Notify ════════
  notify: {
    test: () => http.post<{ ok: boolean; results: NotifyTestResult }>('/api/notify/test').then((r) => r.data),
  },

  // ════════ Collections 收藏分组（F13）════════
  collections: {
    list: () => http.get<{ collections: { id: number; name: string; icon: string; sort_order: number; task_count: number }[] }>('/api/collections').then((r) => {
      const d = r.data as unknown
      if (Array.isArray(d)) return { collections: d as { id: number; name: string; icon: string; sort_order: number; task_count: number }[] }
      return { collections: (d as { collections?: unknown[] }).collections || [] }
    }),
    create: (name: string, _icon?: string) => http.post('/api/collections', { name }).then((r) => r.data),
    remove: (id: number) => http.delete<ApiOk>(`/api/collections/${id}`).then((r) => r.data),
    addTask: (collectionId: number, taskId: number) => http.post<ApiOk>(`/api/collections/${collectionId}/tasks/${taskId}`).then((r) => r.data),
    removeTask: (collectionId: number, taskId: number) => http.delete<ApiOk>(`/api/collections/${collectionId}/tasks/${taskId}`).then((r) => r.data),
    tasks: (collectionId: number, inLibrary?: boolean) => http.get<{ tasks: Task[] }>(`/api/collections/${collectionId}/tasks`, { params: { in_library: inLibrary } }).then((r) => {
      const d = r.data as unknown as { tasks?: Task[]; items?: Task[] }
      return { tasks: d.tasks || d.items || [] }
    }),
  },

  // ════════ Tasks 编辑（F20）════════
  v2: {
    tasks: (params: {
      status?: string; list_source_id?: number; actor?: string; tag?: string; date_from?: string; date_to?: string;
      min_rating?: number; in_library?: boolean; sort?: string; limit?: number; offset?: number;
    }) => http.get<{ tasks: Task[]; total: number }>('/api/v2/tasks', { params }).then((r) => r.data),
    searchFts: (q: string, limit = 48) =>
      http.get<{ tasks: Task[]; total: number; engine: string }>('/api/v2/tasks/search-fts', { params: { q, limit } }).then((r) => r.data),
    analytics: () =>
      http.get<{ top_actors: { name: string; count: number }[]; top_tags: { name: string; count: number }[]; top_makers: { name: string; count: number }[]; rating_dist: { bucket: string; count: number }[]; download_stats: Record<string, number>; daily_added: { day: string; count: number }[] }>('/api/v2/dashboard/analytics').then((r) => r.data),
    similar: (taskId: number) =>
      http.get<{ tasks: Task[]; total: number }>(`/api/v2/tasks/${taskId}/similar`).then((r) => r.data),
  },

  // ════════ View Status（Phase 2：viewed/browsed/want 三态）════════

  // ════════ Actors（Phase 2）════════

  // ════════ Rankings（Phase 2）════════
  rankingsNew: {
    list: (type: 'daily' | 'weekly' | 'monthly' | 'actor', date?: string) =>
      http.get<Ranking[]>(`/api/rankings/${type}`, { params: { date } }).then((r) => r.data),
    dates: () => http.get<Record<string, string[]>>('/api/rankings/types/dates').then((r) => r.data),
    batchAdd: (rankingIds: number[]) =>
      http.post<{ ok: boolean; added: number; skipped: number }>('/api/rankings/batch-add-tasks', { ranking_ids: rankingIds }).then((r) => r.data),
  },

  // ════════ Aggregate（Phase 2：多源元数据补充）════════

  // ════════ Subscriptions（Phase 3：多维订阅）════════
  subscriptions: {
    list: (enabled?: boolean) =>
      http.get<unknown[]>('/api/subscriptions', { params: { enabled } }).then((r) => r.data),
    create: (body: Record<string, unknown>) =>
      http.post<unknown>('/api/subscriptions', body).then((r) => r.data),
    get: (id: number) => http.get<unknown>(`/api/subscriptions/${id}`).then((r) => r.data),
    update: (id: number, body: Record<string, unknown>) =>
      http.put<unknown>(`/api/subscriptions/${id}`, body).then((r) => r.data),
    delete: (id: number) => http.delete<{ ok: boolean }>(`/api/subscriptions/${id}`).then((r) => r.data),
    toggle: (id: number) => http.post<{ ok: boolean; enabled: boolean }>(`/api/subscriptions/${id}/toggle`).then((r) => r.data),
    /** 启动「全部补齐作品」后台任务（串行爬取所有订阅演员的作品） */
    fillAllWorks: (waitLimitMin: number) =>
      http.post<{ ok: boolean; message: string }>('/api/subscriptions/fill-all-works', { wait_limit_min: waitLimitMin }).then((r) => r.data),
    /** 查询「全部补齐作品」任务进度 */
    fillWorksStatus: () =>
      http.get<{ running: boolean; total: number; idx: number; current_actor_id: number | null; current_name: string | null; done: number; skipped: number; failed: number; wait_limit_min: number; last_summary: string | null }>('/api/subscriptions/fill-works-status').then((r) => r.data),
  },

  // ════════ New Releases（订阅巡检发现的新作品）════════
  newReleases: {
    list: (params?: { actor_id?: number; unread_only?: boolean; limit?: number }) =>
      http.get<{ items: NewRelease[]; total: number }>('/api/new-releases', { params }).then((r) => r.data),
    markRead: (id: number) =>
      http.post<{ ok: boolean }>(`/api/new-releases/${id}/read`).then((r) => r.data),
    addToLibrary: (id: number) =>
      http.post<{ ok: boolean; task_id?: number }>(`/api/new-releases/${id}/add-to-library`).then((r) => r.data),
    checkNow: (actorId: number) =>
      http.post<{ ok: boolean; result: Record<string, unknown> }>(`/api/new-releases/check-now/${actorId}`).then((r) => r.data),
    checkAll: () =>
      http.post<{ ok: boolean; result: Record<string, unknown> }>('/api/new-releases/check-all').then((r) => r.data),
  },

  // ════════ Media Server（Emby）════════
  mediaServer: {
    check: (videoCode: string) =>
      http.get<{ video_code: string; in_library: boolean }>(`/api/media-server/check/${videoCode}`).then((r) => r.data),
    test: () =>
      http.get<{ ok: boolean; message: string }>('/api/media-server/test').then((r) => r.data),
    sync: (limit = 200, force = false) =>
      http.post<{ ok: boolean; checked: number; in_library: number; failed: number }>(
        '/api/media-server/sync', null, { params: { limit, force } }
      ).then((r) => r.data),
  },

  // ════════ Insights（Phase 3：数据洞察/月报）════════

  // ════════ Notify（Phase 3：通知测试）════════

  // ════════ Scheduler（Phase 3：调度状态）════════

  // ════════ AI（Phase 4：翻译/标签/摘要/增强；V3：耳语情话）════════
  ai: {
    whisper: (taskId: number, tone: 0 | 1 | 2, night: boolean) =>
      http.post<{ ok: boolean; line: string }>('/api/ai/whisper', { task_id: taskId, tone, night }, { timeout: 8000 })
        .then((r) => r.data),
    test: () =>
      http.post<{ ok: boolean; message: string }>('/api/ai/test', {}, { timeout: 30000 }).then((r) => r.data),
  },

  // ════════ Content Filter（Phase 4：过滤规则）════════

  // ════════ Media Server（Phase 4：Emby/Jellyfin 在库）════════

  // ════════ Images（Phase 4：高清图文件服务）════════

  // ════════ Favorites/Collections（Phase 4：RESTful 收藏分组）════════

  // ════════ Downloaders（Phase 5：磁力推送）════════

  // ════════ Downloads（Phase 5：下载历史）════════

  // ════════ Settings（Phase 5：配置中心）════════

  // ════════ Dashboard（Phase 5：聚合统计）════════

  // ════════ V2（Phase 5：多维筛选/相似/分析）════════

  // ════════ Drive115（Phase 6：115网盘）════════

  // ════════ Magnet Search（Phase 6：多源搜索）════════

  // ════════ Settings (original AVDB) ════════
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
    testProxy: (proxy: string) =>
      http.post<{ ok: boolean; message: string }>('/api/settings/test-proxy', { proxy }).then((r) => r.data),
  },

  // ════════ Images ════════
  images: {
    thumbnails: (taskId: number) =>
      http.get<ThumbnailsResponse>(`/api/images/thumbnails/${taskId}`).then((r) => r.data),
    downloadHires: (taskId: number) =>
      http.post<{ ok: boolean; message: string; downloaded: { cover: boolean; thumbnails: number; total_found: number } }>(
        `/api/images/hires/download-hires/${taskId}`,
      ).then((r) => r.data),
    /** 检查是否有本地高清预览图缓存 */
    hasLocalThumbs: (taskId: number) =>
      http.get<{ has_local: boolean; count: number }>(`/api/images/hires/has-local-thumbs/${taskId}`).then((r) => r.data),
    /** 获取海报索引（自动检测+手动设置的结果） */
    posterIndex: (taskId: number) =>
      http.get<{ poster_index: number }>(`/api/images/hires/poster-index/${taskId}`).then((r) => r.data),
    /** 手动选择海报：0=gallery-1, 1=gallery-2, 2=gallery-3... */
    setPoster: (taskId: number, index: number) =>
      http.post<{ ok: boolean; message: string }>(`/api/images/hires/set-poster/${taskId}/${index}`).then((r) => r.data),
    /** 从远程缩略图直接设为海报（无需本地缓存）：下载远程图 → poster.jpg + 更新 poster_url */
    setPosterRemote: (taskId: number, index: number) =>
      http.post<{ ok: boolean; message: string }>(`/api/images/hires/set-poster-remote/${taskId}/${index}`).then((r) => r.data),
    /** 启动串行队列：逐个处理任务（下载图片+提取磁力） */
    queueStart: (taskIds: number[]) =>
      http.post<{ ok: boolean; message: string; total: number }>(`/api/images/hires/queue/start`, taskIds).then((r) => r.data),
    /** 查询串行队列状态 */
    queueStatus: () =>
      http.get<{
        running: boolean; total: number; current: number; current_task_id: number | null;
        current_video_code: string | null; stage: string; done: number[]; failed: number[]
      }>(`/api/images/hires/queue/status`).then((r) => r.data),
  },
}

/** 本地缓存高清预览图文件 URL（download-hires 下载的） */
export const thumbFileUrl = (taskId: number, index: number) => `/api/images/hires/thumb-file/${taskId}/${index}`

/** gallery-1竖→它就是海报, gallery-1横→gallery-2是海报（自动检测+可手动设置） */
export const coverFileUrl = (taskId: number) => `/api/images/hires/poster-file/${taskId}`

/** 背景图：优先 backdrop.jpg，回退 gallery-1.jpg */
export const backdropUrl = (taskId: number) => `/api/images/hires/backdrop-file/${taskId}`
