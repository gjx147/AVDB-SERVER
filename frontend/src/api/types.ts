/**
 * TypeScript 类型 —— 严格对应后端 schemas.py 的 Pydantic 模型
 * 状态机：status = 'pending'(待处理) | 'visited'(已入库) | 'failed'(失败)
 */

// ── List Sources ──
export interface ListSource {
  id: number
  list_code: string
  list_path: string
  list_params: string
  max_pages: number
  last_scanned_page: number
  last_scanned_at: string | null
  created_at: string | null
}

export interface ListSourceWithStats extends ListSource {
  pending_count: number
  visited_count: number
  failed_count: number
}

export interface ListSourceCreate {
  list_code: string
  list_path?: string
  list_params?: string
  max_pages?: number
}

// ── Tasks ──
export interface Task {
  id: number
  list_source_id: number
  url: string
  status: 'pending' | 'visited' | 'failed'
  retry_count: number
  best_magnet: string | null
  magnets_json: string | null
  video_code: string | null
  title: string | null
  poster_url: string | null
  thumbnail_urls: string | null
  synopsis: string | null
  description: string | null
  actors: string | null
  tags: string | null
  release_date: string | null
  duration: string | null
  director: string | null
  maker: string | null
  label: string | null
  series: string | null
  rating: string | null
  file_size: string | null
  is_favorite: 0 | 1
  favorite_at: string | null
  note: string | null
  error_message: string | null
  created_at: string | null
  updated_at: string | null
  download_status?: string | null  // null=未下载, pushed, downloading, completed, failed
}

export interface Magnet {
  link: string
  size?: string
  name?: string
  priority?: number  // 0=最高优先级（命中最优后缀），未命中为后缀数量
  // 兼容 scraper 原始存储的 {magnet,name} 结构
  magnet?: string
  [k: string]: unknown
}

export interface TaskDetail extends Task {
  magnets: Magnet[] | null
}

export interface TaskStats {
  list_source_id: number | null
  list_code: string | null
  pending: number
  visited: number
  failed: number
}

// ── Actors ──
export interface Actor {
  id: number
  name: string
  name_en: string | null
  avatar_url: string | null
  avatar_local: string | null
  detail_url: string | null
  gender: string | null
  birth_date: string | null
  height: string | null
  cup: string | null
  measurements: string | null
  debut_date: string | null
  description: string | null
  tags: string | null
  movie_count: number
  local_movie_count: number
  is_followed?: number  // F9: 关注标记
  created_at: string | null
}

export interface ActorMovie {
  id: number
  video_code: string | null
  title: string | null
  poster_url: string | null
  rating: string | null
  status: string | null
  is_favorite: 0 | 1
  detail_url: string | null
}

// ── Rankings ──
export type RankType = 'daily' | 'weekly' | 'monthly' | 'actor'

export interface Ranking {
  id: number
  rank_type: string
  rank_date: string
  task_id: number | null
  video_code: string | null
  title: string | null
  cover_url: string | null
  rank_position: number
  score: string | null
  views: number
  detail_url: string | null
  is_in_library: boolean
  created_at: string | null
}

// ── Dashboard ──
export interface DashboardStats {
  total_tasks: number
  visited_tasks: number
  pending_tasks: number
  failed_tasks: number
  favorite_count: number
  actor_count: number
  total_magnets: number
  db_size_mb: number
}

export interface MonthlyStat {
  month: string
  count: number
}

// ── Crawl ──
export interface CrawlStatus {
  running: boolean
  paused: boolean
  list_code: string | null
  crawl_type: string | null
  progress: Record<string, unknown>
}

export interface CrawlLogLine {
  lines: string[]
  running: boolean
}

// ── Settings ──
export interface Settings {
  crawl_delay_min: number
  crawl_delay_max: number
  max_pages_default: number
  aria2_rpc_url: string
  aria2_token: string
  preferred_suffixes: string
  auto_retry_enabled: boolean
  auto_retry_interval: number
  auto_retry_max_count: number
  ranking_auto_crawl: boolean
  ranking_auto_interval_hours: number
  ranking_types: string
  clouddrive_url: string
  clouddrive_token: string
  clouddrive_username: string
  clouddrive_password: string
  clouddrive_save_path: string
  qbittorrent_url: string
  qbittorrent_username: string
  qbittorrent_password: string
  qbittorrent_save_path: string
  default_downloader: string
  javdb_url: string
  // 通知配置（F3）
  notify_bark_key?: string
  notify_telegram_token?: string
  notify_telegram_chat_id?: string
  notify_webhook_url?: string
  notify_events?: string
}

export type SettingsUpdate = Partial<Settings>

// ── 通用响应 ──
export interface ApiOk {
  ok: boolean
  message?: string
  task_id?: number
}

// ── 图片 ──
export interface ThumbnailsResponse {
  task_id: number
  thumbnails: string[]
  count: number
}

export interface DownloadImagesResult {
  ok: boolean
  downloaded: { cover: boolean; thumbnails: number }
}

// ── Downloads（下载历史）──
export interface DownloadRecord {
  id: number
  task_id: number | null
  magnet: string
  infohash: string | null
  downloader: string | null
  save_path: string | null
  status: 'pushed' | 'downloading' | 'completed' | 'failed'
  progress: number
  total_size: string | null
  added_at: string | null
  completed_at: string | null
  error_message: string | null
  video_code: string | null
  title: string | null
}

// ── System Disk（磁盘信息）──
export interface DiskInfo {
  data: { total_gb: number; used_gb: number; free_gb: number; free_percent: number; error?: string }
  images_size_mb: number
  images_count: number
  db_size_mb: number
}

// ── Notify（通知测试结果）──
export interface NotifyTestResult {
  bark: string
  telegram: string
  webhook: string
}
