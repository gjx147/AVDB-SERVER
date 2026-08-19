# 「欲焰 · Boudoir Noir」前端华丽升级方案 v2（情欲加强版）

> **版本**:2.0 | **日期**:2026-08-19 | **状态**:✅ 已实施(Phase 0–3 全部落地,构建通过+浏览器冒烟通过)
> 四部门联合评审(视觉设计部 / 动效导演部 / 感官体验部 / 前端工程部)+ 总编室 v2 修订
> 需求:更华丽、更多动效、**情欲张力为设计主线**(私人媒体库的魅惑系高级感,性感直接但不低俗)
>
> **v2 核心变化**:情欲元素从 v1 的「氛围点缀」升级为「设计主线」。引入五感情欲机制——
> **体温(越用越热)、心跳(生理节律)、掀纱(解扣式披露)、耳语(感官文案)、唇印(挑逗符号)**,全部贯穿全站。

---

## 一、设计总纲:Boudoir(私室)美学

**一句话**:界面不再是"粉色工具",而是一间**会呼吸的私室**——烛光、丝绒、体息;她注意到你在看她,并慢慢回应你。

**风格锚点**:Boudoir Photography(闺房私摄)+ 红丝绒 + 烛光,性感直接但保持高级质感,不廉价不低俗。

### 五感机制(全站贯穿,v2 主线)

| 机制 | 隐喻 | 落地 |
|---|---|---|
| 🔥 体温 Heat | 被撩热的过程 | session 热度累积(hover/停留/收藏加权),界面随热度**升温**:唇色变浓、烛光变旺、光晕变强、动效变快——冷启动是"矜持的",用久了是"滚烫的" |
| 💓 心跳 Pulse | 生理节律 | 全站统一"两快一慢"心搏曲线(现有 heartLine 扩为体系):加载、进度、Wall 切换前蓄力、收藏瞬间,都按心跳节奏跳动 |
| 🌫 掀纱 Undress | 欲遮还露 | 信息不再"浮现"而是"**解开**":解扣式多层披露,每层多等 300–800ms,长按/深 hover 才解锁下一层 |
| 💌 耳语 Whisper | 私密对话 | 全站文案换为第一人称耳语体:"收藏"→**"心动了"**、"想看"→**"馋了"**、已看→"尝过了"、加载→"她在更衣…"、空库→"这里还空着,等你带她回家" |
| 💋 唇印 Kiss | 挑逗符号 | 收藏=唇印盖章动画、导航活跃项唇印角标、登录卡蕾丝描边、搜索框聚焦时唇色渐染 |

---

## 二、统一技术决策(总裁决)

1. **CSS 拆分模块化,不引框架**:`theme.css`(765行)拆为 `src/styles/` 多文件,Vite 构建时 `@import` 聚合为零运行时成本:
   ```
   src/styles/
   ├── index.css        # 入口,按序 @import 聚合
   ├── tokens.css       # :root 变量 + [data-theme="boudoir"] 暗色覆盖 + 曲线/时长/热度变量
   ├── base.css         # reset/body/滚动条/焦点环
   ├── motion.css       # 全部 keyframes + aura/ripple/veil/particles 特效样式
   └── components/      # poster.css / buttons.css / forms.css / wall.css / detail.css ...
   ```
   理由:Tailwind/CSS Modules 需重写全部类名或与全局委托选择器(`.poster`)冲突;纯物理拆分,git 可追、单 commit 可回滚。
