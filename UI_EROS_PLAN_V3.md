# 「欲焰 · Eros V3」前端情欲进阶方案

> **版本**:3.0 | **日期**:2026-08-19 | **状态**:✅ 已实施(V3-1~V3-5 全部落地,构建通过+后端 20 项测试通过+浏览器冒烟通过)
> 四部门联合评审:色气视觉部 / 感官交互部 / 声效节奏部 / 耳语文案部 + 总编室裁决与工程评审
> 前置:v2「Boudoir Noir」已全部落地(五感机制/暗夜丝绒/密室/体温/掀纱/唇印/盲盒,commit ea9a301)
> **v3 主旨**:从「华丽氛围」推进到「勾引主体」——界面不再是被观看的舞台,而是主动调情的那个人。

---

## 一、v3 设计总纲:「她在回应你」

v2 建立了五感机制(体温/心跳/掀纱/耳语/唇印),v3 在其上叠加三层进阶:

| 层 | v2 已有 | v3 进阶 |
|---|---|---|
| **视觉色气** | 华丽氛围(丝绒鎏金/绸光) | 视觉勾引:身体构图取景、哈气玻璃擦拭、红灯区聚光、衣衫褪去分层 |
| **交互调情** | 观看式披露(解扣 hover) | 身体对话:长按解衣、指缝偷看、抚摸留痕、喘息过载、娇嗔人格 |
| **听觉与人格** | (无) | 全新声色层:WebAudio 心跳/轻叹/烛火;文案第三档「露骨」+ UI 拟人化「她」+ AI 动态情话 |

**人格统一定义**(文案部,全站声文案与气泡共用):影库的女主人——低而近的耳语声音、知情识趣永远比你半步从容的性格、欲望写得像诗的说话方式(用温度/重量/距离/衣料暗喻,不直呼器官不写脏字)。

**新设计令牌**(tokens 追加,一次到位):
```css
:root{
  --nude:#e8c4ae; --nude-deep:#c98f76;      /* 裸色系(丝袜/肤色) */
  --lipstick:#d4125a;                        /* 口红红(攻击性主色) */
  --redlight:rgba(212,18,90,.32);            /* 红灯区 rim light */
  --excite:0;                                 /* 喘息值 0~1(快累积,与 --heat 正交) */
}
```

---

## 二、四部门核心提案速览

| 部门 | 提案(详细见附录) |
|---|---|
| 色气视觉部 | P0 锁骨构图(演员照片取景学)、哈气玻璃;P1 红灯区聚光、褪衣分层、心跳辉光锚点;P2 唇语排版(CSS 唇形符号) |
| 感官交互部 | P0 褪去长按解衣、偷看指缝视孔;P1 抚摸红晕轨迹、喘息节奏过载;P2 耳鬓厮磨(演员凑近)、娇嗔赌气与融化 |
| 声效节奏部 | 引擎(AudioContext 惰性单例+三总线+默认静音);P0 heartbeat(BPM=52+heat×0.6)、silk hover;P1 sigh 收藏轻叹、veil 转场、candle 密室环境音;P2 chime 盲盒杯碰 |
| 耳语文案部 | 三档文案(tame/bold/sultry 27+ key 对照表);随机池+深夜加成(22-5点自动升温一档);拟人错误态;AI 耳语(POST /api/ai/whisper);时段问候 |

---

## 三、总编室裁决(冲突合并 + 横切机制)

### 裁决 1:两个「褪衣」合并为「衣衫体系」
视觉部「褪衣分层」(hover 纱面滑落,纯 CSS)与交互部「褪去长按解衣」(长按三层递进,rAF 状态机)是同一叙事的两个深度:**hover = 微褪(纱面向下滑落露出柔焦暖化层),长按 1.8s = 深褪(clip-path 上移递进露出样品图,松手弹回轻颤)**。同一张卡上两档叠加,不冲突。blur 隐私模式下长按深褪禁用(防止绕过隐私)。

