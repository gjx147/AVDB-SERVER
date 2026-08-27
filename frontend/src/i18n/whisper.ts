/** 耳语文案系统 v3 —— 三档 × 随机池 × 深夜加成。
 *  人格：影库的女主人。声音低而近（短句/留白/多称"你"）；性格知情识趣，
 *  永远比你半步从容；说话方式：欲望写得像诗（温度/重量/距离/衣料的暗喻，
 *  不直呼器官不写脏字）。
 *  档位：0 tame 克制 / 1 bold 大胆 / 2 sultry 露骨。
 *  深夜（22:00–05:00）自动升一档（可关）。whisper() 任意位置可调；
 *  useWhisper() 组件内订阅档位变化。 */
import { useStore } from '../store/useStore'

type Pools = Record<string, [string[], string[], string[]]>

/* 每键三档，各档随机池（同键连续两次不重复） */
const COPY: Pools = {
  loading: [
    ['加载中…', '请稍等…'],
    ['她在更衣，稍等…', '马上就好，别急…'],
    ['宽衣需要一点时间，别偷看', '慢工出细活，乖，等着'],
  ],
  loading_wall: [
    ['正在挂今晚的影视墙…'],
    ['今夜的她正在登场…'],
    ['今晚她们排着队等你翻牌'],
  ],
  empty_lib_title: [['还空着呢', '库还空着'], ['这里还空着'], ['这里还空着', '床还空着一半']],
  empty_lib_sub: [
    ['去把她们带回来——先扫描列表源，或按番号创建任务。'],
    ['等你带她回家——先扫描列表源，或按番号创建任务。'],
    ['别让她们在外面临风，接她回来——先扫描列表源，或按番号创建任务。'],
  ],
  empty_wall_title: [['墙还空着'], ['今晚还没有人赴约'], ['今晚还没有人赴约', '床还空着一半']],
  empty_wall_sub: [
    ['去把她们带回来——先扫描列表源，或按番号创建任务。'],
    ['别让今晚空着——去把她们带回来。'],
    ['别让她们在外面临风，接她回来。'],
  ],
  fav_add: [
    ['心动了，收进私藏'],
    ['♥ 心动了，她已经是你的人'],
    ['把她压进心底，锁好'],
  ],
  fav_remove: [
    ['已取消收藏'],
    ['收回了这份心动'],
    ['放开手，让她再野一会儿'],
  ],
  btn_fav: [['收藏'], ['心动了'], ['圈住她']],
  btn_unfav: [['取消收藏'], ['收回心动'], ['放开她']],
  btn_bring: [['把她带回家'], ['带她回家，今晚'], ['今夜，就在这里']],
  btn_see: [['看得更清楚一点'], ['再看清一点，别急'], ['挑开那层纱，看仔细']],
  btn_seeing: [['正在看清…'], ['正在看清…'], ['别急，这就让你看清楚']],
  take: [['占为己有'], ['现在就要'], ['现在就要，一次到底']],
  taken: [['已占为己有'], ['已经是你的了'], ['她身上已经有你的名字']],
  similar_title: [['同样让你心动'], ['和她们也有纠缠'], ['和她眉眼相似的，也一样危险']],
  /* ── V3 新增 ── */
  carousel_line: [
    ['今晚为你挑的一部'],
    ['她说今晚想见你'],
    ['今晚的她，是为你湿了笔墨的一页'],
  ],
  blindbox: [['随机来一部'], ['闭眼，把手给我'], ['闭上眼，我来替你选，后果自负']],
  blindbox_result: [['是她'], ['缘分把她推到你面前'], ['命运把她按进你怀里']],
  download_start: [
    ['已开始下载'],
    ['她在来的路上了'],
    ['她正脱下网络的衣裳朝你走来'],
  ],
  download_done: [['下载完成'], ['她到家了'], ['她已经躺在你硬盘里了']],
  download_pause: [['已暂停'], ['她在门口停了一下'], ['她停在门口，问你还要不要']],
  wax_on: [['已封蜡'], ['这段记忆封存了'], ['把这夜封进蜡里，没人能碰']],
  wax_off: [['已解封'], ['重新揭开这一夜'], ['指尖挑开蜡封，旧夜重新发烫']],
  err_load: [
    ['加载失败，请重试'],
    ['人家走神了，再试一次嘛'],
    ['刚才想你想岔了，再来一次'],
  ],
  err_network: [
    ['网络异常，请检查连接'],
    ['线断了，稍等我接上'],
    ['别急，让我把灯重新点亮'],
  ],
  err_scrape: [
    ['抓取失败，请稍后重试'],
    ['她躲起来了，没找到'],
    ['她玩了捉迷藏，我们换个法子逮她'],
  ],
  greet_morning: [
    ['早安，影库已就绪'],
    ['早啊，她们都还睡着'],
    ['醒了？昨晚的事，先不说破'],
  ],
  greet_noon: [
    ['午后好'],
    ['午后犯困？她们陪你'],
    ['这个点就想？嗯，我懂'],
  ],
  greet_night: [
    ['晚上好'],
    ['夜里才刚开始'],
    ['这个点进来的，都没安好心——包括我'],
  ],
}