2. **纯 CSS + WAAPI,不引 motion 库**:列表/入场用 CSS stagger(现有 `.rv` IntersectionObserver 模式扩展 `--i`);一次性编排(详情页 hero、盲盒揭幕)用 `element.animate()`;scroll-driven 用 `animation-timeline: view()` 仅作 `@supports` 渐进增强,回退 IO。
3. **双主题 + 密室层,一套变量体系**:`[data-theme="boudoir"]` = 暗夜丝绒**调色板**(Settings 切换+持久化+跟随 `prefers-color-scheme`);`[data-mode="mood"]` = 密室**交互层**(蜡烛一键开:慢节奏/隐信息/聚光/大胆文案档),可与任一主题叠加、与隐私模式正交。
4. **性能护栏**:新动效只动 `transform/opacity/filter/clip-path`;禁止常驻 `will-change`(hover 挂载/leave 清除);图片 lazy+async+`aspect-ratio` 防 CLS,失败 fallback 骨架底色;粒子单 canvas ≤40 且 `document.hidden` 停摆;aura 常驻循环改"静止停表";低功耗(coarse pointer / `deviceMemory ≤ 4` / 移动端 768px 以下)自动关粒子、tilt、纱幕,运镜降纯淡入;`prefers-reduced-motion` 沿用 `.01ms` 兜底 + 新特效显式 `display:none`。
5. **曲线库**(进 tokens.css):
   ```css
   :root{
     --e-silk: cubic-bezier(.16,1,.3,1);    /* 丝:快起长尾,微交互 0.5–0.8s */
     --e-slow: cubic-bezier(.37,0,.35,1);   /* 慢:S形滑行,揭示/转场 0.9–1.6s */
     --e-burn: cubic-bezier(.55,0,1,.45);   /* 燃:蓄力后涌出,情绪爆点 1.2–2.4s */
     --e-heart: ...                          /* 心搏:两快一慢,生理节律(v2 新增) */
   }
   ```

---

## 三、分阶段路线图(v2 重排:情欲层前置;每阶段独立可合并、可回滚)

### Phase 0 · 工程基建重组 —— 0.5–1 天,零视觉变化
| # | 内容 | 落点 |
|---|---|---|
| 0.1 | theme.css 拆分为 tokens/base/motion/components | `src/styles/*`、`src/main.tsx` |
| 0.2 | aura rAF 静止停表修复(距目标 <0.5px 即 cancel) | `src/effects/ambient.ts` |
| 0.3 | 海报/缩略图 aspect-ratio 防跳动 + 失败兜底 | `components/PosterCard.tsx` |

**验收**:拆分后逐页目测与现状零差异;`npm run build` 产物为单 CSS。

### Phase 1 · 情欲感官层(v2 提为最优先)—— 2–2.5 天
| # | 内容 | 要点 |
|---|---|---|
| 1.1 | **耳语文案系统** `src/i18n/whisper.ts` | 全站按钮/标签/Toast/空态/加载文案感官化映射表;一处改全站换装:**标准模式克制版 / 密室模式大胆版**双档文案 |
| 1.2 | **解扣式披露**(海报卡) | hover 四层编排:0.2s 番号亮起(*对视*)→ 0.6s 标题滑落浮现(*靠近*)→ 1.2s 预览图薄纱擦过封面(*掀起一角*)→ 持续 2s 光泽绸缎扫过(*呼吸变沉*);触摸端长按 600ms 逐层解锁;信息层动画从"上浮"改"**滑落**"(衣袖意象) |
| 1.3 | **心跳脉搏系统** | `--e-heart` 心搏曲线 + `heartbeat()` 工具类:加载态改"心搏圈"(两快一慢缩放环)、进度条按心跳步进、主按钮待机极缓心搏、收藏瞬间心率骤升 |
| 1.4 | **唇印符号系统** | 收藏成功=💋 唇印"盖章"落在卡上(rotate 随机+半透明+盖下 scale 1.6→1 带冲击);导航活跃项小唇印;登录卡/详情卡蕾丝描边(SVG border-image,data-URI 零外网);搜索框聚焦唇色渐染 |
| 1.5 | **密室 Boudoir 模式** `[data-mode="mood"]` | 侧栏蜡烛一键入"密室":全站渐暗至烛光黑、卡片去框只留聚光(`box-shadow: 0 0 48px var(--gold-glow)`)、Wall 切 12s 慢节奏、文案自动切大胆档、壁纸层烛光摇曳增强(candleDrift);与隐私模式正交可叠加 |
| 1.6 | 暗夜丝绒主题 `[data-theme="boudoir"]` | 黑丝绒底 `#140a12`/鎏金线 `#e8c98a`/文字反白 `#f6e3ee`/detail-bg 白纱换黑纱 `rgba(20,10,16,.55→.88)`;Settings 持久化 + 跟随系统 |