### 裁决 2:两个「遮露」合并为「雾与指缝」
哈气玻璃(视觉部,详情页进场擦拭 + Wall 信息层)与偷看指缝(交互部,blur 模式径向视孔)分别服务不同场景,不合并实现但统一文案与音效:`veil` 音效(双向滤波滑音)同时挂在两处。

### 裁决 3:「她的回应」横切机制(三位一体)
收藏/心动的瞬间 = 视觉(Heartburst 心爆+唇印盖章) + 声音(sigh 若有似无的轻叹) + 文案(随机池低语)同帧触发——这是 v3 的「勾引闭环」样板,所有情感动作都按此三件套设计。

### 裁决 4:heat 与 excite 双变量正交
- `--heat`:慢累积(分钟级,v2 已有)——界面「体温」,驱动光晕/烛焰/粒子/心跳 BPM/文案深夜加成
- `--excite`:快累积(10 秒级,v3 新增)——界面「喘息」,10s 内 ≥5 次交互则攀升,≥100 触发一次性 climax 颤栗(全屏 2px 抖 200ms+绯红闪)后线性回落。二者独立衰减,组合出「慢热」与「急切」两种状态。

### 裁决 5:文案档位迁移
`moodMode: boolean` 升级为 `copyTier: 0|1|2`(tame/bold/sultry),localStorage 兼容旧值('1'→1);密室开关继续存在(密室=暗场交互层),文案档位独立于密室可在设置页单独选;深夜加成(22-5点)默认开,可关。

---

## 四、工程评审(总编室)

1. **视觉/交互提案**:与 v2 架构同构(eros.css 扩展 + ambient.ts 委托扩展),零新依赖;`:has()`/`clip-path`/`mask`/`backdrop-filter` 均为已验证可用的特性。
2. **声效是全新模块** `src/audio/`(engine.ts + voices.ts):纯 WebAudio 合成零音频文件零外网;AudioContext 惰性创建(首次 pointerdown 解锁,规避自动播放策略);默认静音(localStorage `sound`),设置页总开关+三总线分音量;`document.hidden` 即哑;每音效随机化 ±15% 防听觉疲劳。
3. **喘息/娇嗔**:store 扩展(excite/dwellLog/sulk),纯前端状态机,无后端改动。
4. **AI 耳语**:后端 `routers/ai.py` 新增 `POST /api/ai/whisper`(入参 task_id/tone/night),复用 `ai_service.chat()` 与 llm_cache 表(按 prompt_hash 缓存,零迁移);前端 Wall 轮播位 + 盲盒落款接入,AI 未配置/超时静默回退静态池。
5. **性能护栏**:新增动效仍限 transform/opacity/filter/clip-path;抚摸轨迹粒子 60ms 节流+上限;声效发声上限=1 实例;沿用 reduced-motion(声音默认禁用)/coarse/lowPower 三通道降级。
6. **隐私兜底**:声音默认关;blur 模式禁用深褪;AI 情话仅在密室/深夜档生效时不落库明文(仅 llm_cache)。

---

## 五、分阶段路线图(每阶段独立可合并可回滚,总估 5–6 天)

### V3-1 「她的声音」—— 1 天(声色层 + 文案三档)
| # | 内容 | 来源 |
|---|---|---|
| 1.1 | WebAudio 引擎 `src/audio/engine.ts`(三总线/手势解锁/默认静音/heat 挂钩) | 声效§二 |
| 1.2 | 音效 P0:heartbeat 持续层(BPM=52+heat×0.6)、silk hover 音 | 声效§三 |
| 1.3 | 设置页「声色」区:总开关+三总线音量;密室开启时 toast 建议开声 | 声效§四 |
| 1.4 | 文案三档迁移:copyTier 0/1/2 + sultry 全量文案池(27+ key)+ 随机池(每 key 3-5 条) | 文案§二三 |
| 1.5 | 拟人错误态:加载失败/网络异常/抓取失败换「她」的回应 | 文案§二 |

