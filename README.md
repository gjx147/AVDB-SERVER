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

## 快速开始

### 1. 准备环境

```bash
# 复制环境配置模板
cp .env.example .env

# 编辑 .env，设置（留空则首次启动自动生成随机值）
# SECRET_KEY=your-strong-secret-key
# ADMIN_PASSWORD=your-strong-password
```

### 2. Docker 部署（推荐）

```bash
# 构建并启动
docker compose up -d

# 查看日志（首次启动会打印自动生成的密码）
docker logs -f avdb-server

# 访问
open http://localhost:8000
```

### 3. 本地开发

```bash
# 后端
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -r backend/requirements.txt
uvicorn main:app --reload --app-dir backend

# 前端
cd frontend
npm install
npm run dev
```

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `SECRET_KEY` | JWT 密钥（留空自动生成） | 自动生成 |
| `ADMIN_USERNAME` | 管理员用户名 | `admin` |
| `ADMIN_PASSWORD` | 管理员密码（留空自动生成） | 自动生成 |
| `AUTH_DISABLED` | 关闭鉴权（仅开发用） | `false` |
| `DATABASE_URL` | 数据库连接（空=SQLite） | SQLite |
| `JAVDB_URL` | JavDB 站点 URL | `https://javdb.com` |
| `HTTP_PROXY` / `HTTPS_PROXY` | 代理 | 无 |
| `AUTO_CRAWL_ENABLED` | 启用定时爬取 | `false` |

## NAS 部署说明（绿联 UGREEN）

1. 在 UGOS Pro 容器工作站中导入 `docker-compose.yml`
2. 映射 `/app/data` 到 NAS 共享文件夹（便于备份）
3. 映射端口 `8000`
4. **CPU 架构**：需 x86_64（Playwright Chromium 不支持 ARM）
5. **内存建议**：≥ 4GB（Chromium 浏览器池 + Python）
6. 首次启动可能需要 1-2 分钟（Alembic 迁移 + Chromium 冷启动）

## 功能蓝图

- **影片库**:画廊浏览、多维筛选、FTS 搜索、收藏分组
- **爬取引擎**:Playwright 绕 Cloudflare、富元数据提取、串行队列
- **多维订阅**:榜单/演员/综合订阅，定时巡检入库
- **新作品监控**:订阅演员新作检测 + 通知
- **数据洞察**:月度统计报告 + AI 文案
- **AI Agent**:OpenAI 兼容协议，翻译/标签/摘要
- **媒体服务器**:Emby/Jellyfin 在库状态
- **下载闭环**:qBittorrent / aria2 / 115 网盘
- **通知**:Bark / Telegram / Discord / 企业微信 / Webhook
- **用户系统**:JWT 认证 + 管理员权限

## License

MIT
