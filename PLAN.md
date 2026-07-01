# AVDB-SERVER 实施方案

> **版本**:1.0 | **状态**:规划中 | **日期**:2026-07-01

## 定位

基于 **strivek/javdb-app 的干净分层**做地基,融入 **AVDB 的真实改进**(富元数据 scraper、扩展 schema、调度/通知/下载),移植 **JavdBviewed 的服务端友好功能**(观看状态/新作品监控/洞察/AI),并参考 **Immortal 的成熟产品形态**(多维订阅/媒体服务器/AI Agent/多通知渠道)。复用 AVDB 的 admin-new React 前端。丢弃 AVDB 补丁层。

**技术栈锚点**:FastAPI + **SQLAlchemy ORM**(默认 SQLite,预留 PostgreSQL)+ Playwright + APScheduler。

---

## 一、四个基准工程的取材定位

| 工程 | 角色 | 取什么 |
|---|---|---|
| **strivek/javdb-app** | 干净地基 | 分层结构(backend/magnet_scraper/admin)、main.py/database.py/scraper.py 骨架、Dockerfile 思路 |
| **AVDB** | 功能来源 | 富元数据 scraper(10元组提取)、tasks 30字段+全套表、6个新 router、调度/通知/下载服务(去补丁化) |
| **JavdBviewed** | 服务端友好功能移植 | 观看状态三态、新作品监控、数据洞察、AI翻译、115集成、磁力多源 |
| **Immortal(闭源)** | 产品蓝图参考(无可复用代码) | 多维订阅体系(榜单/演员/综合)、媒体服务器集成、AI Agent、Discord/企微通知、FlareSolverr兜底 |

### 关键考古结论

- **strivek/javdb-app → AVDB** 已通过镜像逆向确证同源(scraper.py 前25行逐字节相同,store.py 注释互相呼应)。AVDB 是 strivek 的功能增强版,但叠加了一层 3600 行的 `backend-patch/` 补丁层(monkey-patch + ASGI 包装)来绕过"镜像冻结"。新工程直接用 strivek 干净代码做地基,把 AVDB 的真实改进作为一等公民写进源码。
- **Immortal** 是闭源 Docker 镜像(`envyafish/immortal`,PostgreSQL + FlareSolverr),无可复用代码,但其产品形态(多维订阅、媒体服务器、AI Agent)是最佳功能蓝图。

---

## 二、工程目录结构

