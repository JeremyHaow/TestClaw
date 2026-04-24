import { defineStore } from 'pinia'
import api from '../lib/api'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem('testclaw_token') || '',
    user: null as null | { id: string; username: string; is_active: boolean; is_admin: boolean },
    loading: false,
  }),
  getters: {
    isAuthenticated: (state) => Boolean(state.token),
  },
  actions: {
    async login(username: string, password: string) {
      this.loading = true
      try {
        const { data } = await api.post('/auth/login', { username, password })
        this.token = data.access_token
        localStorage.setItem('testclaw_token', data.access_token)
        await this.fetchMe()
      } finally {
        this.loading = false
      }
    },
    async fetchMe() {
      const { data } = await api.get('/auth/me')
      this.user = data
    },
    logout() {
      this.token = ''
      this.user = null
      localStorage.removeItem('testclaw_token')
    },
  },
})
