# AVDB-SERVER

JavDB 影片元数据采集与管理系统。自部署于 NAS 的服务端全栈应用。

## 定位

基于多工程融合重建:
- **地基**:`strivek/javdb-app` 的干净分层(FastAPI + Playwright 爬虫)
- **功能来源**:AVDB 的富元数据提取与去补丁化重写
- **服务端友好功能**:移植自 JavdBviewed 浏览器扩展(观看状态/新作品监控/数据洞察/AI)
- **产品蓝图**:参考 Immortal 的成熟设计(多维订阅/媒体服务器/AI Agent/多通知渠道)

## 技术栈

- **后端**:Python 3.12 + FastAPI + SQLAlchemy 2.0 ORM + Alembic
- **数据库**:SQLite(默认,FTS5 全文搜索)/ PostgreSQL(可选)
- **爬虫**:Playwright + playwright-stealth + FlareSolverr 兜底
- **调度**:APScheduler
- **前端**:React 18 + TypeScript + Vite + Zustand
- **部署**:Docker + docker-compose

## 状态

📋 **规划中** — 详见 [PLAN.md](./PLAN.md)

## 功能蓝图

- **影片库**:画廊浏览、多维筛选、FTS 搜索、收藏分组
- **爬取引擎**:Playwright 绕 Cloudflare、富元数据提取(磁力/标题/演员/标签/封面/评分)、串行队列
- **多维订阅**:榜单/演员/综合订阅,定时巡检入库(参考 Immortal)
- **新作品监控**:订阅演员新作检测 + 通知
- **数据洞察**:月度统计报告(Top标签/集中度/趋势)+ AI 文案
- **AI Agent**:OpenAI 兼容协议,翻译/标签/摘要/交互
- **媒体服务器**:Emby/Jellyfin 在库状态查询与缓存
- **下载闭环**:qBittorrent / CloudDrive2 / aria2 / Transmission
- **115 网盘**:OAuth + 离线任务(P2)
- **磁力多源**:sukebei/btdig/btsow/torrentz2/javbus 并发搜索(P2)
- **通知**:Bark / Telegram / Discord / 企业微信 / Webhook
- **隐私**:OAuth2 + JWT 认证

## License

MIT