export type WhisperKey = keyof typeof COPY

function isNight(): boolean {
  const h = new Date().getHours()
  return h >= 22 || h < 5
}
export { isNight }

/** 当前生效档位（copyTier + 深夜加成，封顶 2） */
export function effectiveTier(): 0 | 1 | 2 {
  const tier = useStore.getState().copyTier
  const boost = useStore.getState().nightBoost && isNight() ? 1 : 0
  return Math.min(2, Math.max(0, tier + boost)) as 0 | 1 | 2
}

/* 同键连续去重 */
const lastPick = new Map<string, string>()
function pick(key: string, tier: 0 | 1 | 2): string {
  const pool = COPY[key]?.[tier] ?? COPY[key]?.[0] ?? ['']
  if (pool.length === 1) return pool[0]
  let s = pool[Math.floor(Math.random() * pool.length)]
  if (s === lastPick.get(key)) s = pool[(pool.indexOf(s) + 1) % pool.length]
  lastPick.set(key, s)
  return s
}

export function whisper(key: WhisperKey): string {
  return pick(key, effectiveTier())
}

export function useWhisper() {
  const tier = useStore((s) => s.copyTier)
  const night = useStore((s) => s.nightBoost)
  const eff = Math.min(2, Math.max(0, tier + (night && isNight() ? 1 : 0))) as 0 | 1 | 2
  return (key: WhisperKey) => pick(key, eff)
}

/** 时段问候（侧栏）：按当前小时选 key */
export function greetingKey(): WhisperKey {
  const h = new Date().getHours()
  if (h >= 5 && h < 12) return 'greet_morning'
  if (h >= 12 && h < 18) return 'greet_noon'
  return 'greet_night'
}

/* ── v4.1 侧边栏双文案：normal（正常）↔ whisper（情话），切换按钮在侧栏底部 ── */
const NAV_WHISPER: Record<string, string> = {
  /* 分区名 */
  '浏览': '流连',
  '采集': '狩猎',
  '系统': '闺房',
  /* 导航项与页面标题（中文） */
  '首页': '今夜的她',
  '订阅上新': '新夜来客',
  '仪表盘': '心跳记录',
  '影片库': '群芳谱',
  '收藏': '心尖上',
  '演员库': '佳人们',
  '排行榜': '群芳榜',
  '列表源': '猎场',
  '爬取控制台': '潜入暗房',
  '订阅': '挂念的人',
  '下载历史': '收入囊中',
  '下载器': '接她回家',
  '设置': '闺房布置',
  '系统设置': '闺房布置',
  '下载器配置': '接她回家',
  '列表源管理': '猎场',
  /* 页面眉题主词（英文） */
  'Overview': '心跳记录',
  'Library': '群芳谱',
  'Favorites': '心尖上',
  'Actors': '佳人们',
  'Rankings': '群芳榜',
  'Sources': '猎场',
  'Crawl': '潜入暗房',
  'Crawl Console': '潜入暗房',
  'Subscriptions': '挂念的人',
  'New Releases': '新夜来客',
  'Downloads': '收入囊中',
  'Downloaders': '接她回家',
  'Settings': '闺房布置',
  /* detail sections */
  '磁力链接': '她的钥匙',
  '预览图': '今晚最亮的她',
  '简介': '她的底细',
  '描述': '细细读她',
  '备注': '私人笔记',
  '新作发现': '新欢将至',
  '基本资料': '她的档案',
  '编辑资料': '改写她',
  '职业生涯': '她的年代',
  '职业时间线': '她的时间线',
}

export function navLabel(normal: string): string {
  const mode = useStore.getState().navMode
  return mode === 'whisper' ? (NAV_WHISPER[normal] ?? normal) : normal
}

export function useNavMode() {
  const mode = useStore((s) => s.navMode)
  return (normal: string) => (mode === 'whisper' ? (NAV_WHISPER[normal] ?? normal) : normal)
}

/** 眉题映射：先整串匹配，再取首段匹配并保留余下部分（"Library · 4 部" → "群芳谱 · 4 部"） */
export function navEyebrow(eyebrow: string): string {
  if (useStore.getState().navMode !== 'whisper') return eyebrow
  const whole = NAV_WHISPER[eyebrow]
  if (whole) return whole
  const m = eyebrow.match(/^(\S+)(.*)$/)
  if (!m) return eyebrow
  const head = NAV_WHISPER[m[1]]
  return head ? head + m[2] : eyebrow
}
