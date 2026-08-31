import { useEffect, useState } from 'react'
import type { Task } from '../api/types'
import { coverFileUrl, thumbFileUrl, withImageAuth } from '../api/client'

/** 手机断点（<768px，与全站断点体系一致） */
export function useIsMobile(): boolean {
  const [m, setM] = useState(() => window.matchMedia('(max-width: 768px)').matches)
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 768px)')
    const on = () => setM(mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])
  return m
}

/** 触屏设备（pointer: coarse） */
export function useCoarsePointer(): boolean {
  const [c, setC] = useState(() => window.matchMedia('(pointer: coarse)').matches)
  useEffect(() => {
    const mq = window.matchMedia('(pointer: coarse)')
    const on = () => setC(mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])
  return c
}

export interface TaskCoverSources { src: string; vertical: string | null; horizontal: string | null }

/** 任务图源按设备切换：手机=竖版海报（thumb 0，失败回退远程竖图），桌面=横版封面 */
export function taskCoverSources(
  task: Task | null | undefined, isMobile: boolean, imgVersion: string | number = '0',
): TaskCoverSources {
  if (!task) return { src: '', vertical: null, horizontal: null }
  const vertical = (() => { try { return JSON.parse(task.thumbnail_urls || '[]')[0] as string } catch { return null } })()
  const horizontal = task.poster_url || vertical
  if (isMobile) {
    return { src: withImageAuth(`${thumbFileUrl(task.id, 0)}?v=${imgVersion}`), vertical, horizontal }
  }
  return { src: withImageAuth(`${coverFileUrl(task.id)}?v=${imgVersion}`), vertical, horizontal }
}
