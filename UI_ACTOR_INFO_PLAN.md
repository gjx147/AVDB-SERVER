# 女优资料爬取优化 Plan（三源聚合 · 三级回退 · 全自动）

> **版本**:1.1 | **日期**:2026-08-19 | **状态**:待实施
> 需求:演员资料增强——个人信息、人物简介、职业时间线、出道年份、活跃年限;**全自动爬取,无需手动**
> 回退逻辑（用户指定）:①优先中文维基(四维度一次拿齐);查不到→②minnano-av 取个人信息;③laoshi.ink 取人物简介/职业时间线/出道年份/活跃年限

## 〇、自动化设计（v1.1 新增）

**演员入库即自动进入资料队列，后端定时任务批量补齐**：

```
演员入库（JavDB 爬取/URL 添加/榜单同步）
   → actors.profile_fetched=0（默认，自动入队）
定时任务（APScheduler，每 20 分钟一轮）
   → 取 profile_fetched=0 且未失败的演员（每轮限 5 个，限速防封）
   → fetch_profile()（中文维基→minnano→laoshi 三级回退）
   → 写库 + 标记 profile_fetched=1
详情页「刷新资料」按钮保留（手动重试用，失败过的演员/想更新资料时）
```

- **限速与容错**：每轮 5 个、间隔 20 分钟（settings 可配）；单演员失败标记失败（下轮跳过，避免反复重试 hammer 三源）；全部抓完自动停
- **新增演员无缝接入**：任何入口（JavDB 爬虫/URL 添加）入库的演员默认 profile_fetched=0，下一轮定时任务自动抓取
- **存量演员**：迁移时 profile_fetched 默认 0——升级后定时任务会把库里已有演员逐轮补齐（几百个演员分多轮，不影响服务器）

## 一、三源实测结论（全部普通 HTTP 可达，无 Cloudflare）

| 源 | 覆盖 | 可提取字段 | 解析方式 |
|---|---|---|---|
| zh.wikipedia.org | 仅知名演员 | **个人信息**（出生日期/出身地/血液型/身長/三围(B/W/H)/罩杯/出道期间/别名/经纪公司）+ **人物简介**（条目导语段）+ **职业时间线**（经历章节年份行）+ **出道年份/活跃年限**（信息框 AV出演期間） | **API wikitext 全文**（`{{AV女優\|…}}` 模板参数 + 导语段 + 章节，最结构化） |
| minnano-av.com | 几乎全覆盖 | 生日/三围/罩杯(G)/身高/血型/星座/爱好 | HTML 资料行解析（`T158 / B96(G) / W56 / H82`） |
| laoshi.ink | 中 | 人物简介（百科型）、职业时间线、出道年份、活跃年限、别名 | HTML 正文段落 + 档案卡片解析 |

## 二、数据模型

**Alembic 迁移** `add_actor_profile_columns`：actors 表新增列（现有 birth_date/height/cup/measurements/debut_date 直接复用）：

| 新列 | 类型 | 内容 |
|---|---|---|
| blood_type | String(10) | 血型（A型） |
| zodiac | String(20) | 星座 |
| birthplace | String(100) | 出身地（日本愛知縣名古屋市） |
| nationality | String(50) | 国籍 |
| active_years | String(50) | 活跃年限（"12 年" / 出道期间"2015-2023"） |
| bio | Text | 人物简介 |
| timeline | Text | 职业时间线 |
| alias | String(200) | 别名（鬼頭桃菜） |
| profile_fetched | Boolean 默认 0 | 资料抓取标记（自动队列依据） |
| profile_fetch_failed | Boolean 默认 0 | 抓取失败标记（避免反复重试） |

理由：note 字段已被 JavDB 流程占用（`source_url: ` 前缀解析），不塞 JSON；加列清晰、前端展示直接。

## 三、后端聚合器

**新建 `backend/services/actor_profile.py`**（httpx 纯请求，秒级，无浏览器）：

### 主函数
```
fetch_profile(name, name_en) -> {ok, source, fields:{...}, message}
```
按用户指定逻辑：
1. **中文维基**（优先，**全部维度一次拿齐**）：API 搜索 `action=query&list=search&srsearch=名字` → 取首条 → `action=parse&prop=wikitext&section=0` 拿全文 wikitext，四层解析：
   - **个人信息**：`{{AV女優|...}}` 模板参数正则（出生日期/血液型/身長/バスト/ウエスト/ヒップ/カップ/出身地/别名/AV出演期間/専属契約）
   - **出道年份/活跃年限**：从 `AV出演期間`（如 "2015年 - 2023年"）提取起止年份并计算年限
   - **人物简介**：信息框之后的条目导语段（首个非模板正文段落，清洗 wiki 标记/引用）
   - **职业时间线**：`== 经历 ==`/`== 経歴 ==` 等章节下按年份行提取（"2015年 X月…" 开头的行聚成时间线）
   → 命中即返回（含全部四个维度）