**验收**:双档文案全站生效;唇印/心搏在手机端降级为静态;密室↔日常切换 1.2s 丝滑过渡。

### Phase 2 · 华丽魅惑动效 —— 2.5–3 天
| # | 内容 | 要点 |
|---|---|---|
| 2.1 | **体温系统**(v2 新增) | zustand heat store:hover(+2)/深 hover(+5)/进详情(+8)/收藏(+15)/闲置衰减(-1/10s),0–100 六档;映射 CSS 变量 `--heat:0~1`:主色饱和/亮度、烛光光晕半径、粒子密度、动效速率(`calc(var(--dur)*calc(1-.3*var(--heat)))`)——冷时矜持慢热,热时滚烫急切;侧栏烛焰图标实时反映档位(烛火大小) |
| 2.2 | 路由纱幕过场 | 双侧桃粉丝绸纱幕合拢再揭开(合 .45s 丝/停 .1s/开 .8s 慢),新页在揭开中入场——每次切页都是一次"掀纱";新增 `src/effects/transition.ts` |
| 2.3 | 海报丝绸之舞 | hover 封面慢推 scale(1.14)+「回首」位移 translateX(-2%)、光泽反向绸缎视差(`background-position` 反向扫)、金框聚光光晕随热度增强 |
| 2.4 | Wall「凝视运镜」 | 每张随机 12–20s 运镜(缓推近脸/缓拉全身/左摇右移),切换=拉焦进场(blur 18px→0)+ **切换前 1 次心搏蓄力**;文字错峰显影(番号 .3s→标题 .5s→简介 .7s);高热档运镜更慢更近 |
| 2.5 | 详情页「赴约登场」 | 黑场 .4s→追光亮起(中心亮四周暗角加深)→磨砂纱 1.5s「燃」曲线掀开(`clip-path` 揭示+`backdrop-filter` 纱层)→番号衬线落款→标题/评分/演员胶囊 120ms 阶梯入场;元数据默认收起为**"更多的她…"**(耳语文案)点击展开;背景 blur(30px)→(4px) 缓慢聚焦 |
| 2.6 | 电影调色 | 全局 SVG 噪点颗粒层(opacity .05, mix-blend overlay)、detail-bg 玫紫纱叠加(`rgba(90,20,60,.35)` multiply)、演员头像 hover 玫瑰金调(saturate 1.15 + contrast 1.06) |

**验收**:体温档位切换无跳变(所有热度映射过渡 ≥1.5s);连续切 10 页桌面 ≥55fps;reduced-motion 下体温只留色彩映射不动时长。

### Phase 3 · 仪式与沉溺 —— 2–3 天
| # | 内容 | 要点 |
|---|---|---|
| 3.1 | **「今夜情人」盲盒** `components/DailyReveal.tsx` | 丝绒红包囊→黑场烛息→先给**剪影**(封面 silhouette 滤镜+轮廓光)猜 3s→三级对焦揭晓(blur 60→18→0,每级 .7s 心搏间隔)→衬线番号落款→"就是她了 / 再换一位";种子=date 哈希当日固定;与密室模式联动默认在密室下进行 |
| 3.2 | 心动仪式 `components/Heartburst.tsx` | 收藏/关注:描边爱心升起 + 玫瑰光屑散落(5–6 片随机方向 translate+scale+opacity,600ms 自毁)+ 唇印盖章 + 按钮两快一慢心搏 + 耳语 Toast("♥ 已把她放进你的心里");泛化现有 heart-burst |
| 3.3 | 烛光尘埃 | 单 canvas ≤40 暖粉尘埃上浮+呼吸明暗(`opacity .15–.35` 正弦);密度随体温档提升;低功耗自动关 |
| 3.4 | 封蜡揭幕 | 订阅新作=封蜡信封态(磨砂+蜡封图标+倒计时,`sealed/upcoming/ready` 三态);到点首次 hover 蜡封碎裂飞散(::before/::after 分别动画)→掀纱显封面 |
| 3.5 | scroll 叙事 | 榜单柱条滚动生长(`animation-timeline: view(); animation-range: entry 10% cover 30%`,IO 回退)、页头随滚动视差渐隐 |
| 3.6 | 排版奢华化 | Cormorant Garamond display 字体、标题 em 渐变烫金(`#b98a4a→#f7e3b0→#ff4fa3` background-clip:text + shimmer)、眉题菱形装饰、香槟金缎面主按钮(satin 渐变流光)、暗色模式番号烫金 |

