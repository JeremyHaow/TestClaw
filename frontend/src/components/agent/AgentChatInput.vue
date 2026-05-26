<script setup lang="ts">
import { nextTick, ref } from 'vue'
import {
  AlertTriangle,
  Loader2,
  Pencil,
  Send,
  X,
} from 'lucide-vue-next'

withDefaults(
  defineProps<{
    modelValue: string
    sending: boolean
    disabled: boolean
    editingMessageId: string | null
    rejectionReason: string
  }>(),
  {
    modelValue: '',
    sending: false,
    disabled: false,
    editingMessageId: null,
    rejectionReason: '',
  },
)

const emit = defineEmits<{
  (event: 'update:modelValue', value: string): void
  (event: 'send'): void
  (event: 'cancel-edit'): void
}>()

const draftInput = ref<HTMLTextAreaElement | null>(null)

async function focus() {
  await nextTick()
  draftInput.value?.focus()
}

defineExpose({ focus })
</script>

<template>
  <div class="border-t border-gray-100 bg-white p-3">
    <div v-if="rejectionReason" class="mb-2 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
      <AlertTriangle :size="15" class="mt-0.5 shrink-0" />
      <span class="min-w-0 break-words">{{ rejectionReason }}</span>
    </div>
    <div v-if="editingMessageId" class="mb-2 flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-800">
      <Pencil :size="14" class="shrink-0" />
      <span class="min-w-0 flex-1">正在编辑上一条需求，发送后会从这里重新生成。</span>
      <button
        type="button"
        title="取消编辑"
        aria-label="取消编辑"
        class="flex h-6 w-6 shrink-0 items-center justify-center rounded-md hover:bg-blue-100"
        @click="emit('cancel-edit')"
      >
        <X :size="14" />
      </button>
    </div>
    <div class="flex gap-2">
      <textarea
        ref="draftInput"
        :value="modelValue"
        rows="2"
        class="min-h-[52px] flex-1 resize-none rounded-lg border border-gray-200 px-3 py-2 text-sm leading-5 outline-none transition focus:border-gray-500 focus:ring-2 focus:ring-gray-200"
        placeholder="粘贴 URL、OpenAPI/Swagger，或补充额外上下文"
        :disabled="sending || disabled"
        @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
        @keydown.enter.exact.prevent="emit('send')"
      />
      <button
        type="button"
        title="发送"
        aria-label="发送"
        class="flex h-[52px] w-[52px] items-center justify-center rounded-lg bg-gray-950 text-white hover:bg-gray-800 disabled:cursor-not-allowed disabled:bg-gray-300"
        :disabled="sending || !modelValue.trim() || disabled"
        @click="emit('send')"
      >
        <Loader2 v-if="sending" :size="18" class="animate-spin" />
        <Send v-else :size="18" />
      </button>
    </div>
  </div>
</template>