2. **维基未命中 → minnano-av**：`search_result.php?search_scope=actress&actress_name=名字` 搜索结果取首条 → 详情页解析资料行（T158/B96(G)/W56/H82、birthday=YYYY-MM-DD、血型/星座）——个人信息维度；简介/时间线维度 minnano 无（保持空）
3. **laoshi.ink**（维基失败后执行，补百科维度）：`search.html?q=名字` → 详情页 `/actresses/actor-XXX.html` 解析：简介段落、职业时间线、出道年份、活跃年限

### 关键实现点
- **代理**：从 DB settings 读 http_proxy（与 browser_pool 同款 `_get_proxy_from_db`），httpx client 带 proxy；未配代理则直连
- **名字搜索策略**：优先 Actor.name；维基无结果且 name_en 存在时用 name_en 重搜一次
- **结果匹配校验**：维基搜索结果标题与输入名做包含匹配（防同名不同人）；minnano 同理
- **字段映射**：三围拆成 measurements（`B84 / W58 / H88` 格式与现有 xslist/JavDB 惯例一致）；カップ→cup；AV出演期間→active_years（保留原文"2015年 - 2023年"）；维基正文清洗（去 {{cite}}/ref/wiki 标记与模板）
- **维基四层解析容错**：任一层解析失败不阻断其余层——信息框无某参数则留空，导语段/经历章节缺失时简介/时间线留空（前端不显示空卡）
- **超时与降级**：每源 15s 超时，单源失败不影响后续回退；全部失败返回可读错误

### 端点
- `POST /api/actors/{actor_id}/refresh-profile`：手动重试（读演员名 → fetch_profile → 写库 → 标记状态重置）
- `GET /api/actors/profile-queue/status`：队列状态（未抓/已抓/失败计数，供前端展示）

### 定时任务（自动化核心）
- 新建 `backend/services/actor_profile_sync.py`：`run_cycle()`——取 5 个 profile_fetched=0 的演员逐个抓取（含 5-10s 间隔限速），写库并标记；注册进现有 APScheduler（main.py lifespan，与订阅巡检同模式，间隔 20 分钟，env 可调 `ACTOR_PROFILE_INTERVAL_MIN`）
- 全自动：演员入库即入队，无需任何手动操作

## 四、前端

- **types.ts**：Actor 加 blood_type/zodiac/birthplace/nationality/active_years/bio/timeline/alias
- **client.ts**：加 `refreshProfile(actorId)`
- **ActorDetail.tsx**：
  - 操作栏加「刷新资料」按钮（Icon.refresh），点击调接口，toast 显示来源（"资料已更新（中文维基）"）
  - 资料胶囊区扩展：血型/星座/出身地/国籍/活跃年限/出道
  - 新增「人物简介」卡（bio）与「职业时间线」卡（timeline，毛玻璃卡内，与照片画廊同区）
- 演员库 Actors 列表卡片无改动（资料在详情页展示）

## 五、阶段

| 阶段 | 内容 | 估时 |
|---|---|---|
| P1 | 迁移（8 资料列 + 2 队列标记列）+ actor_profile.py 聚合器 + 定时任务 + refresh-profile 端点 | 1 天 |
| P2 | 前端展示扩展（资料胶囊 + 简介卡 + 时间线卡 + 队列状态）+ 保留手动重试按钮 | 0.5 天 |
| P3 | 联调冒烟（真实三源走查：知名演员命中维基 / 二线演员走 minnano+laoshi；验证定时任务自动补齐） | 0.5 天 |

## 六、风险与对策

| 风险 | 对策 |
|---|---|
| 维基条目名与库内名不一致（繁简/别名） | 用 API 搜索而非猜 URL；标题包含匹配校验；失败自动降级 minnano |
| minnano 日文界面编码 | httpx 响应按 UTF-8/SHIFT_JIS 探测解码；解析只取关键行 |
| laoshi 页面结构变动 | 解析选择器集中常量 + 多级兼容（照 xslist 联调经验，先本机抓真实页校准） |
| 同名不同人误匹配 | 三源均做标题包含校验；维基模板含别名可辅助确认 |
| 站速慢/超时 | 每源 15s 超时；refresh-profile 异步返回，前端 loading 态 |

## 七、涉及文件

- 新增：`alembic/versions/xxx_add_actor_profile_columns.py`、`backend/services/actor_profile.py`
- 修改：`backend/models/actor.py`、`backend/schemas.py`、`backend/routers/actors.py`（+refresh-profile）、`frontend/src/api/types.ts`、`frontend/src/api/client.ts`、`frontend/src/pages/ActorDetail.tsx`
