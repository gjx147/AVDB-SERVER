# 「欲焰 · Eros V4」更色情·更诱惑 优化方案

> **版本**:4.1 | **日期**:2026-08-19 | **状态**:实施中
> 四部门合议（魅态视觉部 / 挑逗互动部 / 情话文案部 / 声乐节奏部）+ 总编室裁决与工程评审
> 前置:V2 Boudoir + V3 Eros 已落地;V3-2 衣衫与雾已按用户要求取消;详情页已重设计
> **v4 主旨**:从「她被撩动」推进到「她反过来撩你」——V2/V3 是她回应你的操作,v4 让她主动:看你、贴近你、回吻你、事后的余韵都属于你。
> **v4.1 用户增补**:①声音全部以「做爱时的喘息声」为核心——呼吸层改为拟人性喘息(随体温从轻浅变急促);②侧边栏导航文字双模式(正常/情话)+ 切换按钮。

---

## 一、v4 设计总纲:「她先动了」

| 层 | v3 已有 | v4 进阶 |
|---|---|---|
| 视觉 | 锁骨构图/红灯区/辉光锚点 | **汗珠珠光**(皮肤高光)、**剪影轮廓光**、**事后余红**、**唇印残留** |
| 交互 | 抚摸轨迹/喘息/娇嗔/厮磨 | **视线追随**(她看着你的鼠标)、**亲密距离**(40px 预感应)、**回吻**(双击)、**余韵状态机** |
| 文案 | 三档+状态化初见 | **第四档「欲火」**(fever)、**状态联动气泡**(热度/喘息/事后/深夜)、**AI 情话 prompt 强化** |
| 声效 | 心跳/轻叹/烛火/杯碰 | **呼吸循环**(随体温加深)、**心跳失序**(climax 前骤停)、**余韵耳鸣音**、**轻哼**、**湿唇音** |

**用户硬约束（全部继承）**:①不重复已取消的 V3-2 衣衫与雾机制;②文字可读性不牺牲（光效 z-index 低于文字层、玻璃底不透明度不再下调）;③默认静音/手势解锁/分总线音量策略延续;④色而不俗的文学品位线;⑤reduced-motion/移动端/低功耗三通道降级。

---

## 二、四部门提案

### 部门 A · 魅态视觉部「肉与光」

**A1 汗珠珠光（Sheen Dew）— P0**
勾引机制:皮肤的反光是最赤裸的暗示。海报 hover 时,封面叠一层跟随鼠标的椭圆珠光高光——像灯光下肌肤泛起的水光。复用现有 `--mx/--my`(initSpotlight 已在写)。
```css
.poster-sheen-dew{position:absolute;inset:0;z-index:2;pointer-events:none;opacity:0;
  background:radial-gradient(90px 60px at var(--mx,50%) var(--my,50%),
    rgba(255,244,250,.30),rgba(255,214,236,.12) 45%,transparent 70%);
  mix-blend-mode:soft-light;filter:blur(1px);
  transition:opacity .5s var(--e-slow)}
.poster:hover .poster-sheen-dew{opacity:1}
```
应用:影片库海报卡、详情页大封面(光斑更大 140px)、演员头像。移动端/低功耗关闭。

**A2 剪影轮廓光（Rim Light）— P1**
勾引机制:舞台背光勾出身体轮廓——演员头像 hover 时背后泛一圈绯红轮廓光,像暗房里被灯光描出线条。
```css
.actor-photo::after{background:
  linear-gradient(200deg,transparent 55%,rgba(61,21,38,.26) 100%),
  radial-gradient(70% 46% at 50% 18%,transparent 52%,rgba(255,20,147,.10) 100%)}
.actor:hover .actor-photo::after{background:
  linear-gradient(200deg,transparent 55%,rgba(61,21,38,.18) 100%),
  radial-gradient(72% 48% at 50% 20%,transparent 48%,rgba(212,18,90,.30) 100%);
  transition:background .6s var(--e-slow)}
```
应用:演员库网格、详情页演员栏。

