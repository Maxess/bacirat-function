// لایه‌ی نازک روی fetch — همیشه credentials: same-origin
async function request(path, { method = 'GET', body, form, isForm = false } = {}) {
  const opts = { method, credentials: 'same-origin', headers: {} }
  if (form) { opts.body = form }
  else if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body) }
  const r = await fetch(path, opts)
  let data = null
  const text = await r.text()
  try { data = text ? JSON.parse(text) : null } catch { data = { raw: text } }
  if (!r.ok) {
    const err = new Error((data && (data.detail || data.error)) || `HTTP ${r.status}`)
    err.status = r.status
    err.data = data
    throw err
  }
  return data
}

export const api = {
  get: (p) => request(p),
  post: (p, body) => request(p, { method: 'POST', body }),
  put: (p, body) => request(p, { method: 'PUT', body }),
  postForm: (p, form) => request(p, { method: 'POST', form }),
  putForm: (p, form) => request(p, { method: 'PUT', form }),
  del: (p) => request(p, { method: 'DELETE' }),
}
