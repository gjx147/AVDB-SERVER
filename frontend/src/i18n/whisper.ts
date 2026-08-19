/** 耳语文案系统 —— 全站文案双档：tame 克制（日常）/ bold 大胆（密室模式）。
 *  whisper(key)：任意位置可调（含事件回调），读 store 当前档位。
 *  useWhisper()：组件内订阅档位变化（密室开关后文案自动换装）。 */
import { useStore } from '../store/useStore'

const COPY = {
  loading:          { tame: '加载中…', bold: '她在更衣，稍等…' },
  loading_wall:     { tame: '正在挂今晚的影视墙…', bold: '今夜的她正在登场…' },
  empty_lib_title:  { tame: '还空着呢', bold: '这里还空着' },
  empty_lib_sub:    { tame: '去把她们带回来——先扫描列表源，或按番号创建任务。', bold: '等你带她回家——先扫描列表源，或按番号创建任务。' },
  empty_wall_title: { tame: '墙还空着', bold: '今晚还没有人赴约' },
  empty_wall_sub:   { tame: '去把她们带回来——先扫描列表源，或按番号创建任务。', bold: '别让今晚空着——去把她们带回来。' },
  fav_add:          { tame: '心动了，收进私藏', bold: '♥ 心动了，她已经是你的人' },
  fav_remove:       { tame: '已取消收藏', bold: '收回了这份心动' },
  btn_fav:          { tame: '收藏', bold: '心动了' },
  btn_unfav:        { tame: '取消收藏', bold: '收回心动' },
  btn_bring:        { tame: '把她带回家', bold: '带她回家，今晚' },
  btn_see:          { tame: '看得更清楚一点', bold: '再看清一点，别急' },
  btn_seeing:       { tame: '正在看清…', bold: '正在看清…' },
  take:             { tame: '占为己有', bold: '现在就要' },
  taken:            { tame: '已占为己有', bold: '已经是你的了' },
  similar_title:    { tame: '同样让你心动', bold: '和她们也有纠缠' },
} as const

export type WhisperKey = keyof typeof COPY

export function whisper(key: WhisperKey): string {
  const c = COPY[key]
  return useStore.getState().moodMode ? c.bold : c.tame
}

export function useWhisper() {
  const mood = useStore((s) => s.moodMode)
  return (key: WhisperKey) => (mood ? COPY[key].bold : COPY[key].tame)
}
