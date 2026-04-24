import { defineStore } from 'pinia'
import api from '../lib/api'

export const useTaskStore = defineStore('tasks', {
  state: () => ({
    items: [] as any[],
    current: null as any,
    loading: false,
  }),
  actions: {
    async fetchTasks() {
      this.loading = true
      try {
        const { data } = await api.get('/tasks')
        this.items = data
      } finally {
        this.loading = false
      }
    },
    async fetchTask(id: string) {
      const { data } = await api.get(`/tasks/${id}`)
      this.current = data
      return data
    },
    async createTask(payload: { objective: string; target_url: string; test_type: string; api_doc_id?: string | null; environment_id?: string | null }) {
      const { data } = await api.post('/tasks', payload)
      this.items.unshift(data)
      return data
    },
  },
})
