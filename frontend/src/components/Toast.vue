<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { CheckCircle2, XCircle, AlertTriangle, Info, X } from 'lucide-vue-next'

interface Toast {
  id: number
  type: 'success' | 'error' | 'warning' | 'info'
  message: string
}

const toasts = ref<Toast[]>([])
let nextId = 0

function addToast(type: Toast['type'], message: string, duration = 3000) {
  const id = nextId++
  toasts.value.push({ id, type, message })
  setTimeout(() => removeToast(id), duration)
}

function removeToast(id: number) {
  toasts.value = toasts.value.filter(t => t.id !== id)
}

const iconMap = { success: CheckCircle2, error: XCircle, warning: AlertTriangle, info: Info }
const colorMap = {
  success: 'bg-emerald-50 border-emerald-200 text-emerald-800',
  error: 'bg-red-50 border-red-200 text-red-800',
  warning: 'bg-amber-50 border-amber-200 text-amber-800',
  info: 'bg-blue-50 border-blue-200 text-blue-800',
}

// Expose globally
window.__toast = { success: (m: string) => addToast('success', m), error: (m: string) => addToast('error', m, 5000), warning: (m: string) => addToast('warning', m), info: (m: string) => addToast('info', m) }
</script>

<template>
  <div class="fixed top-4 right-4 z-[9999] flex flex-col gap-2 pointer-events-none">
    <TransitionGroup name="toast">
      <div v-for="toast in toasts" :key="toast.id"
        class="pointer-events-auto flex min-w-[300px] max-w-[420px] items-center gap-3 rounded-lg border px-4 py-3 shadow-lg backdrop-blur-sm"
        :class="colorMap[toast.type]">
        <component :is="iconMap[toast.type]" :size="18" class="shrink-0" />
        <span class="text-sm font-medium flex-1">{{ toast.message }}</span>
        <button type="button" aria-label="关闭提示" @click="removeToast(toast.id)" class="shrink-0 rounded p-1 opacity-60 transition-opacity hover:bg-white/50 hover:opacity-100">
          <X :size="14" />
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toast-enter-active { transition: all 0.3s ease-out; }
.toast-leave-active { transition: all 0.2s ease-in; }
.toast-enter-from { opacity: 0; transform: translateX(40px); }
.toast-leave-to { opacity: 0; transform: translateX(40px); }
</style>
