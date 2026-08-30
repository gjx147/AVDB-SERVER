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
  DownloadRecord, DiskInfo, NotifyTestResult, NewRelease, Subscription,
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
    // 保留原错误对象（err.response/status 对下游可用），只统一 message 文本
    err.message = detail
    return Promise.reject(err)
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
    batchView: (task_ids: number[], status = 'viewed') =>
      http.post<{ ok: boolean; updated: number }>('/api/tasks/batch-view', { task_ids, status }).then((r) => r.data),
    batchPush: (task_ids: number[]) =>
      http.post<{ ok: boolean; pushed: number; skipped: number }>('/api/tasks/batch-push', { task_ids }).then((r) => r.data),

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
    /** 分页拉取演员（返回 items + total，供演员库翻页） */
    listPage: (page: number, pageSize: number, withAvatar?: boolean, followed?: boolean, keyword?: string) =>
      http.get<{ total: number; page: number; page_size: number; items: Actor[] }>('/api/actors', {
        params: { page, page_size: pageSize, with_avatar: withAvatar, followed, q: keyword || undefined },
      }).then((r) => {
        const d = r.data as unknown
        if (Array.isArray(d)) return { items: d as Actor[], total: (d as Actor[]).length }
        return { items: (d as { items?: Actor[] }).items || [], total: (d as { total?: number }).total ?? 0 }
      }),
    /** 自动翻页拉取全部演员（后端 page_size 上限 200；订阅页建头像映射用） */
    // T15: 全量演员列表缓存 5 分钟（订阅页每次进入不再全量重拉）
    _listAllCache: null as Actor[] | null,
    _listAllCacheTs: 0,
    listAll: (withAvatar?: boolean, pageSize = 200): Promise<Actor[]> => {
      const now = Date.now()
      if (api.actors._listAllCache && now - api.actors._listAllCacheTs < 300000) {
        return Promise.resolve(api.actors._listAllCache)
      }
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
        api.actors._listAllCache = out
        api.actors._listAllCacheTs = Date.now()
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
    crawlWorks: (actorId: number, maxCoStar = 0, soloOnly = false) =>
      http.post<{ ok: boolean; pid: number; mode: string; actor_url: string }>(`/api/actors/${actorId}/crawl-works`, { max_co_star: maxCoStar, solo_only: soloOnly }).then((r) => r.data),
    refreshProfile: (actorId: number) =>
      http.post<{ ok: boolean; source: string | null; fields?: Record<string, string | null>; message?: string; locked_skipped?: string[] }>(`/api/actors/${actorId}/refresh-profile`).then((r) => r.data),
    profileQueueStatus: () =>
      http.get<{ pending: number; fetched: number; failed: number }>('/api/actors/profile-queue/status').then((r) => r.data),
    /** 一键提取全部待抓演员信息（后台任务） */
    extractProfiles: () =>
      http.post<{ ok: boolean; message: string }>('/api/actors/extract-profiles').then((r) => r.data),
    /** 一键提取任务进度 */
    extractProfilesStatus: () =>
      http.get<{ running: boolean; total: number; idx: number; current_name: string | null; done: number; skipped: number; failed: number; last_summary: string | null }>('/api/actors/extract-profiles/status').then((r) => r.data),
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
    logs: (file: 'app' | 'scraper' | 'downloaders' | 'actor_profile' | 'ai' | 'subscriptions' | 'crawl_console' | 'magnet' | 'organize' | 'emby_sync' | 'actor_works_batch', limit = 300, filter = '') =>
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
    searchFts: (q: string, limit = 48, offset = 0) =>
      http.get<{ tasks: Task[]; total: number; engine: string }>('/api/v2/tasks/search-fts', { params: { q, limit, offset } }).then((r) => r.data),
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
      http.get<Subscription[]>('/api/subscriptions', { params: { enabled } }).then((r) => r.data),
    create: (body: Record<string, unknown>) =>
      http.post<unknown>('/api/subscriptions', body).then((r) => r.data),
    get: (id: number) => http.get<unknown>(`/api/subscriptions/${id}`).then((r) => r.data),
    update: (id: number, body: Record<string, unknown>) =>
      http.put<unknown>(`/api/subscriptions/${id}`, body).then((r) => r.data),
    delete: (id: number) => http.delete<{ ok: boolean }>(`/api/subscriptions/${id}`).then((r) => r.data),
    toggle: (id: number) => http.post<{ ok: boolean; enabled: boolean }>(`/api/subscriptions/${id}/toggle`).then((r) => r.data),
    /** 启动「全部补齐作品」后台任务（串行爬取所有订阅演员的作品） */
    fillAllWorks: (waitLimitMin: number, maxCoStar = 0) =>
      http.post<{ ok: boolean; message: string }>('/api/subscriptions/fill-all-works', { wait_limit_min: waitLimitMin, max_co_star: maxCoStar }).then((r) => r.data),
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
    fullSync: () => http.post<{ ok: boolean; message: string }>('/api/media-server/full-sync').then((r) => r.data),
    fullSyncStatus: () => http.get<{ ok: boolean; running: boolean; total: number; done: number; checked: number; in_library: number; failed: number }>(
      '/api/media-server/full-sync-status').then((r) => r.data),

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
  top250: {
    query: (kind: number, force = false, kindEnd?: number) => http.post<{ ok: boolean; kinds: number[]; label: string; grand_total: number; summary: { kind: number; label: string; total: number; no_code: number; in_library_synced: number }[] }>('/api/top250/query', { kind, force, kind_end: kindEnd ?? null }).then((r) => r.data),
    import: (kind: number, csvFile: File, magnetFile: File) => {
      const fd = new FormData()
      fd.append('kind', String(kind))
      fd.append('csv_file', csvFile)
      fd.append('magnet_file', magnetFile)
      return http.post<{ ok: boolean; label: string; csv_rows: number; magnet_rows: number; magnet_matched: number; in_library_synced: number }>('/api/top250/import', fd).then((r) => r.data)
    },
    list: (kind: number, q = '', status = 'all') =>
      http.get<{ ok: boolean; label: string; total: number; items: { id: number; kind: number; rank: number; number: string; name: string; date: string | null; poster_url: string | null; magnet_version: string | null; task_id: number | null; in_library: boolean; updated_at: string | null; prev_rank: number | null; prev_date: string | null }[];
    snapshot: string | null }>(`/api/top250/list`, { params: { kind, q, status } }).then((r) => r.data),
    crawlMissing: (kind: number) => http.post<{ ok: boolean; queued: number; message: string }>('/api/top250/crawl-missing', { kind }).then((r) => r.data),
    addTask: (number: string, kind: number) => http.post<{ ok: boolean; message: string }>(`/api/top250/${number}/add-task?kind=${kind}`).then((r) => r.data),
  },
  cd2Rename: {
    renameAll: () => http.post<{ ok: boolean; total: number; organized: number; results: { video_code: string | null; ok: boolean; message: string }[] }>('/api/downloaders/rename-all').then((r) => r.data),
  },
  javdbLogin: {
    start: () => http.post<ApiOk>('/api/javdb-login/start').then((r) => r.data),
    screenshot: () => http.get<{ ok: boolean; image: string }>('/api/javdb-login/screenshot').then((r) => r.data),
    submit: (d: { username: string; password: string; captcha?: string }) =>
      http.post<{ ok: boolean; message: string }>('/api/javdb-login/submit', d).then((r) => r.data),
    status: () => http.get<{ running: boolean; logged_in: boolean | null; message: string; elapsed: number }>('/api/javdb-login/status').then((r) => r.data),
    cancel: () => http.post<ApiOk>('/api/javdb-login/cancel').then((r) => r.data),
  },

  // ════════ Notifications（F2 通知中心）════════
  notifications: {
    list: (event?: string) =>
      http.get<{ items: { id: number; event: string; title: string; body: string; channel: string; ok: boolean; message: string; created_at: string }[] }>(
        '/api/notifications', { params: { event } }).then((r) => r.data),
    events: () => http.get<{ events: string[] }>('/api/notifications/events').then((r) => r.data),
    test: () => http.post<Record<string, boolean | string>>('/api/notifications/test').then((r) => r.data),
    dnd: () => http.get<{ dnd_start: string; dnd_end: string }>('/api/notifications/dnd').then((r) => r.data),
    setDnd: (d: { dnd_start: string; dnd_end: string }) =>
      http.put<ApiOk>('/api/notifications/dnd', d).then((r) => r.data),
  },

  // ════════ Transfer（F1 导入导出）════════
  exportTasksCsv: () => http.get<Blob>('/api/export/tasks.csv', { responseType: 'blob' }).then((r) => r.data),
  exportSubscriptions: () =>
    http.get<{ subscriptions: { name: string; sub_type: string; actor_name: string | null; auto_add: boolean; enabled: boolean; check_interval_hours: number }[] }>(
      '/api/export/subscriptions.json').then((r) => r.data),
  importCodes: (codes: string[]) =>
    http.post<{ ok: boolean; added: number; skipped: number }>('/api/import/codes', { codes }).then((r) => r.data),
  activityHeatmap: (days = 180) =>
    http.get<{ days: Record<string, { favorites: number; downloads: number }>; total_days: number }>(
      '/api/insights/activity-heatmap', { params: { days } }).then((r) => r.data),
  recommendations: () =>
    http.get<{ items: { task_id: number; video_code: string | null; title: string | null; rating: number | null; poster_url: string | null; score: number | null; match: string[] }[]; reason: string }>(
      '/api/insights/recommendations').then((r) => r.data),
  recommendReason: (taskId: number) =>
    http.post<{ reason: string; cached: boolean }>('/api/ai/recommend-reason', { task_id: taskId }).then((r) => r.data),
  agentChat: (messages: { role: string; content: string }[], sessionId?: number | null) =>
    http.post<{ ok: boolean; type: string; content?: string; items?: unknown[]; query?: Record<string, unknown>; tool?: string; tool_cn?: string; args?: Record<string, unknown>; reason?: string; preview?: string; token?: string; steps?: { tool: string; reason?: string; content?: string }[] }>(
      '/api/ai/agent', { messages, session_id: sessionId ?? undefined }).then((r) => r.data),
  chatSessions: () =>
    http.get<{ ok: boolean; items: { id: number; title: string; created_at?: string }[] }>('/api/ai/sessions').then((r) => r.data),
  chatCreateSession: (title?: string) =>
    http.post<{ ok: boolean; session: { id: number; title: string } }>('/api/ai/sessions', { title: title || '' }).then((r) => r.data),
  chatDeleteSession: (id: number) =>
    http.delete<{ ok: boolean }>(`/api/ai/sessions/${id}`).then((r) => r.data),
  chatSessionMessages: (id: number) =>
    http.get<{ ok: boolean; messages: { role: string; content: string }[] }>(`/api/ai/sessions/${id}/messages`).then((r) => r.data),
  agentCommand: (command: string, argText: string) =>
    http.post<{ ok: boolean; type: string; content?: string; items?: unknown[]; steps?: unknown[]; token?: string; tool?: string; tool_cn?: string; preview?: string; args?: Record<string, unknown>; reason?: string }>(
      '/api/ai/agent/command', { command, arg_text: argText }).then((r) => r.data),
  aiUsage: (days = 7) =>
    http.get<{ ok: boolean; days: number; total: { calls: number; prompt_tokens: number; completion_tokens: number; cache_hits: number; cache_rate: number; est_cost: number }; daily: { date: string; calls: number; prompt_tokens: number; completion_tokens: number; avg_ms: number; cache_hits: number }[]; by_type: { task_type: string; calls: number; prompt_tokens: number; completion_tokens: number }[] }>(
      `/api/ai/usage?days=${days}`).then((r) => r.data),
  crawlLogFiles: () =>
    http.get<{ ok: boolean; items: { file: string; name: string; exists: boolean; size: number; mtime: number | null }[] }>(
      '/api/crawl/log-files').then((r) => r.data),
  crawlLogTail: (file: string, lines = 80) =>
    http.get<{ ok: boolean; file: string; name: string; items: string[] }>(
      `/api/crawl/logs?file=${encodeURIComponent(file)}&lines=${lines}`).then((r) => r.data),
  progressLite: () =>
    http.get<{ ok: boolean; running: boolean; pid?: number; mode?: string; log: string[] }>(
      '/api/ai/progress-lite').then((r) => r.data),
  agentConfirm: (token: string) =>
    http.post<{ ok: boolean; result?: { ok: boolean; message?: string } }>(
      '/api/ai/agent/confirm', { token }).then((r) => r.data),
  publicShareSummary: (token: string) =>
    http.get<{ ok: boolean; kind?: string; name?: string; count?: number; top_tags?: string[]; summary?: string }>(
      `/api/shares/public-summary/${token}`).then((r) => r.data),
  aiAsk: (question: string, history: { role: string; content: string }[] = []) =>
    http.post<{ ok: boolean; question: string; query: Record<string, unknown>; total: number; engine: string; items: { task_id: number; video_code: string | null; title: string | null; rating: number | null; poster_url: string | null; tags: string | null; actors: string | null }[] }>(
      '/api/ai/ask', { question, history }).then((r) => r.data),
  actorInsights: (actorId: number) =>
    http.get<{ years: { year: string; count: number }[]; co_stars: { name: string; count: number }[] }>(
      `/api/actors/${actorId}/profile-insights`).then((r) => r.data),
  quota115: () =>
    http.get<{ ok: boolean; total?: number | null; used?: number | null; remain?: number | null; message?: string }>(
      '/api/drive115/quota').then((r) => r.data),
  dedupeTasks: (dryRun = true) =>
    http.post<{ ok: boolean; dry_run: boolean; groups: number; to_delete?: number; deleted?: number; plan?: { code: string; keep_id: number; dup_ids: number[]; dup_count: number }[] }>(
      `/api/tasks/dedupe?dry_run=${dryRun}`).then((r) => r.data),
  yearlyReport: (year?: number) =>
    http.get<{ year: number; stats: { added: number; downloads: number; favorites: number }; top_actors: { name: string; count: number }[]; top_tags: { name: string; count: number }[]; top_makers: { name: string; count: number }[]; monthly: number[] }>(
      '/api/insights/yearly-report', { params: { year } }).then((r) => r.data),
  // ── N1-N8 分析中心 ──
  libraryHealth: () =>
    http.get<{ ok: boolean; score: number; total: number; rates: Record<string, number>; fix_top: { task_id: number; video_code: string | null; title: string | null; rating: number | null }[] }>(
      '/api/insights/library-health').then((r) => r.data),
  profileReport: () =>
    http.get<{ ok: boolean; total: number; avg_rating: number | null; hhi: number | null; top_actors: { name: string; score: number }[]; top_tags: { name: string; score: number }[]; top_makers: { name: string; score: number }[] }>(
      '/api/insights/profile').then((r) => r.data),
  downloadStats: (days = 30) =>
    http.get<{ ok: boolean; days: number; items: { downloader: string; total: number; completed: number; failed: number; success_rate: number; avg_hours: number | null; top_errors: { msg: string; count: number }[] }[] }>(
      '/api/insights/download-stats', { params: { days } }).then((r) => r.data),
  crawlEfficiency: (days = 14) =>
    http.get<{ ok: boolean; days: number; trend: { date: string; total: number; errors: number }[]; totals: { type: string; total: number; errors: number; success_rate: number }[]; top_errors: { msg: string; count: number }[] }>(
      '/api/insights/crawl-efficiency', { params: { days } }).then((r) => r.data),
  notificationHealth: (days = 30) =>
    http.get<{ ok: boolean; days: number; items: { channel: string; total: number; ok: number; fail_rate: number }[]; recent_failures: { channel: string; message: string; time: string }[] }>(
      '/api/insights/notification-health', { params: { days } }).then((r) => r.data),
  rankingTrends: (days = 14) =>
    http.get<{ ok: boolean; days: number; top_risers: { code: string; days: number; best: number; from: number; to: number; change: number }[]; top_on_chart: { code: string; days: number; best: number }[] }>(
      '/api/insights/ranking-trends', { params: { days } }).then((r) => r.data),
  actorStatusSummary: (ids: string) =>
    http.get<{ ok: boolean; items: Record<number, { last_release: string; days_since: number | null }> }>(
      `/api/actors/status-summary?ids=${ids}`).then((r) => r.data),
  importMagnets: (text: string) =>
    http.post<{ ok: boolean; added: number; skipped: number; total: number; message?: string }>(
      '/api/import/magnets', { text }).then((r) => r.data),
  wishlistGaps: (limit = 50) =>
    http.get<{ ok: boolean; total: number; items: { task_id: number; video_code: string | null; title: string | null; rating: number | null; has_magnet: boolean; in_library: boolean }[] }>(
      '/api/tasks/wishlist-gaps', { params: { limit } }).then((r) => r.data),
  systemStatus: () =>
    http.get<{ ok: boolean; jobs: { id: string; next_run: string }[]; queue: { pending: number; failed: number }; active_downloads: number; errors_24h: { crawl: number; notify: number }; backups: { name: string; size_mb: number }[]; server_time: string }>(
      '/api/system/status').then((r) => r.data),
  ratingTrends: (taskId: number) =>
    http.get<{ ok: boolean; task_id: number; points: { date: string; rating: number }[] }>(
      `/api/insights/rating-trends/${taskId}`).then((r) => r.data),
  batchTags: (limit = 5) =>
    http.post<{ ok: boolean; processed: number; done: number; errors: number }>(
      `/api/ai/batch-tags?limit=${limit}`).then((r) => r.data),
  mediaAudit: () =>
    http.post<{ ok: boolean; message?: string; emby_total?: number; local_total?: number; emby_only?: string[]; dup_codes?: { code: string; count: number }[]; in_lib_missing_from_emby?: string[] }>(
      '/api/media-server/audit').then((r) => r.data),
  torrentHealth: () =>
    http.get<{ ok: boolean; total: number; message?: string; items?: { dl_id: number; video_code: string | null; seeder: number; leecher: number; progress: number; healthy: boolean }[] }>(
      '/api/downloads/torrent-health').then((r) => r.data),
  s3Status: () =>
    http.get<{ ok: boolean; configured: boolean; remote: { ok: boolean; items?: { key: string; size_mb: number }[]; message?: string } }>(
      '/api/system/s3-status').then((r) => r.data),
  s3Upload: () =>
    http.post<{ ok: boolean; key?: string; message?: string }>('/api/system/s3-upload').then((r) => r.data),
  uploadSubtitle: (dlId: number, file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return http.post<{ ok: boolean; path?: string; name?: string }>(`/api/organize/subtitle/${dlId}`, fd).then((r) => r.data)
  },
  shares: {
    create: (kind: string, refId: number, days = 7, note = '') =>
      http.post<{ ok: boolean; token: string; url: string; expires_at: string }>(
        '/api/shares', { kind, ref_id: refId, days, note }).then((r) => r.data),
    list: () =>
      http.get<{ ok: boolean; items: { token: string; kind: string; ref_id: number; note: string; expires_at: string; url: string }[] }>(
        '/api/shares').then((r) => r.data),
    remove: (token: string) =>
      http.delete<{ ok: boolean }>(`/api/shares/${token}`).then((r) => r.data),
  },
  publicShare: (token: string) =>
    http.get<{ ok: boolean; kind: string; title: string; items: { video_code: string | null; title: string | null; rating: number | null; actors: string | null; poster_url: string | null; rank?: number; score?: number }[] }>(
      `/api/public/share/${token}`).then((r) => r.data),
  rules: {
    list: () => http.get<{ ok: boolean; items: { id: number; name: string; conditions_json: string; actions_json: string; enabled: boolean; hit_count: number; last_run_at: string | null }[] }>('/api/rules').then((r) => r.data),
    create: (body: { name: string; conditions: Record<string, unknown>; actions: Record<string, unknown>; enabled?: boolean }) =>
      http.post<{ ok: boolean; id: number }>('/api/rules', body).then((r) => r.data),
    update: (id: number, body: Record<string, unknown>) =>
      http.put<{ ok: boolean }>(`/api/rules/${id}`, body).then((r) => r.data),
    remove: (id: number) => http.delete<{ ok: boolean }>(`/api/rules/${id}`).then((r) => r.data),
    runNow: () => http.post<{ ok: boolean; rules: number; hits: number }>('/api/rules/run-now').then((r) => r.data),
  },
  downloadStrategy: {
    get: () => http.get<{ ok: boolean; strategy: Record<string, unknown> }>('/api/system/download-strategy').then((r) => r.data),
    set: (strategy: Record<string, unknown>) =>
      http.put<{ ok: boolean }>('/api/system/download-strategy', { strategy }).then((r) => r.data),
  },
  seriesProgress: (limit = 15) =>
    http.get<{ ok: boolean; items: { series: string; total: number; viewed: number; faved: number; avg_rating: number | null; viewed_rate: number }[]; total_series: number }>(
      '/api/insights/series-progress', { params: { limit } }).then((r) => r.data),
  organize: {
    config: () =>
      http.get<{ organize_enabled: string; organize_target_dir: string; organize_naming: string; organize_keep_source: string }>(
        '/api/organize/config').then((r) => r.data),
    setConfig: (c: Record<string, string>) =>
      http.put<ApiOk>('/api/organize/config', c).then((r) => r.data),
    runAll: () =>
      http.post<{ ok: boolean; total: number; organized: number }>('/api/organize/run-all').then((r) => r.data),
    undo: (dlId: number) =>
      http.post<{ ok: boolean; removed: number }>(`/api/organize/undo/${dlId}`).then((r) => r.data),
  },

  // ════════ Crawl Health / Calendar（F3/F4）════════
  crawlHealth: () =>
    http.get<{ total_24h: number; error_24h: number; success_rate: number; reasons: Record<string, number>; trend: { date: string; total: number; errors: number }[] }>(
      '/api/crawl/health').then((r) => r.data),
  crawlDiagnostics: () =>
    http.get<{ proxy: string; javdb: string; javdb_url: string }>('/api/crawl/diagnostics').then((r) => r.data),
  releaseCalendar: (month?: string) =>
    http.get<{ month: string; days: Record<string, number> }>('/api/new-releases/calendar', { params: { month } }).then((r) => r.data),

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

/**
 * 给图片 URL 追加鉴权 token（query 参数 token=<JWT>）。
 *
 * 后端 /api/images/* 要求鉴权；浏览器 <img> 无法携带 Authorization header，
 * 只能通过 query 传 token（参数名固定为 token）。URL 已含 query 时用 & 连接；
 * 未登录（无 token）时原样返回，保证登录页等免鉴权场景不受影响。
 */
export const withImageAuth = (url: string): string => {
  if (!url) return url
  const token = localStorage.getItem('apiToken')
  if (!token) return url
  const sep = url.includes('?') ? '&' : '?'
  return `${url}${sep}token=${encodeURIComponent(token)}`
}

/** 本地缓存高清预览图文件 URL（download-hires 下载的） */
export const thumbFileUrl = (taskId: number, index: number) => `/api/images/hires/thumb-file/${taskId}/${index}`

/** gallery-1竖→它就是海报, gallery-1横→gallery-2是海报（自动检测+可手动设置） */
export const coverFileUrl = (taskId: number) => `/api/images/hires/poster-file/${taskId}`

/** 背景图：优先 backdrop.jpg，回退 gallery-1.jpg */
export const backdropUrl = (taskId: number) => `/api/images/hires/backdrop-file/${taskId}`