**A3 事后余红（Afterglow Tint）— P1**
勾引机制:高潮之后脸上未褪的红。climax/融化后 8s 内,全局一层极浅绯红滤镜从 12% 渐隐到 0——不是闪一下,是慢慢消退的余韵。
```css
html.afterglow .app::after{background:radial-gradient(ellipse at center,transparent 45%,rgba(212,18,90,.12) 100%);
  transition:background 8s var(--e-slow)}
/* JS:climax 后加 .afterglow,8s 后移除(移除时渐变自然回落) */
```
应用:全局。

**A4 唇印残留（Kiss Trail）— P2**
勾引机制:她吻过的地方留着痕。回吻/收藏时在触发点留下一枚 2s 渐隐的小唇印残迹(现有 kiss-stamp 的 mini 版,定位到 pointer 坐标)。
```css
.kiss-trail{position:fixed;z-index:600;font-size:18px;pointer-events:none;
  filter:drop-shadow(0 0 6px rgba(212,18,90,.6));
  animation:kissTrail 2s var(--e-slow) forwards}
@keyframes kissTrail{0%{opacity:.95;transform:scale(1.2) rotate(-12deg)}
  30%{opacity:.7;transform:scale(1)}100%{opacity:0;transform:scale(.9) translateY(-10px)}}
```
应用:双击回吻触发点。

### 部门 B · 挑逗互动部「她先动了」

**B1 视线追随（Eye Contact）— P0**
剧本:鼠标移动,演员头像的目光跟着你走——img 向指针方向微移 2-4px(视差),像她倾身看你;离开时缓缓回正。情绪:被注视的酥麻。
```ts
// ambient.ts initGaze():pointermove 在 .actor-photo 内,算指针相对中心的偏移
// el.style.setProperty('--gx', `${dx/rect.width*4}px`) 同 --gy
```
```css
.actor-photo img{transform:translate(var(--gx,0),var(--gy,0));transition:transform .4s var(--e-silk)}
.actor:not(:hover) .actor-photo img{transform:translate(0,0)}
```
触屏/低功耗关闭;位移 ≤4px 不引起晕动。应用:演员库、详情页演员栏。

**B2 亲密距离（Intimacy Proximity）— P0**
剧本:指针还没碰到海报,40px 外她先有反应——卡片微微抬起、边缘泛光、光晕预亮;进入才完全亮起。像她先注意到你,等着你靠近。
```ts
// ambient.ts initProximity():pointermove 检测距 .poster 边界 <40px 且未 hover
// el.classList.add('prox');离开 40px 移除
```
```css
.poster.prox .poster-frame{transform:translateY(-2px);box-shadow:0 2px 16px rgba(212,18,90,.16);
  transition:transform .4s var(--e-silk),box-shadow .4s}
```
应用:影片库画廊。移动端关闭。

**B3 回吻（Kiss Back）— P1**
剧本:双击封面=她回吻你——不是收藏,是她主动的一下:触发点绽开小唇印残迹+迷你心爆,配湿唇音,toast 低语(随机"回吻你"池)。情绪:被回敬的心跳漏拍。
```ts
// PosterCard onDoubleClick(desktop only):spawn .kiss-trail at e.clientX/Y + mini Heartburst + audio.play('lip') + toastOk(whisper('kiss_back'))
```
应用:影片库海报卡。触屏不可用(无双击),自然降级。

**B4 余韵状态机（Afterglow）— P1**
剧本:climax 颤栗之后,不是立刻冷静——5s 的"事后":心跳骤降曲线、全局余红渐隐(A3)、侧栏问候换事后语、呼吸声变深变慢。情绪:餍足。
```ts
// store.addExcite 顶点时:set({ afterglow: true });8s 后 false
// audio.setAfterglow(true)→BPM 曲线骤降、breath 变深
```
应用:全局,与 A3/C4/D3 三部门共用同一状态。

### 部门 C · 情话文案部「她的话更烫了」

**C1 第四档「欲火档」fever（tier 3）— P0**
比 sultry 再烫一档:直白滚烫但仍不脏、不直呼器官。深夜加成封顶从 2 升到 3。设置页文案档加一档「欲火」。
| key | sultry(2) | fever(3) |
|---|---|---|
| loading | 宽衣需要一点时间,别偷看 | 她在准备,已经等不及了 |
| fav_add | 把她压进心底,锁好 | 烙进心里,拔不出来了 |
| btn_see | 挑开那层纱,看仔细 | 看她看你,谁先受不了 |
| download_done | 她已经躺在你硬盘里了 | 她整个身子都是你的了 |
| take | 现在就要,一次到底 | 别等了,她现在就要 |
| greet_night | 这个点进来的,都没安好心——包括我 | 这么晚还不睡,是在想我吗 |
| kiss_back | 回吻你一下 | 回吻你,很用力 |