**验收**:粒子在手机/低功耗确认关闭;盲盒无数据日有优雅空态。

---

## 四、风险与对策

| 风险 | 对策 |
|---|---|
| 拆 CSS 级联顺序错漏 | Phase 0 合并后关键页面截图 diff;纯重构不改规则体 |
| 暗主题漏改硬编码色 | 变量化专项:全文搜 `#fff`/`rgba(255,`,统一入 tokens |
| 手机端动效过载发热 | 低功耗块统一管控:关粒子/tilt/纱幕/体温时长调制(只留色彩映射) |
| blur 与 backdrop-filter 同帧叠加掉帧 | 纱幕层与聚光层分时触发;粒子 canvas 与 CSS 动效不叠帧 |
| MutationObserver 大列表开销 | stagger 每批 ≤24 元素;沿用 150ms 防抖 |
| 装饰资源打外网 | 颗粒/光斑/纱/蕾丝全部 CSS/SVG data-URI 生成,零新增图片资源 |
| **体温系统"闪烁焦虑"(v2 新增)** | 所有热度映射过渡 ≥1.5s,档位间缓动不跳变 |
| **文案尺度(v2 新增)** | 大胆档仍保持暗示而非露骨,截图/旁观场景不尴尬;双档随时一键切换 |

---

## 五、涉及文件总表

- `frontend/src/styles/`(拆分重组;tokens 增 `--heat`/`--e-heart`)
- `frontend/src/effects/ambient.ts`(aura 停表修复 + 粒子层 + 体温写 CSS 变量 + tilt 低功耗降级)
- 新增:`src/effects/transition.ts`(纱幕过场)、`src/i18n/whisper.ts`(双档文案)、`src/store/heat.ts`(体温)、`components/DailyReveal.tsx`、`components/Heartburst.tsx`、`components/KissMark.tsx`(唇印)
- 修改:`pages/Wall.tsx`(凝视运镜/心搏蓄力/慢节奏)、`TaskDetail.tsx`、`ActorDetail.tsx`(赴约登场)、`Subscriptions.tsx`(封蜡)、`Settings.tsx`(主题切换)、`Login.tsx`(调色/蕾丝)、`components/PosterCard.tsx`(解扣披露/丝绸之舞/画框)、`components/Sidebar.tsx`(蜡烛开关+烛焰热度计)、`store/`(mood/theme 持久化)

---
---

# 附录:四部门原始提案(实施参考,含代码草图)

## 附录 A · 视觉设计部「暗夜丝绒」方案

### 现状诊断
1. 只有亮桃粉一档温度,夜间观影场景刺眼且"甜"多于"惑";`.terminal` 深酒红底证明暗色语言天然契合。
2. 主色系单一饱和,缺香槟金/丝绒紫配角金属色,华丽感停留在"粉色发光"一层。
3. 海报呈现是"裸片"而非"藏品":无画框、鎏金卡纸、聚光投射等博物馆式包装。
4. 图片滤镜未做影调统一:封面色温杂乱,缺胶片颗粒/双色调等电影感调色。
5. 动效多但同质:全是"光"类动效,缺丝绒质感的慢速阴影/绸缎流光。

