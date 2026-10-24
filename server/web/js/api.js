// The only module that talks to the server. Every failure becomes an ApiError whose
// message comes from {"error": {"code", "message"}} when the server sent one.

export class ApiError extends Error {
  constructor(message, { status = 0, code = 'error', body = null } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.body = body
  }
}

let unauthorized = () => {}
export function onUnauthorized(fn) { unauthorized = fn }

function resolve(path, query) {
  const rel = path.startsWith('/health') ? path : `/api/v1${path}`
  const url = new URL(rel.replace(/^\//, ''), document.baseURI)
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v))
    }
  }
  return url
}

function messageFrom(body, res) {
  const e = body && body.error
  if (e && typeof e === 'object' && e.message) return e.message
  if (typeof e === 'string') return e
  if (body && typeof body.detail === 'string') return body.detail
  if (body && Array.isArray(body.detail)) return body.detail.map((d) => d.msg || String(d)).join('; ')
  return `${res.status} ${res.statusText}`.trim() || 'Request failed'
}

async function toError(res) {
  let body = null
  try { const t = await res.text(); body = t ? JSON.parse(t) : null } catch { /* not JSON */ }
  const code = (body && body.error && body.error.code) || String(res.status)
  return new ApiError(messageFrom(body, res), { status: res.status, code, body })
}

export async function request(method, path, { body, query, signal, as = 'json', quiet401 = false } = {}) {
  const init = { method, credentials: 'same-origin', signal, headers: { Accept: as === 'json' ? 'application/json' : '*/*' } }
  if (body instanceof FormData) init.body = body
  else if (body !== undefined) { init.body = JSON.stringify(body); init.headers['Content-Type'] = 'application/json' }
  let res
  try {
    res = await fetch(resolve(path, query), init)
  } catch (err) {
    if (err && err.name === 'AbortError') throw err
    throw new ApiError('Cannot reach the Quill server. Check your connection and try again.', { code: 'network' })
  }
  if (!res.ok) {
    const err = await toError(res)
    if (res.status === 401 && !quiet401) unauthorized(err)
    throw err
  }
  if (res.status === 204) return null
  if (as === 'blob') return res.blob()
  if (as === 'text') return res.text()
  const text = await res.text()
  return text ? JSON.parse(text) : null
}

export const api = {
  get: (path, opts) => request('GET', path, opts),
  post: (path, body, opts) => request('POST', path, { ...opts, body }),
  patch: (path, body, opts) => request('PATCH', path, { ...opts, body }),
  put: (path, body, opts) => request('PUT', path, { ...opts, body }),
  del: (path, opts) => request('DELETE', path, opts),
}

// multipart upload with progress (fetch cannot report upload progress)
export function upload(path, formData, onProgress) {
  return new Promise((resolveP, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', resolve(path).href)
    xhr.withCredentials = true
    xhr.setRequestHeader('Accept', 'application/json')
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total) }
    xhr.onerror = () => reject(new ApiError('Cannot reach the Quill server. Check your connection and try again.', { code: 'network' }))
    xhr.onload = () => {
      let body = null
      try { body = xhr.responseText ? JSON.parse(xhr.responseText) : null } catch { /* not JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) return resolveP(body)
      const err = new ApiError(messageFrom(body, { status: xhr.status, statusText: xhr.statusText }), {
        status: xhr.status, code: (body && body.error && body.error.code) || String(xhr.status), body,
      })
      if (xhr.status === 401) unauthorized(err)
      reject(err)
    }
    xhr.send(formData)
  })
}

export const audioUrl = (meetingId, trackId) => resolve(`/meetings/${encodeURIComponent(meetingId)}/audio/${encodeURIComponent(trackId)}`).href
export const eventsUrl = (meetingId) => resolve(`/meetings/${encodeURIComponent(meetingId)}/events`).href
export const liveUrl = (meetingId) => resolve(`/meetings/${encodeURIComponent(meetingId)}/live`).href.replace(/^http/, 'ws')

// Lists come back as an array or wrapped in an object; accept the likely wrappers.
export function listOf(res, ...keys) {
  if (Array.isArray(res)) return res
  if (!res || typeof res !== 'object') return []
  for (const k of [...keys, 'items', 'data', 'results']) if (Array.isArray(res[k])) return res[k]
  return []
}