### V3-2 「衣衫与雾」—— 1–1.5 天(遮露交互包)
| # | 内容 | 来源 |
|---|---|---|
| 2.1 | 褪衣分层:poster-drape 纱面层,hover 纱向下褪去露出柔焦暖化层 | 视觉四 |
| 2.2 | 长按深褪:peel 状态机(掀角→半褪→滑落),松手弹回轻颤,按满 +8 heat;blur 模式禁用 | 交互一 |
| 2.3 | 哈气玻璃:TaskDetail 进场「雾擦」替代「纱掀」、Wall 信息层雾面 | 视觉二 |
| 2.4 | 偷看指缝:blur 隐私模式升级为按住视孔(径向 mask 跟随指针,松开泛红晕) | 交互二 |
| 2.5 | veil 音效挂接转场/解雾 | 声效§三 |

### V3-3 「红灯与身体」—— 1 天(视觉勾引包)
| # | 内容 | 来源 |
|---|---|---|
| 3.1 | 红灯区聚光:画廊 hover 时被凝视者红 rim 点亮、其余压暗沉入暗部 | 视觉三 |
| 3.2 | 锁骨构图:演员图 object-position 上移+锁骨侧影遮罩+唇部聚焦晕;ActorDetail S 形金线引导 | 视觉一 |
| 3.3 | 心跳辉光锚点:收藏键/演员名/CTA 的「皮下透红」辉光(--heat 同步心搏) | 视觉五 |
| 3.4 | 耳鬓厮磨:hover 演员头像她「凑近」(scale 1.35+背景推远压暗)+气声气泡 | 交互五 |

### V3-4 「喘息与人格」—— 1–1.5 天(状态与人格包)
| # | 内容 | 来源 |
|---|---|---|
| 4.1 | 喘息系统:excite 快累积、界面轻颤/泛红/动效提速,climax 一次性颤栗后回落 | 交互四 |
| 4.2 | 抚摸轨迹:指针划过海报留渐隐红晕(速度反比尺寸,3s 连续 +4 heat) | 交互三 |
| 4.3 | 娇嗔与融化:快速跳片触发赌气态(扭头入场+气泡),停留 3s 触发融化奖励 | 交互六 |
| 4.4 | 深夜加成 + 时段问候(侧栏早安/午后/深夜三语气) | 文案§三 |
| 4.5 | sigh 收藏轻叹 + candle 密室环境音 | 声效§三 |

### V3-5 「AI 耳语」—— 1 天(智能情话包)
| # | 内容 | 来源 |
|---|---|---|
| 5.1 | 后端 `POST /api/ai/whisper`(人格 prompt + 元数据拼接 + llm_cache) | 文案§四 |
| 5.2 | 前端接入:Wall 轮播简介位换 AI 情话(zustand 按 task+tone 缓存,失败回退静态池) | 文案§四 |
| 5.3 | 今夜情人落款词 AI 化 + chime 杯碰音 | 文案/声效 |
| 5.4 | 唇语排版:eyebrow 分隔符换 CSS 唇形、标题关键字「欲言又止」、元数据口红管胶囊 | 视觉六 |

---

## 六、风险清单

| 风险 | 对策 |
|---|---|
| 声音打扰/尴尬 | 默认全局静音;手势解锁后才可出声;设置页三总线分音量;页面隐藏即哑 |
| 长按深褪与移动端长按菜单冲突 | touch 上下文菜单 preventDefault 仅限海报卡;单档 800ms 直接半褪防误触 |
| excite/climax 引发晕动不适 | 颤栗幅度 ≤2px、时长 ≤200ms、单次会话最多触发 3 次;reduced-motion 全关 |
| 哈气玻璃 backdrop-filter 叠加掉帧 | 与丝绸光泽分时触发;低功耗直接换渐变遮罩 |
| AI 情话响应慢/未配置 | 静态池先落位,超时 1.5s 静默回退;不阻塞任何 UI |
| 文案尺度失控 | sultry 池入库前人工审定;AI prompt 内置「禁粗俗词」约束 |
| `:has()` 聚光在超大厅容器的开销 | 画廊容器级 :has 一次性求值,不随指针移动重算 |