### A1 「Dark Velvet」暗色主题
```css
[data-theme="boudoir"]{
  --bg-ink:#140a12; --bg-page:#1d0e18; --bg-surface:rgba(38,18,30,.72);
  --bg-raised:#2a1422; --bg-overlay:rgba(24,11,19,.88);
  --line-hair:rgba(255,143,179,.10); --line-soft:rgba(255,143,179,.18);
  --t-display:#f6e3ee; --t-body:#d9b8ca; --t-mute:#a87b93; --t-faint:#7d5568;
  --gold:#ff4fa3; --gold-glow:rgba(255,79,163,.40);
  --champagne:#e8c98a; --champagne-glow:rgba(232,201,138,.30);
  --shadow-card:0 8px 28px rgba(0,0,0,.5),0 0 0 1px rgba(255,143,179,.08);
}
[data-theme="boudoir"] body::before{
  background:
    radial-gradient(60% 42% at 18% 0%, rgba(255,20,147,.14), transparent 70%),
    radial-gradient(46% 38% at 85% 90%, rgba(232,201,138,.08), transparent 70%),
    #140a12;
}
[data-theme="boudoir"] .card{background:rgba(38,18,30,.66);backdrop-filter:blur(14px)}
```
detail-bg 遮罩由白粉渐变换黑纱 `rgba(20,10,16,.55→.88)`。

### A2 海报「鎏金画框 + 聚光」
```css
.poster-frame{
  padding:6px; /* 金色卡纸边 */
  background:linear-gradient(160deg,#3a2418,#1c1008 40%,#4a3420);
  box-shadow:0 10px 30px rgba(0,0,0,.45),inset 0 0 0 1px rgba(232,201,138,.35);
}
.poster-frame .frame-inner{border-radius:calc(var(--r-md) - 3px);overflow:hidden;
  box-shadow:inset 0 0 0 1px rgba(232,201,138,.45)}
.poster::after{content:"";position:absolute;left:10%;right:10%;bottom:-18px;height:22px;
  background:radial-gradient(50% 100% at 50% 0,var(--champagne-glow),transparent 75%);
  opacity:0;transition:opacity .4s;filter:blur(6px)}
.poster:hover::after{opacity:1}
```
conic 流光环换香槟金序列 `#e8c98a,#ff4fa3,#fff0dd,#e8c98a`;podium 加宽金框。

### A3 香槟金缎面材质
```css
:root{--satin:linear-gradient(105deg,#b98a4a 0%,#f3dfae 28%,#c9a05e 46%,#fdf1cd 62%,#b98a4a 100%)}
.btn--gold{background:var(--satin);background-size:220% 100%;color:#3d1526;animation:satinFlow 7s linear infinite}
@keyframes satinFlow{to{background-position:220% 0}}
.eyebrow,.winfo-code,.cap-code{color:var(--champagne)} /* 暗色模式番号烫金 */
```

### A4 电影调色 + 胶片颗粒
```css
.grain{position:fixed;inset:0;z-index:3;pointer-events:none;opacity:.05;mix-blend-mode:overlay;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E")}
```

### A5 排版奢华化
```css
:root{--ff-display:"Cormorant Garamond","Playfair Display","Noto Serif SC",serif}
.page-title em{background:linear-gradient(120deg,#b98a4a,#f7e3b0,#ff4fa3);
  -webkit-background-clip:text;color:transparent;animation:goldShimmer 5s ease-in-out infinite}
.eyebrow::before,.eyebrow::after{content:"◆";font-size:6px;color:var(--champagne)}
```

## 附录 B · 动效导演部「慢镜蔷薇」方案

### 现状诊断
1. 曲线单一(全站几乎一条 cubic-bezier(.23,1,.32,1)),hover/入场 0.3–0.6s 偏快,缺"慢镜头撩人"呼吸感。
2. 无路由转场语言:切页只有 .page 0.35s 上浮淡入。
3. Wall 运镜单镜头:只有一组固定 kenburns 24s,6s 切换与 24s 推镜节奏打架。
4. 海报 hover 是"位移堆叠"而非"舞蹈":上浮+scale+tilt 同层叠加,无多层视差。
5. 氛围层只有"光":缺花瓣、尘埃、烟雾等可感知慢速介质。

### B1 路由纱幕过场
```css
.veil{position:fixed;inset:0;z-index:500;pointer-events:none;display:grid}
.veil i{background:linear-gradient(100deg,rgba(255,228,238,0),rgba(255,143,179,.55),rgba(255,228,238,0));transform:scaleY(0)}
.veil.play i{animation:veilPass 1.35s var(--e-slow) forwards}
@keyframes veilPass{0%{transform:scaleY(0);transform-origin:bottom}38%{transform:scaleY(1)}100%{transform:scaleY(0);transform-origin:top}}
```

