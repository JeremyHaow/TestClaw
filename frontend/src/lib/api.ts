import axios, { AxiosHeaders } from 'axios'

export const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || '/api/v1'

export function apiUrl(path: string, params?: Record<string, string | number | boolean | null | undefined>) {
  const base = apiBaseUrl.replace(/\/+$/, '')
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  const url = `${base}${normalizedPath}`
  const query = new URLSearchParams()

  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      query.set(key, String(value))
    }
  })

  const queryString = query.toString()
  return queryString ? `${url}${url.includes('?') ? '&' : '?'}${queryString}` : url
}

const api = axios.create({
  baseURL: apiBaseUrl,
})

api.interceptors.request.use((config) => {
  const headers = AxiosHeaders.from(config.headers)
  headers.set('Cache-Control', 'no-cache')
  headers.set('Pragma', 'no-cache')
  headers.set('Expires', '0')
  config.headers = headers
  if ((config.method || 'get').toLowerCase() === 'get') {
    if (config.params instanceof URLSearchParams) {
      config.params.set('_tc', String(Date.now()))
    } else {
      config.params = { ...(config.params || {}), _tc: Date.now() }
    }
  }
  const token = localStorage.getItem('testclaw_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('testclaw_token')
      if (window.location.pathname !== '/login') {
        window.location.href = '/login'
      }
    }
    return Promise.reject(error)
  },
)

export default api