---

## 七、涉及文件

- 新增:`src/audio/engine.ts`、`src/audio/voices.ts`
- 扩展:`src/styles/eros.css`(色气视觉包)、`src/effects/ambient.ts`(抚摸轨迹/peel/视孔)、`src/store/useStore.ts`(copyTier/excite/sulk/dwellLog/soundOn)、`src/i18n/whisper.ts`(三档+随机池+深夜)
- 页面:`Wall.tsx`(AI 情话/雾面)、`TaskDetail.tsx`(雾擦进场/辉光锚点)、`Actors.tsx`+`ActorDetail.tsx`(锁骨构图/凑近)、`Settings.tsx`(声色/文案档)、`Sidebar.tsx`(时段问候)、`DailyReveal.tsx`(AI 落款+chime)
- 后端:`backend/routers/ai.py`(+whisper 端点)、`backend/services/ai_service.py`(prompt 构造)

---

# 附录:四部门原始提案

## 附录 A · 色气视觉部「视觉勾引」层

### 提案一「锁骨构图」P0
演员照片身体构图学:头像/演员图特写裁剪+曲线引导线,视线锚定肩颈—锁骨—唇三角区。
```css
.actor-photo{aspect-ratio:3/4;overflow:hidden;position:relative}
.actor-photo img{object-position:50% 22%}
.actor-photo::after{content:"";position:absolute;inset:0;
  background:
    linear-gradient(200deg,transparent 55%,rgba(61,21,38,.28) 100%),
    radial-gradient(70% 46% at 50% 18%,transparent 52%,rgba(255,20,147,.10) 100%)}
.actor-hero::before{content:"";position:absolute;inset:0;
  background:linear-gradient(160deg,transparent 30%,rgba(232,201,138,.18) 50%,transparent 70%);
  transform:skewY(-6deg)}
```

### 提案二「哈气玻璃」P0
镜子上哈了口气——hover 像手指擦开雾露出下面的人。
```css
.fog-glass::before{content:"";position:absolute;inset:0;z-index:2;pointer-events:none;
  backdrop-filter:blur(9px) saturate(1.15);background:rgba(255,244,248,.28);
  -webkit-mask:radial-gradient(120px 90px at var(--fx,30%) var(--fy,70%),transparent 98%,#000);
  mask:radial-gradient(120px 90px at var(--fx,30%) var(--fy,70%),transparent 98%,#000)}
@media(hover:hover){ .fog-glass:hover::before{opacity:0;transition:opacity 1s var(--e-slow)} }
.fog-wipe{animation:fogWipe 1.4s var(--e-burn) forwards}
@keyframes fogWipe{0%{opacity:1;mask-size:20% 30%}60%{mask-size:160% 200%}100%{opacity:0}}
```

### 提案三「红灯区」P1
深夜红灯区顶光:被凝视者红 rim 点亮,其余沉入暗部。
```css
.gallery-grid:has(.poster:hover) .poster:not(:hover){filter:brightness(.55) saturate(.7);
  transition:filter .8s var(--e-slow)}
.poster::before{content:"";position:absolute;inset:0;z-index:2;pointer-events:none;opacity:0;
  background:linear-gradient(115deg,var(--redlight),transparent 46%),
    radial-gradient(60% 44% at 78% 8%,rgba(255,80,140,.30),transparent 70%);
  transition:opacity .6s var(--e-slow)}
.poster:hover::before{opacity:1}
```

