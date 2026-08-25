// AVDB Service Worker：app shell 缓存（版本化，API 请求不缓存）
const CACHE = 'avdb-v1'

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(['/'])).then(() => self.skipWaiting())
  )
})

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  )
})

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url)
  if (e.request.method !== 'GET') return
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/ws/')) return
  // 导航请求：网络优先，离线回退缓存的首页
  if (e.request.mode === 'navigate') {
    e.respondWith(
      fetch(e.request)
        .then((r) => {
          const cp = r.clone()
          caches.open(CACHE).then((c) => c.put('/', cp))
          return r
        })
        .catch(() => caches.match('/'))
    )
    return
  }
  // 静态资源：缓存优先，回源后写入缓存
  e.respondWith(
    caches.match(e.request).then((r) => r || fetch(e.request).then((res) => {
      if (res.ok) {
        const cp = res.clone()
        caches.open(CACHE).then((c) => c.put(e.request, cp))
      }
      return res
    }))
  )
})