### B2 海报丝绸之舞(三层三速)
```css
.poster:hover .poster-frame img{transform:scale(1.14) translateX(-2%);transition:transform 1.2s var(--e-slow)}
.poster-frame::after{background:linear-gradient(105deg,transparent 30%,rgba(255,255,255,.28) 45%,transparent 60%);
  background-size:250% 100%;background-position:120% 0;transition:background-position 1.4s var(--e-slow)}
.poster:hover .poster-frame::after{background-position:-20% 0}
.poster:hover .poster-info{transition:transform .9s .15s var(--e-slow),opacity .9s .15s var(--e-slow)}
```

### B3 Wall 导演运镜 + 拉焦
```tsx
const MOVES=['zoomIn','zoomOut','panL','panR']; const mv=MOVES[index%4]  // key=index 自动重播
```
```css
@keyframes zoomIn{from{transform:scale(1)}to{transform:scale(1.12)}}
@keyframes panL{from{transform:scale(1.15) translateX(3%)}to{transform:scale(1.15) translateX(-3%)}}
.wstage{animation:focusPull 1.4s var(--e-slow)}
.winfo>*{animation:softRise .9s var(--e-silk) backwards}
.winfo>*:nth-child(2){animation-delay:.2s}.winfo>*:nth-child(3){animation-delay:.4s}
```

### B4 详情页面纱揭示
```css
.detail-cover .veil-layer{position:absolute;inset:0;backdrop-filter:blur(14px);background:rgba(255,228,238,.5);
  animation:veilLift 1.5s var(--e-burn) .2s forwards}
@keyframes veilLift{to{clip-path:inset(100% 0 0 0);opacity:0}}
.detail-bg img{animation:bgFocus 2.4s var(--e-slow)}
@keyframes bgFocus{from{filter:blur(30px) brightness(1.2)}}
```

### B5 烛光尘埃粒子
```ts
function initMotes(){ const c=Object.assign(document.createElement('canvas'),{className:'motes'})
  document.body.appendChild(c); const ctx=c.getContext('2d')!
  const ps=Array.from({length:36},()=>({x:Math.random()*innerWidth,y:Math.random()*innerHeight,
    r:.5+Math.random()*1.8,v:.06+Math.random()*.15,ph:Math.random()*7}))
  ;(function loop(t){ if(!document.hidden){ ctx.clearRect(0,0,c.width,c.height)
    for(const p of ps){ p.y-=p.v; const a=.15+.2*Math.sin(t/1800+p.ph)
      ctx.fillStyle=`rgba(255,143,179,${a})`; ctx.beginPath();ctx.arc(p.x+8*Math.sin(t/4000+p.ph),p.y,p.r,0,7);ctx.fill()
      if(p.y<-4){p.y=innerHeight+4;p.x=Math.random()*innerWidth} } }
    requestAnimationFrame(loop) })(0) }
```

### B6 scroll-driven 叙事
```css
.bar{animation:barGrow .9s var(--e-slow) both;animation-timeline:view();animation-range:entry 10% cover 30%}
.page-head{animation:headDrift linear both;animation-timeline:scroll(root)}
@keyframes headDrift{to{transform:translateY(10px);opacity:.55}}
```

## 附录 C · 感官体验部「欲焰诱惑力」方案

### 现状诊断
1. 信息一次性全给,没有期待感:披露是"开关式"而非"渐进式"。
2. 心动时刻无仪式:收藏仅按钮文案切换,情绪峰值为零。
3. 亮桃粉全天候,缺"夜晚人格";夜间才是真实主场景。
4. 详情页是"资料卡"不是"登场",入场即剧透。
5. 推荐是列表不是悬念,无揭幕、无倒计时。