### 提案四「褪衣分层」P1
衣衫滑落式三层揭幕:纱面→柔焦暖化→原图。
```css
.poster-drape{position:absolute;inset:0;z-index:2;pointer-events:none;
  background:linear-gradient(180deg,rgba(255,228,238,.10),rgba(255,190,215,.34) 88%);
  backdrop-filter:blur(2px) saturate(1.1);
  clip-path:inset(0 0 0 0);transition:clip-path 1.2s var(--e-burn)}
@media(hover:hover){
  .poster:hover .poster-drape{clip-path:inset(100% 0 0 0)}
  .poster:hover .poster-frame img{filter:saturate(1.22) contrast(1.05) brightness(1.04)} }
```

### 提案五「心跳辉光锚点」P1
关键锚点的皮下透红辉光,与 --heat 同步心搏。
```css
.pulse-anchor::after{content:"";position:absolute;left:18%;top:-6px;width:70%;height:34%;
  background:radial-gradient(50% 100% at 50% 100%,rgba(212,18,90,.55),transparent 75%);
  filter:blur(6px);pointer-events:none;
  opacity:calc(.35 + .45*var(--heat));
  animation:skinGlow calc(3.2s - 1.4s*var(--heat)) var(--e-heart) infinite}
@keyframes skinGlow{0%,100%{transform:scale(1)}12%{transform:scale(1.18)}24%{transform:scale(1.02)}
  36%{transform:scale(1.12)}58%{transform:scale(1)}}
```

### 提案六「唇语排版」P2
排版级唇形符号:eyebrow 分隔符 CSS 唇形、标题关键字欲言又止、元数据口红管胶囊。
```css
.eyebrow::before,.eyebrow::after{content:"";width:14px;height:8px;
  background:var(--lipstick);border-radius:60% 60% 60% 60%/100% 100% 40% 40%;
  clip-path:path("M0 4 Q7 -3 14 4 Q7 9 0 4")}
.page-title em{letter-spacing:.06em;font-style:italic;color:var(--lipstick);
  text-shadow:0 0 calc(8px+10px*var(--heat)) rgba(212,18,90,.45)}
.dm-key{border-radius:3px 10px 10px 3px;background:var(--nude-deep);color:#fff8f2;padding:1px 8px}
```

## 附录 B · 感官交互部「身体对话」

### 提案一「褪去」长按解衣 P0
按住海报,封面如衣角被勾住缓缓上移露出样品图,三档递进(掀角→半褪→滑落);松手弹回轻颤像被嗔怪。
```ts
const onDown = (el) => { start = performance.now(); raf = tick(() => {
  el.style.setProperty('--peel', Math.min(1, (now-start)/1800)) }) }
// CSS: clip-path: inset(calc(var(--peel)*60%) 0 0 0) 配合上层 translateY 弹回
```
触屏单档 800ms 直接半褪防误触;blur 模式禁用;按满 +8 heat。

### 提案二「偷看」指缝遮眼 P0
blur 模式按住海报=从指缝偷看:指针为圆心的柔边视孔,按住才清晰、移走合拢,松开泛红晕。
```css
/* mask:radial-gradient(circle at var(--mx) var(--my), transparent 0 calc(var(--peek)*90px), black 120px) */
```
复用 initSpotlight 的 --mx/--my;密室模式孔径×0.6。

### 提案三「抚摸」触碰红晕 P1
指尖划过海报留渐隐暖粉红晕,划得越慢红晕越大越深;连续 3s +4 heat。ambient.ts initCaress:pointermove 60ms 节流 spawn .caress-dot,速度反比尺寸。

### 提案四「喘息」节奏过载 P1
10s 内连续交互触发喘息态:界面 ±1px 呼吸轻颤、边缘泛红、动效×1.5、心跳加快;顶点一次颤栗(2px/200ms+绯红闪)后缓慢平复。store 加 excite 0-100,--excite 驱动 animation-duration/motes/vignette。

### 提案五「耳鬓厮磨」演员凑近 P2
hover 演员头像她凑近镜头(scale 1.35 超调缓动),背景推远压暗,耳边气声气泡;离开恋恋不舍退回。触屏 tap 切换。

