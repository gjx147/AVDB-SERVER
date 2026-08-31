/**
 * JS masonry 布局核心 —— 纯函数，无 DOM 依赖，可单测。
 * 算法：逐卡放入当前总高度最小的列（最短列优先），
 * 返回每卡的绝对定位（x/y）与容器总高。
 */

export interface MasonryEntry {
  id: number
  ratio: number // 图片 高/宽；未知时用默认值
}

export interface MasonryPlacement {
  id: number
  col: number
  x: number
  y: number
}

export interface MasonryLayout {
  placements: MasonryPlacement[]
  colWidth: number
  height: number // 容器需要的高度（最高列）
}

/** 卡片信息行（标题+副行+内边距）的估算高度，随样式调整同步改 */
export const CARD_INFO_H = 62

/** 图片未加载前的默认 高/宽 估算（竖版海报） */
export const DEFAULT_RATIO = 1.4

/** 单卡高度估算：列宽 × ratio + 信息行 */
export function cardHeight(colWidth: number, ratio: number, infoH = CARD_INFO_H): number {
  return Math.round(colWidth * (ratio > 0 ? ratio : DEFAULT_RATIO)) + infoH
}

/** 按容器宽度算列数（最小列宽 minCol，上限 max） */
export function colCountOf(width: number, minCol = 240, max = 5): number {
  if (width <= 0) return 1
  return Math.max(1, Math.min(max, Math.floor(width / minCol)))
}

/**
 * 最短列放置：逐卡找当前高度最小的列放入。
 * - 图片 onload 后更新 ratio 再调一次即可校正布局（增量重排）
 * - 固定 ratio（如海报墙裁剪模式）时高度可预计算，天然无高度误差
 */
export function computeShortestColumnLayout(
  entries: MasonryEntry[],
  containerWidth: number,
  colCount: number,
  gap: number,
  infoH = CARD_INFO_H,
): MasonryLayout {
  const n = Math.max(1, Math.min(Math.floor(colCount) || 1, 12))
  const colWidth = containerWidth > 0
    ? Math.floor((containerWidth - gap * (n - 1)) / n)
    : 0
  const placements: MasonryPlacement[] = []
  if (containerWidth <= 0 || colWidth <= 0 || entries.length === 0) {
    return { placements, colWidth: Math.max(0, colWidth), height: 0 }
  }
  const heights = new Array<number>(n).fill(0)
  for (const e of entries) {
    let col = 0
    for (let c = 1; c < n; c++) {
      if (heights[c] < heights[col]) col = c
    }
    const h = cardHeight(colWidth, e.ratio, infoH)
    placements.push({ id: e.id, col, x: col * (colWidth + gap), y: heights[col] })
    heights[col] += h + gap
  }
  const height = Math.max(0, Math.max(...heights) - gap)
  return { placements, colWidth, height }
}