### 体验三原则
1. **慢一拍才撩人(Restraint)**:所有披露动效刻意延迟 300–800ms 分层浮现;快是工具,慢是欲望。
2. **遮比露更性感(Reveal over Show)**:信息永远分层解锁,每次交互只换来"再多看到一点"。
3. **心动要有回响(Reward the Heart)**:情感动作必须获得超过功能预期的感官反馈。

### C1 挑逗式渐进披露(Tease Hover)
hover → 0.2s 封面微放大+边缘泛桃粉描边("注意到你了")→ 0.5s 番号标题渐显 → 1.2s 预览图薄纱擦除扫过 → 松手退场。预览图用 `mask-image: linear-gradient` + `mask-position` 动画擦除;`@media (hover:none)` 降级长按 600ms 揭晓。

### C2 烛光客厅氛围模式(Mood Mode)
```css
[data-mode="mood"]{
  --bg-ink:#17060d; --bg-page:#1f0912; --bg-surface:#2a0d18;
  --t-display:#f7d6e2; --t-body:#e8b8c9; --line-hair:rgba(255,20,147,.18);
}
[data-mode="mood"] .poster{border:none;
  box-shadow:0 0 48px rgba(255,20,147,.14);border-radius:var(--r-lg)}
```
卡片去边框只剩聚光、轮播 12s 慢节奏、信息层默认隐去。

### C3 「今晚看什么」盲盒揭幕(v2 升级为「今夜情人」)
全屏黑场+烛光呼吸 → (v2 新增:先给剪影猜 3s) → 封面三级对焦(blur 60→18→0,每级 700ms)→ 番号衬线落款 → "就是她了 / 换一位"。种子 = date 哈希当日固定。

### C4 心动反馈仪式
爱心上升放大 + 5–6 片玫瑰光屑散落 + 按钮呼吸 + 感官化文案。

### C5 详情页「登场」编排
模糊封面黑场 400ms 静默 → 追光亮起 → 番号大字浮起 → 元数据阶梯入场 → 元数据默认收起"更多的她…"。

### C6 新作倒计时揭幕
封蜡信封态(倒计时) → 到点 hover 蜡封碎裂 → 纱罩擦除显封面。

## 附录 D · 前端工程部落地方案

### 现状评估
1. 零动效依赖,基建健康:仅 react/router/zustand/axios;ambient.ts 事件委托+rAF+MutationObserver 模式合理,是理想扩展基座。
2. theme.css 765 行单文件是最大技术债,升级后预计 1500+ 行,必须先拆再扩。
3. 已有滚动显现(IO + .rv)与图片 lazy 渐显;PosterCard 失败图 display:none 会布局跳动。
4. 单用户局域网:可大胆用现代 CSS(color-mix/@supports/scroll-driven 渐进增强)。
5. aura 常驻 rAF 循环即使鼠标静止也在跑,是持续 CPU 消耗点。

### 技术决策(结论)
1. **CSS 拆分模块化多文件,不引框架**(Vite @import 打包为零成本);回滚=revert 一个 commit。
2. **不引 framer-motion**:动效全是装饰性、无布局动画/拖拽需求,~30KB gzipped 换不来对等价值;ambient.ts 委托模式零 React 侵入,15 页面无需改动。
3. **性能护栏**:帧预算 8ms;will-change 只 hover 挂载;合成层预算 ≤30;aura 静止停表;低功耗(`deviceMemory ≤ 4`/coarse)关 aura+tilt。
   ```ts
   const lowPower = coarse || (navigator as any).deviceMemory <= 4
   if (!lowPower) initAura()
   initTilt()  // tilt 改为 lowPower ? 0 : 6 度
   ```
4. **双主题 CSS 变量 + data-theme 切换 + Zustand 持久化**;`color-scheme` 同步。

### 风险清单
1. 拆 CSS 引入顺序错漏 → 逐页目测比对+截图 diff
2. 暗主题硬编码色漏改 → 全文搜 `#fff`/`rgba(255` 统一入变量
3. 手机端动效过载发热 → coarse 降级保留并扩展到新特效
4. 装饰背景打外网 → 全部 CSS 生成,零图片资源
5. MutationObserver 大列表开销 → 150ms 防抖 + stagger 每批 ≤24 元素