### 提案六「娇嗔」惩罚与宠溺 P2
快速跳片 5+ 次界面闹脾气(扭头入场+气泡"急什么呀…");温柔停留 3s 触发融化(光晕绽开+heat+10+"这才对嘛")。store dwellLog + [data-sulk] 变体。

## 附录 C · 声效节奏部「声色层」

声音身份三词:低语、贴耳、烛光——好的暧昧音效是无形的,用户只该觉得心跳快了一点。

### 三音色家族
- **生理音**:心跳(60Hz 正弦短包络双跳)、呼吸(粉噪带通 400-900Hz 慢包络)。频率越低越贴身,包络越慢越暧昧。
- **材质音**:丝绸/布料(白噪 bandpass 1.5-4kHz + 八度滑音,随机 ±15%)。
- **环境音**:烛火(棕噪低通+随机噼啪脉冲簇)、杯碰(双高频正弦高 Q 共振)。

### 引擎架构
```ts
class AudioEngine {
  private ctx?: AudioContext
  enabled = localStorage.getItem('sound') === '1'   // 默认关闭
  unlock() { /* 首次 pointerdown 创建 ctx;master→三总线→Compressor→destination */ }
  play(name: string, intensity = 0.5) { /* enabled/hidden/suspended 三重守卫 */ }
  setHeat(h: number) { /* 心跳 BPM: 52 + h*60 */ }
}
```

### 音效清单
| 名称 | 触发 | 合成 | 级 |
|---|---|---|---|
| heartbeat | 持续层 heat>30,BPM=52+heat×0.6 | 60Hz 双跳+微混响 | P0 |
| silk | 海报 hover(2.5s 节流) | bandpass sweep 3.5k→1.2k | P0 |
| veil | 掀纱/解雾 | 双向 sweep+200Hz 托底 | P1 |
| sigh | 收藏成功 | 粉噪 600Hz 缓起缓落+280Hz 气声下滑,音量 0.06 | P1 |
| candle | 密室环境循环 | 棕噪+随机噼啪 | P1 |
| chime | 盲盒揭晓 | 2093/3136Hz 杯碰 | P2 |

### 克制原则
默认静音/手势解锁/节流去抖/页面隐藏即哑/随机化防疲劳/reduced-motion 用户默认禁用。

## 附录 D · 耳语文案部「全链路调情」

### 人格三行定义
声音:低而近,贴着耳廓,短句留白,多称"你",语气词收尾。性格:知情识趣的引路人,永远比你半步从容。说话:欲望写得像诗——温度/重量/距离/衣料的暗喻。

### 三档对照表(节选,完整 27+ key 见实施)
| key | tame | bold | sultry |
|---|---|---|---|
| loading | 加载中… | 她在更衣,稍等… | 宽衣需要一点时间,别偷看 |
| fav_add | 心动了,收进私藏 | ♥ 心动了,她已经是你的人 | 把她压进心底,锁好 |
| btn_see | 看得更清楚一点 | 再看清一点,别急 | 挑开那层纱,看仔细 |
| download_done | 下载完成 | 她到家了 | 她已经躺在你硬盘里了 |
| err_load | 加载失败,请重试 | 人家走神了,再试一次嘛 | 刚才想你想岔了,再来一次 |
| greet_night | 晚上好 | 夜里才刚开始 | 这个点进来的,都没安好心——包括我 |

### 机制
档位 boolean→0|1|2(兼容旧值);COPY 值改 string[] 随机池(3-5 条/键,lastPick 去重);深夜 22-5 点自动 +1 档;侧栏时段问候。

### AI 耳语
`POST /api/ai/whisper` {task_id, tone, night} → ai_service.chat()(llm_cache 零迁移);system=人格三行+「一句 ≤24 字挑逗推荐语,暗喻性张力,禁粗俗」;user=标题/演员/标签/评分+档位时段指令;前端 Wall 简介位+盲盒落款,zustand 缓存,失败回退静态池。
