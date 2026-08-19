/** 音色库（Eros V3/V4）—— 纯函数合成配方，全部走 AudioEngine 的总线。
 *  三音色家族：
 *  - 生理音 physio：心跳、喘息（v4.1 做爱时的喘息）。低频正弦短包络；双 formant 粉噪人声共振
 *  - 材质音 tex：丝绸/布料。白噪 bandpass + 滑音 sweep，随机 ±15% 防听觉疲劳
 *  - 环境音 amb：烛火（棕噪+随机噼啪）、杯碰（双高频正弦高 Q 共振）
 *  克制原则：默认静音、音量整体极低（存在感极低、撤退感极快），绝不叠响。 */
import { VOICES, audio, type MoanLayer, type VoiceDeps } from './engine'

let D: VoiceDeps

/** 每次触发随机化（±15% 音高 / ±10% 时长），杜绝机械重复 */
const rnd = (v: number, pct = 0.15) => v * (1 + (Math.random() * 2 - 1) * pct)

/** 噪声源（随机 playbackRate 制造变化） */
function noise(t: number, dur: number) {
  const src = D.ctx().createBufferSource()
  src.buffer = D.noise()
  src.loop = true
  src.playbackRate.value = rnd(1, 0.1)
  src.start(t)
  src.stop(t + dur + 0.1)
  return src
}

/** v4.1 喘息声层：双 formant 粉噪（人声共鸣感）+ 呼吸包络循环 + 呼气微颤。
 *  heat 越高：呼吸周期越短（2.8s→0.9s）、音量越深、颤音越明显——从轻浅到急促。 */
export function createMoan(deps: VoiceDeps): MoanLayer {
  const ctx = deps.ctx()
  const out = deps.bus('physio')
  const t = ctx.currentTime

  const src = ctx.createBufferSource()
  src.buffer = deps.noise()
  src.loop = true
  // 双 formant：模拟人声共鸣腔
  const bp1 = ctx.createBiquadFilter()
  bp1.type = 'bandpass'; bp1.frequency.value = 620; bp1.Q.value = 1.1
  const bp2 = ctx.createBiquadFilter()
  bp2.type = 'bandpass'; bp2.frequency.value = 920; bp2.Q.value = 1.6
  // 呼气尾端微颤（4-6Hz 气声抖动）
  const vib = ctx.createOscillator()
  vib.frequency.value = 5
  const vibG = ctx.createGain()
  vibG.gain.value = 8
  vib.connect(vibG).connect(bp2.frequency)
  // 呼吸包络：dc(1) + sine(±1) → 0..2 → ×0.5 → 0..1
  const dc = ctx.createConstantSource()
  dc.offset.value = 1
  const lfo = ctx.createOscillator()
  lfo.frequency.value = 0.36   // 周期 ≈2.8s（heat=0 基准）
  const sum = ctx.createGain()
  sum.gain.value = 0.5
  dc.connect(sum)
  lfo.connect(sum)
  const scale = ctx.createGain()
  scale.gain.value = 0.015
  sum.connect(scale)
  const g = ctx.createGain()
  g.gain.value = 0
  scale.connect(g.gain)

  src.connect(bp1).connect(bp2).connect(g).connect(out)
  src.start(t)
  dc.start(t)
  lfo.start(t)
  vib.start(t)

  return {
    update(heat01: number) {
      const t0 = ctx.currentTime
      // 周期 2.8s → 0.9s（更急促）；音量 0.015 → 0.05（更深）；颤音 8 → 22Hz 振幅
      lfo.frequency.setTargetAtTime(0.36 + heat01 * 0.75, t0, 0.4)
      scale.gain.setTargetAtTime(0.015 + heat01 * 0.035, t0, 0.4)
      vibG.gain.setTargetAtTime(8 + heat01 * 14, t0, 0.4)
    },
    stop() {
      const t0 = ctx.currentTime
      scale.gain.setTargetAtTime(0, t0, 0.35)
      window.setTimeout(() => {
        for (const n of [src, dc, lfo, vib]) { try { n.stop() } catch { /* already stopped */ } }
      }, 900)
    },
  }
}

