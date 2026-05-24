<script setup lang="ts">
import { AlertTriangle } from 'lucide-vue-next'

defineProps<{
  show: boolean
  title?: string
  message?: string
  confirmText?: string
  cancelText?: string
  danger?: boolean
}>()

const emit = defineEmits(['confirm', 'cancel'])
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="show" class="fixed inset-0 z-[9998] flex items-center justify-center bg-black/40 backdrop-blur-sm">
        <div class="mx-4 w-full max-w-sm space-y-4 rounded-lg border border-gray-200 bg-white p-5 shadow-2xl">
          <div class="flex items-start gap-3">
            <div class="rounded-lg p-2" :class="danger ? 'bg-red-50' : 'bg-amber-50'">
              <AlertTriangle :size="20" :class="danger ? 'text-red-500' : 'text-amber-500'" />
            </div>
            <div>
              <h3 class="font-semibold text-gray-950">{{ title || '确认操作' }}</h3>
              <p class="mt-1 text-sm leading-6 text-gray-500">{{ message || '确定要执行此操作吗？' }}</p>
            </div>
          </div>
          <div class="flex justify-end gap-2">
            <button @click="emit('cancel')"
              class="rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-bold text-gray-600 transition-all hover:bg-gray-50">
              {{ cancelText || '取消' }}
            </button>
            <button @click="emit('confirm')"
              class="rounded-lg px-4 py-2 text-sm font-bold text-white transition-all"
              :class="danger ? 'bg-red-600 hover:bg-red-700' : 'bg-gray-950 hover:bg-gray-800'">
              {{ confirmText || '确认' }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.fade-enter-active, .fade-leave-active { transition: opacity 0.2s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