**C2 状态联动气泡 — P0**
泛化 sulk-bubble 为 status-bubble:heat>60 / excite>60 / sulk / afterglow / 深夜,各配专属低语,自动出现 3s 渐隐(带 60s 冷却防刷屏)。
- heat>60:「你把我弄得好热…」
- excite>60:「慢一点,我快跟不上了」
- sulk:「急什么呀…」(已有)
- afterglow:「刚才…很好。」
- 深夜:「夜还长,别急着走」

**C3 AI 耳语 prompt 强化 — P1**
system prompt 追加意象指令:多用肌肤、体温、呼吸、距离、渴的暗喻;tone 指令随档位加温。完整替换文本见附录。温度 0.9 保持。

**C4 事后文案 — P2**
afterglow 期间侧栏问候换池:「她满足地靠在你肩上」/「事后烟要吗」/「别说话,再抱一会儿」。

**C5 侧边栏双文案模式 — P0（v4.1 新增）**
侧边栏导航文字两种表达:normal(正常)与 whisper(情话),切换按钮放侧边栏底部(烛光密室旁)。状态 `navMode: 'normal' | 'whisper'` 持久化 localStorage,独立于文案档位。
| 正常 | 情话 |
|---|---|
| 首页 | 今夜的她 |
| 仪表盘 | 心跳记录 |
| 影片库 | 群芳谱 |
| 收藏 | 心尖上 |
| 演员库 | 佳人们 |
| 排行榜 | 群芳榜 |
| 列表源 | 猎场 |
| 爬取控制台 | 潜入暗房 |
| 订阅 | 挂念的人 |
| 下载历史 | 收入囊中 |
| 下载器 | 接她回家 |
| 设置 | 闺房布置 |

### 部门 D · 声乐节奏部「听得见的心跳」

**D1 喘息声循环（Moaning Breath）— P0（v4.1 重设计）**
heat>25 启动拟人性喘息层:粉噪双 formant 带通(600Hz/900Hz 微颤,模拟人声共鸣腔)+ 周期性包络(吸气渐强-呼气渐弱-呼气尾端 4-6Hz 气声微颤)。**heat 越高喘息越急促越深**:呼吸周期 2.8s→0.9s、音量 0.015→0.05、微颤加深。与心跳叠加,共用 physio 总线;默认静音、手势解锁、document.hidden 停、移动端/低功耗关闭。
```ts
// voices.ts VOICES.set('moan', (t) => {
//  粉噪 → bp1(600Hz,Q1.2) → bp2(900+颤音LFO,Q1.5) → gain
//  包络循环:sin 半波 × (1+heat) 控 gain;周期 = 2.8 - 1.9*heat 秒
//  呼气尾端:4-6Hz 振幅微颤(heat 越高越明显);return stop })
```

**D2 心跳失序（Arrhythmia）— P0**
climax 前 2s BPM ×1.6 加速,顶点骤停 0.4s(一次漏拍),之后 3s 内回落到基础 BPM。戏剧性的"心律不齐"。
```ts
// engine:setClimaxing(true)→beat 间隔 = 60000/(bpm*1.6);顶点 setTimeout(停 .4s)→恢复
```

**D3 余韵耳鸣音（Tinnitus）— P1**
climax 后 1.8kHz sine 高频衰减尾音 3s(音量 0.02,像耳边的高频余振)。
```ts
VOICES.set('tinnitus', (t) => { osc 1.8kHz → gain 0.02 → expRamp 3s })
```

**D4 轻哼（Moan-lite）— P1**
收藏/融化时的气声短哼:400-600Hz 带通微颤粉噪 0.5s,音量 0.04——严格"若有似无"。

**D5 湿唇音（Lip）— P2**
双击回吻的短促唇音:800Hz 正弦 30ms click + 高频气声 80ms。

**D6 climax 音序 — P0**
前奏(D2 心跳加速+呼吸变深)→ 顶点(漏拍骤停+D4 轻哼+D3 余韵音同帧)→ 恢复(3s 回落+呼吸变慢)。

