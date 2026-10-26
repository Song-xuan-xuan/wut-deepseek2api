/**
 * 统一 API 客户端。
 *
 * - 所有请求都带本地 key（后端本机访问也会校验 OpenAI 接口）
 * - 统一错误归一化：{ ok, data, error }
 */

const KEY_STORAGE = 'whut-2api-key'

export function getApiKey() {
  return localStorage.getItem(KEY_STORAGE) || 'sk-whut-local'
}

export function setApiKey(key) {
  localStorage.setItem(KEY_STORAGE, key)
}

async function request(path, { method = 'GET', body, raw = false } = {}) {
  const headers = { Authorization: `Bearer ${getApiKey()}` }
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  let resp
  try {
    resp = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (error) {
    return { ok: false, status: 0, error: `连不上本地服务：${error.message}` }
  }

  if (raw) {
    return { ok: resp.ok, status: resp.status, data: await resp.text() }
  }

  const text = await resp.text()
  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = { error: { message: text.slice(0, 200) } }
  }
  if (!resp.ok) {
    const message = data?.error?.message || data?.detail || `HTTP ${resp.status}`
    return { ok: false, status: resp.status, error: message, data }
  }
  return { ok: true, status: resp.status, data }
}

export const api = {
  // 系统
  info: () => request('/api/system/info'),
  health: () => request('/health'),

  // 认证
  authStatus: () => request('/api/auth/status'),
  login: (email) => request('/api/auth/login', { method: 'POST', body: { email } }),
  saveEmail: (email) => request('/api/auth/email', { method: 'POST', body: { email } }),
  saveToken: (token) => request('/api/auth/token', { method: 'POST', body: { token } }),
  refresh: () => request('/api/auth/refresh', { method: 'POST' }),
  authEvents: (limit = 30) => request(`/api/auth/events?limit=${limit}`),
  clearAuthEvents: () => request('/api/auth/events/clear', { method: 'POST' }),

  // 分发
  accessOverview: () => request('/api/access/overview'),
  createKey: (body) => request('/api/access/keys', { method: 'POST', body }),
  updateKey: (id, body) => request(`/api/access/keys/${id}`, { method: 'PATCH', body }),
  rotateKey: (id) => request(`/api/access/keys/${id}/rotate`, { method: 'POST' }),
  deleteKey: (id) => request(`/api/access/keys/${id}`, { method: 'DELETE' }),
  clientConfig: (kind, key = '') =>
    request(`/api/access/client-config?kind=${encodeURIComponent(kind)}&key=${encodeURIComponent(key)}`),

  // 用量
  usage: (range = '7d', model = '') =>
    request(`/api/usage/overview?range=${encodeURIComponent(range)}&model=${encodeURIComponent(model)}`),
  recent: (limit = 50, status = '', model = '') =>
    request(
      `/api/usage/recent?limit=${limit}&status=${encodeURIComponent(status)}&model=${encodeURIComponent(model)}`,
    ),
  exportCsv: (range = 'all') => request(`/api/usage/export.csv?range=${range}`, { raw: true }),
  clearUsage: () => request('/api/usage', { method: 'DELETE' }),
}

/** 下载文本为文件 */
export function downloadUrl(path, filename) {
  const url = `${path}${path.includes('?') ? '&' : '?'}k=${encodeURIComponent(getApiKey())}`
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
}

export function download(filename, text) {
  const blob = new Blob([text], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}