```
AVDB-SERVER/
├── backend/
│   ├── main.py                       # FastAPI 入口:lifespan 启动调度器/看门狗/Playwright池
│   ├── database.py                   # SQLAlchemy 引擎+会话+全部模型(ORM)
│   ├── models/                       # ORM 模型分层
│   │   ├── task.py                   # Task(30字段+view_status+ai缓存)
│   │   ├── actor.py                  # Actor + ActorMovie + ActorSubscription
│   │   ├── ranking.py                # Ranking
│   │   ├── subscription.py           # [新-Immortal参考] 多维订阅(榜单/演员/综合+过滤器)
│   │   ├── download.py               # Download + DownloaderConfig
│   │   ├── collection.py             # Collection + TaskCollection
│   │   ├── content_filter.py         # ContentFilterRule
│   │   ├── insight.py                # MonthlyReport + InsightView
│   │   ├── media_server.py           # [新-Immortal参考] 媒体库状态缓存(Emby/Jellyfin)
│   │   ├── new_release.py            # NewRelease(检测到的新作品)
│   │   ├── magnet_cache.py           # MagnetCache(多源搜索TTL缓存)
│   │   ├── llm_cache.py              # LLM 调用缓存
│   │   ├── log.py                    # CrawlLog
│   │   └── setting.py                # Setting(键值)
│   ├── schemas.py                    # Pydantic 模型
│   ├── deps.py                       # 依赖注入(get_db 认证 分页)
│   ├── auth.py                       # FastAPI OAuth2 + JWT(取代补丁层 token)
│   ├── routers/
│   │   ├── tasks.py                  # 任务 CRUD + 批量删除级联
│   │   ├── list_sources.py           # 列表源
│   │   ├── crawl.py                  # 爬取控制 + 进度 WebSocket
│   │   ├── actors.py                 # 演员档案 + 关注/订阅/拉黑 + 作品
│   │   ├── rankings.py               # 排行榜 + 批量入库
│   │   ├── subscriptions.py          # [新-Immortal] 多维订阅 CRUD
│   │   ├── dashboard.py              # 聚合统计
│   │   ├── downloaders.py            # CD2/qBittorrent/aria2/Transmission 推送
│   │   ├── downloads.py              # 下载历史
│   │   ├── images.py                 # 图片文件服务(抓取下沉scraper)
│   │   ├── favorites.py              # 收藏 + 分组(RESTful 规范)
│   │   ├── v2.py                     # 多维筛选/FTS5/similar
│   │   ├── status.py                 # [新-JavdBviewed] 观看状态三态
│   │   ├── insights.py               # [新-JavdBviewed] 数据洞察/月报
│   │   ├── new_works.py              # [新-JavdBviewed] 新作品监控
│   │   ├── ai.py                     # [新-Immortal] AI Agent(翻译/标签/交互)
│   │   ├── media_server.py           # [新-Immortal] 媒体库对接/在库查询
│   │   ├── settings.py               # 配置 CRUD(密码字段排除)
│   │   ├── drive115.py               # [P2-新] 115 OAuth + 离线
│   │   └── magnet_search.py          # [P2-新] 磁力多源搜索
│   ├── services/                     # 后台服务(补丁层去补丁化)
│   │   ├── scheduler.py              # APScheduler 统一调度中心
│   │   ├── auto_crawl.py             # 定时 scan+extract(修bug)
│   │   ├── auto_ranking.py           # 排行榜爬取
│   │   ├── subscription_monitor.py   # [新-Immortal] 多维订阅巡检+入库
│   │   ├── new_works_monitor.py      # [新-JavdBviewed] 演员新作检测
│   │   ├── download_tracker.py       # 下载进度轮询(补CD2)
│   │   ├── notifier.py               # Bark/TG/Discord/企微/Webhook(Immortal扩充渠道)
│   │   ├── report_generator.py       # 月报(SQL聚合+AI文案)
│   │   ├── ai_service.py             # OpenAI兼容LLM层(缓存/配额/Agent)
│   │   ├── data_aggregator.py        # [新-JavdBviewed] 多源元数据聚合
│   │   ├── media_server_sync.py      # [新-Immortal] Emby/Jellyfin 在库同步
│   │   ├── flaresolverr.py           # [新-Immortal] Cloudflare 兜底代理
│   │   ├── magnet_sources.py         # [P2] 5源适配器
│   │   ├── drive115_client.py        # [P2] 115 API客户端
│   │   ├── browser_pool.py           # [新] Playwright 浏览器实例池(复用,防泄漏)
│   │   └── watchdog.py               # 磁盘/WAL/队列看门狗
│   └── requirements.txt
├── magnet_scraper/                   # AVDB 版 scraper(完整元数据提取)
│   ├── scraper.py                    # 10元组 + ranking/crawl-actor/extract-single
│   ├── store.py                      # 扩展版
│   └── config.py                     # 读 settings 表
├── frontend/                         # 复用 admin-new React 前端
│   └── (复制+调整 api/client.ts 适配规范化端点+加新页面)
├── alembic/                          # [新] 数据库迁移(SQLAlchemy 配套)
├── scripts/
│   └── migrate_from_avdb.py
├── Dockerfile                        # 多阶段构建
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## 三、技术选型

| 层 | 选型 | 理由 |
|---|---|---|
| Web | FastAPI | 沿用 |
| ORM | **SQLAlchemy 2.0**(新) | 预留 PG,模型即文档;AVDB 原生 sqlite3 手写 SQL 不便扩展 |
| 迁移 | **Alembic**(新) | 配合 ORM,取代 AVDB 的 ALTER TABLE 堆叠 |
| DB | SQLite+FTS5(默认)/ PostgreSQL(可选) | NAS 单机用 SQLite;ORM 抽象后切 PG 零改动 |
| 调度 | **APScheduler**(新) | 统一管理所有周期任务,取代 AVDB 5个手写线程 |
| 浏览器 | Playwright + playwright-stealth + **浏览器池**(新) | 复用实例防泄漏;**FlareSolverr 兜底**(Immortal参考) |
| AI | OpenAI 兼容协议 + **Agent 模式**(Immortal参考) | 翻译/标签/摘要 + 可交互 Agent |
| 认证 | OAuth2 + JWT(新) | 取代补丁层 token |
| 前端 | React+TS+Vite+Zustand(复用 admin-new) | 不重写 |
| 通知 | Bark/TG/**Discord/企微**(Immortal扩充) | 多渠道 |
| 下载器 | qBittorrent/CD2/aria2/**Transmission**(Immortal参考) | 多下载器 |
| 部署 | Docker 多阶段 + compose | 沿用 |

---

## 四、数据库模型(ORM,在 AVDB 基础上 + JavdBviewed + Immortal)

**Task**(AVDB 30字段) + 新增:
- `view_status`(viewed/browsed/want)
- `viewed_at`
- `ai_title_translated`
- `ai_tags`
- `media_in_library`(在库状态缓存,Immortal参考)

**新表**:
- `ActorSubscription`(JavdBviewed)+ **`Subscription`(Immortal式多维订阅:类型=榜单/演员/综合,过滤器=发行商/制作商/系列/类型/番号前缀黑名单)**
- `NewRelease`(JavdBviewed 新作品)
- `MonthlyReport`/`InsightView`(JavdBviewed 洞察)
- `ContentFilterRule`(JavdBviewed 过滤)
- `MagnetCache`(JavdBviewed 多源缓存)
- `LLMCache`(省钱)
- `MediaLibraryStatus`(Immortal 媒体库在库缓存)
- 沿用 AVDB:Actor/ActorMovie/Ranking/CrawlLog/Setting/Download/Collection/TaskCollection/ListSource

---

## 五、分 Phase 路线图

### Phase 1:地基 + 核心爬取(~2-3天)
- 初始化工程,Dockerfile/compose/requirements
- SQLAlchemy 模型(Task/Actor/Ranking/Setting/Log 全套)+ Alembic 初始迁移
- main.py(lifespan)+ auth.py(JWT)+ deps.py
- magnet_scraper 迁移 AVDB 版(10元组提取)
- routers: tasks/list_sources/crawl
- 复制 admin-new 为 frontend/,验证 build+连后端
- **验证**:compose up,前端连后端,跑通一次 scan→extract

### Phase 2:观看状态 + 多源聚合 + 演员库(~2-3天)
- routers/status.py(三态)+ actors.py(档案/关注/订阅/拉黑)
- services/data_aggregator.py(多源抓取合并)
- routers/rankings.py + services/auto_ranking.py
- 浏览器池 + FlareSolverr 兜底
- 前端:Library 观看状态,Actors 页

### Phase 3:调度中心 + 订阅体系 + 新作品 + 洞察(~3天)[核心差异化]
- services/scheduler.py(APScheduler 统一)
- **services/subscription_monitor.py(Immortal式多维订阅巡检)** ← 核心差异化
- services/new_works_monitor.py(演员新作 diff)
- services/report_generator.py + routers/insights.py(月报)
- services/notifier.py(Bark/TG/Discord/企微)
- 前端:Dashboard 洞察,新增"订阅""新作品"页

### Phase 4:AI Agent + 过滤 + 媒体服务器(~2天)[Immortal参考]
- services/ai_service.py(Agent 模式,翻译/标签/摘要/交互)
- routers/ai.py + llm_cache
- 内容过滤规则引擎
- **services/media_server_sync.py + routers/media_server.py(Emby/Jellyfin 在库)** ← Immortal参考
- routers/images.py(文件服务,抓取已下沉scraper)
- routers/favorites.py
- 前端:TaskDetail AI按钮,Settings 配置

### Phase 5:下载闭环(~1-2天)
- routers/downloaders.py(qB/CD2/aria2/Transmission)+ downloads.py
- services/download_tracker.py(补CD2,加Transmission)
- routers/dashboard.py + v2.py(FTS5/similar)+ settings.py
- 前端:Downloaders/Downloads 页

### Phase 6:115 + 磁力多源(~3-4天)
- services/drive115_client.py(OAuth)+ routers/drive115.py
- services/magnet_sources.py(5源)+ routers/magnet_search.py
- 前端:115页,TaskDetail 多源磁力

---

## 六、关键设计原则(规避 AVDB 补丁层覆辙)

1. **零 monkey-patch**:strivek 地基干净,功能直接进源码
2. **lifespan 启停服务**:调度/看门狗/浏览器池都在 lifespan,不在 import-time
3. **APScheduler 统一调度**:取代 5个手写 threading,支持 misfire/coalesce
4. **SQLAlchemy ORM + Alembic**:取代手写 SQL+ALTER 堆叠,预留 PG
5. **浏览器池复用**:全局 Playwright 实例池,防 Chromium 泄漏(根治 AVDB P0 问题);FlareSolverr 兜底
6. **settings 表驱动**:config 读表不硬编码
7. **RESTful 规范**:favorites/status/search 走标准动词;统一错误 `{error:{code,message}}`
8. **scraper 抓图,后端服务图**:高清图抓取下沉 scraper(浏览器已开),后端只文件服务
9. **修已知 bug**:auto_crawl `_get_setting` 误用、CASE WHEN 失效、queue TOCTOU、download_tracker CD2缺失——源码层面修正
10. **Immortal 启发**:多维订阅过滤器、媒体库缓存、Discord/企微通知、AI Agent

---

## 七、迁移与兼容

- `scripts/migrate_from_avdb.py`:ATTACH AVDB javdb.db → INSERT 到新 ORM 表(字段名兼容)
- 前端复用 admin-new,调整 `src/api/client.ts` 端点适配规范化 API

---

## 八、已知待修 Bug 清单(从 AVDB 继承,源码层面修正)

| Bug | 位置(AVDB) | 修正方式 |
|---|---|---|
| `_get_setting(get_conn())` 误用上下文管理器 | auto_crawl.py:452 | ORM session 依赖注入 |
| `CASE WHEN` 无效(THEN/ELSE 同字段) | auto_crawl.py:496 | 重写 meta_refresh 保护逻辑 |
| 队列 TOCTOU 竞态 | hires_images.py:474 | 原子 check-and-set + ORM 事务 |
| download_tracker CD2 缺失 | download_tracker.py | 补 CD2 轮询 |
| Chromium 进程泄漏 | hires_images.py:219 | 浏览器池统一管理 + finally 强制关闭 |
| Settings 密码脱敏覆盖 | main_patched.py:226 | settings router PUT 跳过哨兵值 |
| import-time 阻塞启动 | main_patched.py:1408 | 迁移到 lifespan |