---

## 三、总编室裁决（合并与冲突消解）

1. **余韵三合一**:交互部 afterglow 状态机 = 唯一状态源,驱动视觉部 A3 余红、文案部 C4 事后语、声效部 D3 耳鸣音 + D2 回落曲线。store 加 `afterglow: boolean` + `climaxing: boolean`。
2. **回吻闭环**:B3 双击回吻 = A4 唇印残迹 + 迷你心爆 + D5 湿唇音 + C1 kiss_back 低语——「她的回应」三件套的 v4 版本。
3. **视线追随与亲密距离叠加规则**:gaze 位移 ≤4px(演员头像),proximity 只做 translateY+光晕(海报卡)——两者不作用于同一元素,无冲突;均降级关闭于 coarse/低功耗。
4. **fever 档与深夜加成**:tier 0-3,深夜 +1 封顶 3;密室开关不再联动档位(设置页独立选)。
5. **声效节流**:呼吸层单实例;轻哼/湿唇音 300ms 去抖;climax 音序单次触发(会话内 3 次上限,与颤栗同限)。
6. **可读性红线**:所有新光效 z-index ≤2(低于文字层)、mix-blend 仅用于高光层、玻璃底不透明度不回退。

## 四、工程评审

- 全部在既有架构扩展:eros.css(视觉)、ambient.ts(视线/亲密/回吻)、audio/(呼吸/失序/音序)、whisper.ts(fever 档/气泡)、store(afterglow/climaxing)——零新依赖、零新文件
- 视线追随复用 --mx/--my 数据流与 transform-only 动效规范;位移 ≤4px 无晕动风险
- 呼吸循环:document.hidden 停、默认静音、physio 总线音量统一管控
- 气泡冷却 60s 防刷屏;文字层 z-index 高于新光效
- 降级:coarse/移动端关 gaze/proximity/dew;reduced-motion 关呼吸循环与失序曲线(只留静态色彩)

## 五、分阶段路线图（总估 3.5–4 天,每阶段独立可合并可回滚）

### V4-1 「她的体温」—— 0.5–1 天
D1 呼吸循环 + D2 心跳失序 + D6 climax 音序 + store climaxing/afterglow 状态

### V4-2 「她的目光」—— 1–1.5 天
A1 汗珠珠光 + B1 视线追随 + B2 亲密距离 40px 预感应 + A2 剪影轮廓光

### V4-3 「她的余韵」—— 0.5–1 天
B3 回吻(双击+唇印残迹+湿唇音) + B4 余韵状态机(A3 余红 + C4 事后语 + D3 耳鸣音) + D4 轻哼

### V4-4 「她的情话」—— 0.5–1 天
C1 fever 档(全 key 对照表+设置页第四档按钮+深夜封顶 3) + C2 状态联动气泡 + C3 AI prompt 强化

## 六、涉及文件

- `frontend/src/styles/eros.css`(A1-A4 视觉)、`frontend/src/effects/ambient.ts`(B1 视线/B2 亲密/B3 回吻)、`frontend/src/audio/voices.ts`+`engine.ts`(D1-D6)、`frontend/src/i18n/whisper.ts`(C1/C2/C4)、`frontend/src/store/useStore.ts`(afterglow/climaxing)、`frontend/src/pages/Settings.tsx`(fever 档按钮)、`frontend/src/components/PosterCard.tsx`(dew 层/双击回吻)、`frontend/src/components/Sidebar.tsx`(afterglow 问候)、`backend/services/ai_service.py`(whisper prompt 强化)

## 附录 · AI 耳语强化 prompt（C3 完整替换文本）

system 追加段:「你的修辞素材:肌肤的温度、呼吸的深浅、心跳的快慢、两个人之间的距离、喉咙里的干渴、衣料的重量。用这些写欲望,让它像皮肤一样可感。可以暗示下一步,但永远不把下一步说出来——说破就不美了。」
tone 指令替换:
- 0:「克制:像指尖在杯沿上打转,碰到了但不说。」
- 1:「大胆:像解开的第一个扣子,呼吸已经不同。」
- 2:「滚烫:像贴着皮肤的耳语,句句都是邀请。」
- 3(新增):「欲火:只差最后一层窗户纸,每个字都带着喘息。」
