/** 共用真随机工具（盲盒 / 影视墙轮播 / 预览墙均可复用） */

/** Fisher-Yates 洗牌：返回新数组，不改动原数组 */
export function shuffle<T>(arr: T[]): T[] {
  const a = [...arr]
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[a[i], a[j]] = [a[j], a[i]]
  }
  return a
}

/** 真随机取一个 */
export function pickOne<T>(arr: T[]): T | undefined {
  if (arr.length === 0) return undefined
  return arr[Math.floor(Math.random() * arr.length)]
}