export function registerVoices(deps: VoiceDeps) {
  D = deps
  // v4.1 喘息层工厂注入（engine.setHeat 驱动启停与急促度）
  audio.setMoanDeps(deps)
  audio.setMoanFactory(createMoan)

  /* ── 心跳：双跳结构（两快一慢的"快"部分），60Hz 贴耳低频 ── */
  VOICES.set('heartbeat', (t, k) => {
    const ctx = D.ctx()
    const out = D.bus('physio')
    const thump = (t0: number, vol: number) => {
      const osc = ctx.createOscillator()
      osc.type = 'sine'
      osc.frequency.setValueAtTime(rnd(62, 0.08), t0)
      const g = ctx.createGain()
      g.gain.setValueAtTime(0, t0)
      g.gain.linearRampToValueAtTime(vol * 0.5 * k, t0 + 0.005)
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.13)
      osc.connect(g).connect(out)
      osc.start(t0)
      osc.stop(t0 + 0.16)
    }
    thump(t, 0.9)
    thump(t + 0.18, 0.45)  // 第二击更轻
  })

  /* ── 丝绸 hover：白噪 bandpass 高→低滑音，一声"沙——" ── */
  VOICES.set('silk', (t, k) => {
    const ctx = D.ctx()
    const out = D.bus('tex')
    const dur = rnd(0.35, 0.1)
    const src = noise(t, dur)
    const bp = ctx.createBiquadFilter()
    bp.type = 'bandpass'
    bp.Q.value = rnd(0.8, 0.2)
    bp.frequency.setValueAtTime(rnd(3500, 0.12), t)
    bp.frequency.exponentialRampToValueAtTime(rnd(1200, 0.12), t + dur)
    const g = ctx.createGain()
    g.gain.setValueAtTime(0, t)
    g.gain.linearRampToValueAtTime(0.06 * k, t + dur * 0.4)
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur)
    src.connect(bp).connect(g).connect(out)
  })

  /* ── 掀纱/解雾：双向滑音 + 200Hz 低托底，结尾极轻 ── */
  VOICES.set('veil', (t, k) => {
    const ctx = D.ctx()
    const out = D.bus('tex')
    const dur = rnd(0.6, 0.1)
    const src = noise(t, dur)
    const bp = ctx.createBiquadFilter()
    bp.type = 'bandpass'
    bp.Q.value = 0.9
    bp.frequency.setValueAtTime(rnd(1000, 0.1), t)
    bp.frequency.exponentialRampToValueAtTime(rnd(4000, 0.1), t + dur * 0.5)
    bp.frequency.exponentialRampToValueAtTime(rnd(800, 0.1), t + dur)
    const g = ctx.createGain()
    g.gain.setValueAtTime(0, t)
    g.gain.linearRampToValueAtTime(0.05 * k, t + dur * 0.3)
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur)
    src.connect(bp).connect(g).connect(out)
    // 低频托底
    const osc = ctx.createOscillator()
    osc.type = 'sine'
    osc.frequency.value = rnd(200, 0.08)
    const g2 = ctx.createGain()
    g2.gain.setValueAtTime(0, t)
    g2.gain.linearRampToValueAtTime(0.025 * k, t + dur * 0.4)
    g2.gain.exponentialRampToValueAtTime(0.0001, t + dur)
    osc.connect(g2).connect(out)
    osc.start(t)
    osc.stop(t + dur + 0.1)
  })

  /* ── 轻叹（收藏）：粉噪带通缓起缓落 + 尾部气声下滑，若有似无 ── */
  VOICES.set('sigh', (t, k) => {
    const ctx = D.ctx()
    const out = D.bus('physio')
    const dur = 1.3
    const src = noise(t, dur)
    const bp = ctx.createBiquadFilter()
    bp.type = 'bandpass'
    bp.frequency.value = rnd(600, 0.1)
    bp.Q.value = 1
    const g = ctx.createGain()
    g.gain.setValueAtTime(0, t)
    g.gain.linearRampToValueAtTime(0.05 * k, t + 0.4)   // 缓起（吸入）
    g.gain.linearRampToValueAtTime(0.03 * k, t + 0.9)
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur) // 缓落（呼出）
    src.connect(bp).connect(g).connect(out)
    // 尾部 280Hz 气声下滑
    const osc = ctx.createOscillator()
    osc.type = 'sine'
    osc.frequency.setValueAtTime(rnd(300, 0.08), t + 0.85)
    osc.frequency.exponentialRampToValueAtTime(220, t + dur)
    const g2 = ctx.createGain()
    g2.gain.setValueAtTime(0, t + 0.85)
    g2.gain.linearRampToValueAtTime(0.008 * k, t + 1.0)
    g2.gain.exponentialRampToValueAtTime(0.0001, t + dur)
    osc.connect(g2).connect(out)
    osc.start(t + 0.85)
    osc.stop(t + dur + 0.05)
  })

  /* ── 烛火（密室环境循环）：棕噪低平常驻 + 随机噼啪脉冲簇；返回 stop ── */
  VOICES.set('candle', (t, k) => {
    const ctx = D.ctx()
    const out = D.bus('amb')
    // 棕噪 = 白噪过低通（-6dB/oct 近似）
    const src = ctx.createBufferSource()
    src.buffer = D.noise()
    src.loop = true
    const lp = ctx.createBiquadFilter()
    lp.type = 'lowpass'
    lp.frequency.value = 600
    const g = ctx.createGain()
    g.gain.setValueAtTime(0, t)
    g.gain.linearRampToValueAtTime(0.02 * k, t + 2)  // 2s 淡入
    src.connect(lp).connect(g).connect(out)
    src.start(t)
    // 随机噼啪
    let alive = true
    let timer = 0
    const crackle = () => {
      if (!alive) return
      const t0 = ctx.currentTime
      const n = 2 + Math.floor(Math.random() * 3)
      for (let i = 0; i < n; i++) {
        const t1 = t0 + i * 0.004
        const cs = ctx.createBufferSource()
        cs.buffer = D.noise()
        const hp = ctx.createBiquadFilter()
        hp.type = 'highpass'
        hp.frequency.value = 2000
        const cg = ctx.createGain()
        cg.gain.setValueAtTime(0.04 * k * Math.random(), t1)
        cg.gain.exponentialRampToValueAtTime(0.0001, t1 + 0.003)
        cs.connect(hp).connect(cg).connect(out)
        cs.start(t1)
        cs.stop(t1 + 0.01)
      }
      timer = window.setTimeout(crackle, 300 + Math.random() * 1700)
    }
    crackle()
    return () => {
      alive = false
      clearTimeout(timer)
      g.gain.setTargetAtTime(0, ctx.currentTime, 0.5)
      window.setTimeout(() => { try { src.stop() } catch { /* already stopped */ } }, 1200)
    }
  })

  /* ── 杯碰（盲盒揭晓）：双高频正弦高 Q 共振，一次即止 ── */
  VOICES.set('chime', (t, k) => {
    const ctx = D.ctx()
    const out = D.bus('amb')
    const ping = (freq: number, t0: number, vol: number) => {
      const osc = ctx.createOscillator()
      osc.type = 'sine'
      osc.frequency.value = rnd(freq, 0.02)
      const bp = ctx.createBiquadFilter()
      bp.type = 'bandpass'
      bp.frequency.value = freq
      bp.Q.value = 12
      const g = ctx.createGain()
      g.gain.setValueAtTime(vol * k, t0)
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + 1.8)
      osc.connect(bp).connect(g).connect(out)
      osc.start(t0)
      osc.stop(t0 + 1.9)
    }
    ping(2093, t, 0.06)
    ping(3136, t + 0.09, 0.045)
  })

  /* ── 指尖敲击（搜索输入）：极轻的短叩 ── */
  VOICES.set('tap', (t, k) => {
    const ctx = D.ctx()
    const out = D.bus('tex')
    const src = noise(t, 0.03)
    const bp = ctx.createBiquadFilter()
    bp.type = 'bandpass'
    bp.frequency.value = rnd(900, 0.2)
    bp.Q.value = 2
    const g = ctx.createGain()
    g.gain.setValueAtTime(0.03 * k, t)
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.04)
    src.connect(bp).connect(g).connect(out)
  })
}
