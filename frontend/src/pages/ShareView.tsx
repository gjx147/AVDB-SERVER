import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../api/client'
import { Loading } from '../components/States'

export function ShareView() {
  const { token = '' } = useParams()
  const [d, setD] = useState<Awaited<ReturnType<typeof api.publicShare>> | null>(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    let alive = true
    api.publicShare(token).then((r) => { if (alive) setD(r) })
      .catch((e) => { if (alive) { setErr(String((e as Error).message)); setD(null) } })
    return () => { alive = false }
  }, [token])
  return (
    <div className="page" style={{ maxWidth: 720, margin: '0 auto' }}>
      <div className="card" style={{ marginTop: 40, padding: '20px 24px' }}>
        <div style={{ fontSize: 20, fontWeight: 700, marginBottom: 4 }}>{d?.title || 'AVDB 分享'}</div>
        <div style={{ fontSize: 12, color: 'var(--t-mute)', marginBottom: 14 }}>
          由 AVDB 影片库用户分享{d ? ` · ${d.items.length} 项` : ''}
        </div>
        {err ? (
          <div style={{ fontSize: 13, color: 'var(--red, #dc2626)', padding: '20px 0', textAlign: 'center' }}>
            {err === 'Request failed with status code 410' ? '分享已过期' : '分享不存在或已失效'}
          </div>
        ) : !d ? <Loading /> : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {d.items.map((t, i) => (
              <div key={`${t.video_code}-${i}`} style={{ display: 'flex', gap: 10, alignItems: 'center', border: '1px solid var(--line, #eee)', borderRadius: 8, padding: 8, fontSize: 12 }}>
                {t.poster_url
                  ? <img src={t.poster_url} alt="" style={{ width: 28, height: 40, objectFit: 'cover', borderRadius: 4 }} loading="lazy" referrerPolicy="no-referrer"
                      onError={(e) => { e.currentTarget.style.visibility = 'hidden' }} />
                  : <div style={{ width: 28, height: 40, background: 'var(--bg-raised, #f3f4f6)', borderRadius: 4 }} />}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div>{t.video_code}{t.rating ? <span style={{ color: 'var(--gold, #d97706)' }}> {t.rating}</span> : null}</div>
                  <div style={{ color: 'var(--t-mute)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.title || ''}</div>
                </div>
              </div>
            ))}
            {d.items.length === 0 && <div style={{ fontSize: 12, color: 'var(--t-mute)', textAlign: 'center', padding: 16 }}>空</div>}
          </div>
        )}
      </div>
    </div>
  )
}
