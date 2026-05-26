<script setup lang="ts">
import {
  AlertTriangle,
  Loader2,
  RotateCcw,
} from 'lucide-vue-next'

withDefaults(
  defineProps<{
    modelValue: string
    cancelCurrent?: boolean
    summary?: Record<string, any> | null
    suggestedInputs?: string[]
    submitting?: boolean
  }>(),
  {
    modelValue: '',
    cancelCurrent: false,
    summary: null,
    suggestedInputs: () => [],
    submitting: false,
  },
)

const emit = defineEmits<{
  (event: 'update:modelValue', value: string): void
  (event: 'update:cancelCurrent', value: boolean): void
  (event: 'submit'): void
}>()
</script>

<template>
  <aside class="bg-white border border-amber-200 rounded-lg shadow-sm p-4">
    <div class="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
      <div class="min-w-0">
        <div class="flex items-center gap-2">
          <AlertTriangle :size="17" class="text-amber-600" />
          <h3 class="text-sm font-bold text-gray-900">人工干预 / 补充上下文</h3>
          <span class="rounded bg-amber-100 px-2 py-0.5 text-[10px] font-bold text-amber-700">{{ summary?.category }}</span>
        </div>
        <p class="mt-2 text-sm leading-6 text-gray-700">{{ summary?.reason }}</p>
        <p class="mt-1 text-xs leading-5 text-amber-700">{{ summary?.recommended_action }}</p>
      </div>
      <div class="shrink-0 rounded-lg border border-amber-100 bg-amber-50 px-3 py-2 text-xs font-bold text-amber-700">
        辅助重跑 {{ summary?.assisted_rerun_enabled ? '可用' : '不可用' }}
      </div>
    </div>

    <div v-if="suggestedInputs.length" class="mt-4 flex flex-wrap gap-2">
      <span
        v-for="item in suggestedInputs"
        :key="item"
        class="rounded-lg border border-gray-200 bg-gray-50 px-2.5 py-1 text-[11px] text-gray-600"
      >
        {{ item }}
      </span>
    </div>

    <slot>
      <div class="mt-4 grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
        <div>
          <textarea
            :value="modelValue"
            rows="4"
            class="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm leading-6 text-gray-700 outline-none transition-all focus:border-amber-400 focus:ring-2 focus:ring-amber-100"
            placeholder="补充登录步骤、测试账号、Token/Header、环境入口、需要跳过或优先验证的范围..."
            @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
          ></textarea>
          <label v-if="summary?.requires_cancel_current" class="mt-2 flex items-center gap-2 text-xs font-bold text-amber-700">
            <input
              :checked="cancelCurrent"
              type="checkbox"
              class="h-4 w-4 rounded border-amber-300 text-amber-600 focus:ring-amber-500"
              @change="emit('update:cancelCurrent', ($event.target as HTMLInputElement).checked)"
            />
            先取消当前运行再发起辅助重跑
          </label>
        </div>
        <button
          type="button"
          :disabled="submitting || !modelValue.trim() || !summary?.assisted_rerun_enabled || (summary?.requires_cancel_current && !cancelCurrent)"
          class="inline-flex h-10 items-center justify-center gap-1.5 rounded-lg bg-amber-600 px-4 text-xs font-bold text-white transition-all hover:bg-amber-700 disabled:cursor-not-allowed disabled:bg-gray-300"
          @click="emit('submit')"
        >
          <Loader2 v-if="submitting" :size="14" class="animate-spin" />
          <RotateCcw v-else :size="14" />
          辅助重跑
        </button>
      </div>
    </slot>
  </aside>
</template>
