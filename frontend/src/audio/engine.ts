/** 声色引擎（Eros V3）—— WebAudio 程序化合成，零音频文件零外网。
 *  声音身份：低语、贴耳、烛光——用户不该"听到音效"，只该觉得心跳快了一点。
 *  隐私：默认静音（localStorage sound==='1' 才开）；AudioContext 惰性创建
 *  （首次用户手势 unlock，规避自动播放策略）；document.hidden 即哑。 */
import { registerVoices } from './voices'

type Bus = 'physio' | 'tex' | 'amb'

class AudioEngine {
  private ctx: AudioContext | null = null
  private master: GainNode | null = null
  private buses: Partial<Record<Bus, GainNode>> = {}
  private noiseBuf: AudioBuffer | null = null
  private unlocked = false

  enabled = localStorage.getItem('sound') === '1'

  /** 总线音量（设置页可调，0~1） */
  busVolume: Record<Bus, number> = {
    physio: parseFloat(localStorage.getItem('sound.physio') || '0.8'),
    tex: parseFloat(localStorage.getItem('sound.tex') || '0.7'),
    amb: parseFloat(localStorage.getItem('sound.amb') || '0.6'),
  }

  /** 供心跳循环读取的当前体温（0~1），由外部 wiring 推送 */
  heat = 0
  private hbTimer = 0
  private ambNodes: { stop: () => void } | null = null

  /** 首次用户手势时调用（一次性 pointerdown） */
  unlock() {
    if (this.unlocked || typeof AudioContext === 'undefined') return
    this.unlocked = true
    this.ctx = new AudioContext()
    const comp = this.ctx.createDynamicsCompressor()
    comp.connect(this.ctx.destination)
    this.master = this.ctx.createGain()
    this.master.gain.value = 0.8
    this.master.connect(comp)
    for (const b of ['physio', 'tex', 'amb'] as Bus[]) {
      const g = this.ctx.createGain()
      g.gain.value = this.busVolume[b]
      g.connect(this.master)
      this.buses[b] = g
    }
    // 1s 白噪底料（各材质音色共享，随机 playbackRate 变化）
    const len = this.ctx.sampleRate
    this.noiseBuf = this.ctx.createBuffer(1, len, this.ctx.sampleRate)
    const data = this.noiseBuf.getChannelData(0)
    for (let i = 0; i < len; i++) data[i] = Math.random() * 2 - 1
    registerVoices({
      ctx: () => this.ctx!,
      noise: () => this.noiseBuf!,
      bus: (b) => this.buses[b]!,
      vol: (b) => this.busVolume[b],
    })
    this.ctx.resume().catch(() => {})
  }

  get ready() { return !!this.ctx && this.enabled && !document.hidden }

  play(name: string, intensity = 0.5) {
    if (!this.ready) return
    if (this.ctx!.state === 'suspended') this.ctx!.resume().catch(() => {})
    const v = VOICES.get(name)
    if (v) v(this.ctx!.currentTime, intensity)
  }

  setEnabled(on: boolean) {
    this.enabled = on
    localStorage.setItem('sound', on ? '1' : '0')
    if (!on) { this.stopHeartbeat(); this.stopMoan(); this.stopAmbient() }
  }

  setBusVolume(b: Bus, v: number) {
    this.busVolume[b] = v
    localStorage.setItem(`sound.${b}`, String(v))
    const g = this.buses[b]
    if (g && this.ctx) g.gain.setTargetAtTime(v, this.ctx.currentTime, 0.1)
  }

  setHeat(h01: number) {
    this.heat = h01
    if (h01 > 0.3) this.startHeartbeat()
    else this.stopHeartbeat()
    // 喘息层：heat>25% 启动，越热越急促越深（v4.1）
    if (h01 > 0.25) { this.startMoan(); this.moan?.update(h01) }
    else this.stopMoan()
  }

  /* ── 心跳持续层：BPM = 52 + heat×60，音量∝heat；页面隐藏/静音即停 ── */
  startHeartbeat() {
    if (this.hbTimer || !this.ready) return
    const beat = () => {
      if (!this.ready) { this.stopHeartbeat(); return }
      this.play('heartbeat', 0.25 + this.heat * 0.75)
      const bpm = 52 + this.heat * 60
      this.hbTimer = window.setTimeout(beat, 60000 / bpm)
    }
    beat()
  }
  stopHeartbeat() { clearTimeout(this.hbTimer); this.hbTimer = 0 }

  /* ── 喘息层（v4.1）：做爱时的喘息声，工厂由 voices.ts 注入；heat 联动 ── */
  private moanFactory: ((deps: VoiceDeps) => MoanLayer) | null = null
  private moan: MoanLayer | null = null
  setMoanFactory(fn: NonNullable<typeof this.moanFactory>) { this.moanFactory = fn }
  private moanDeps: VoiceDeps | null = null
  setMoanDeps(d: VoiceDeps) { this.moanDeps = d }
  startMoan() {
    if (this.moan || !this.ready || !this.moanFactory || !this.moanDeps) return
    this.moan = this.moanFactory(this.moanDeps)
    this.moan.update(this.heat)
  }
  stopMoan() {
    this.moan?.stop()
    this.moan = null
  }

  /* ── 环境层（烛火）：moodMode 开关驱动，单实例 ── */
  startAmbient(name = 'candle') {
    if (this.ambNodes || !this.ready) return
    const v = VOICES.get(name)
    if (!v) return
    const stop = v(this.ctx!.currentTime, 1)
    this.ambNodes = { stop: () => stop?.() }
  }
  stopAmbient() {
    this.ambNodes?.stop()
    this.ambNodes = null
  }
}

/* 音色注册表：由 voices.ts 填充；(t, intensity) => stop? */
export type Voice = (t: number, intensity: number) => (() => void) | void
export const VOICES = new Map<string, Voice>()

/* 喘息层控制：循环音色（v4.1） */
export interface MoanLayer {
  update: (heat01: number) => void
  stop: () => void
}

export interface VoiceDeps {
  ctx: () => AudioContext
  noise: () => AudioBuffer
  bus: (b: 'physio' | 'tex' | 'amb') => GainNode
  vol: (b: 'physio' | 'tex' | 'amb') => number
}

export const audio = new AudioEngine()
