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
        <div class="bg-white rounded-2xl shadow-2xl p-6 max-w-sm w-full mx-4 space-y-4">
          <div class="flex items-start gap-3">
            <div class="p-2 rounded-xl" :class="danger ? 'bg-red-50' : 'bg-amber-50'">
              <AlertTriangle :size="20" :class="danger ? 'text-red-500' : 'text-amber-500'" />
            </div>
            <div>
              <h3 class="font-bold text-gray-900">{{ title || '确认操作' }}</h3>
              <p class="text-sm text-gray-500 mt-1">{{ message || '确定要执行此操作吗？' }}</p>
            </div>
          </div>
          <div class="flex justify-end gap-2">
            <button @click="emit('cancel')"
              class="px-4 py-2 text-sm font-bold text-gray-600 bg-gray-100 hover:bg-gray-200 rounded-lg transition-all">
              {{ cancelText || '取消' }}
            </button>
            <button @click="emit('confirm')"
              class="px-4 py-2 text-sm font-bold text-white rounded-lg transition-all"
              :class="danger ? 'bg-red-600 hover:bg-red-700' : 'bg-blue-600 hover:bg-blue-700'">
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
