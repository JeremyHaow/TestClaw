<script setup lang="ts">
import { computed } from 'vue'
import { CheckSquare, Eye, Pencil, Sparkles, Square, Trash2 } from 'lucide-vue-next'

const props = withDefaults(defineProps<{
  item: Record<string, any>
  selected?: boolean
  suiteNames?: string[]
}>(), {
  selected: false,
  suiteNames: () => [],
})

defineEmits<{
  (event: 'select'): void
  (event: 'detail'): void
  (event: 'plan'): void
  (event: 'edit'): void
  (event: 'delete'): void
}>()

function caseAsset(item: Record<string, any>) {
  return item.test_data?.case_asset || {}
}

function formatStep(value: any): string {
  return typeof value === 'string' ? value : JSON.stringify(value)
}

const asset = computed(() => caseAsset(props.item))
const steps = computed(() => Array.isArray(props.item.steps) ? props.item.steps : [])
const expected = computed(() => Array.isArray(props.item.expected) ? props.item.expected : [])
const caseType = computed(() => {
  const assetType = String(asset.value.case_type || '').toLowerCase()
  if (assetType) return assetType
  const category = String(props.item.category || '').toLowerCase()
  if (category.includes('api') || props.item.test_data?.request_template) return 'api'
  if (category.includes('ui') || category.includes('page') || props.item.test_data?.playwright_commands) return 'ui'
  return 'case'
})
const sourceKind = computed(() => {
  const source = String(props.item.source || '')
  if (asset.value.source_run_id || source.startsWith('run_case_asset:')) return '运行沉淀'
  if (source.startsWith('agent:')) return 'Agent 生成'
  if (source) return source
  return '手动维护'
})
const sourceDetail = computed(() => {
  const source = asset.value.source ? `${asset.value.source} #${Number(asset.value.source_index ?? 0) + 1}` : ''
  return source || props.item.source || 'manual'
})
const sourceRunId = computed(() => {
  if (asset.value.source_run_id) return String(asset.value.source_run_id)
  const source = String(props.item.source || '')
  if (source.startsWith('run_case_asset:')) return source.split(':')[1] || ''
  return ''
})
const projectLabel = computed(() => (
  props.item.test_data?.project
  || props.item.test_data?.project_id
  || props.item.test_data?.target_url
  || props.item.test_data?.base_url
  || ''
))
const previewText = computed(() => {
  if (!steps.value.length) return '未记录步骤'
  return steps.value.slice(0, 2).map(formatStep).join(' / ')
})
const resultLabel = computed(() => props.item.last_result || props.item.test_data?.last_result || '待运行')
const cardClass = computed(() => props.selected
  ? 'border-blue-300 bg-blue-50/50 shadow-[0_12px_30px_rgba(37,99,235,0.10)]'
  : 'border-gray-200 bg-white shadow-sm hover:border-blue-200 hover:shadow-[0_12px_30px_rgba(15,23,42,0.08)]')

function priorityClass(priority: string) {
  if (priority === 'P0') return 'bg-red-100 text-red-700'
  if (priority === 'P1') return 'bg-orange-100 text-orange-700'
  if (priority === 'P2') return 'bg-yellow-100 text-yellow-700'
  return 'bg-gray-100 text-gray-600'
}
</script>

<template>
  <article
    data-testid="test-case-asset-card"
    class="rounded-lg border p-4 transition-all"
    :class="cardClass"
  >
    <div class="flex items-start gap-3">
      <button
        type="button"
        class="mt-1 text-gray-400 transition-colors hover:text-blue-600"
        :title="selected ? '取消选择用例' : '选择用例'"
        :aria-label="selected ? '取消选择用例' : '选择用例'"
        @click="$emit('select')"
      >
        <CheckSquare v-if="selected" :size="17" class="text-blue-600" />
        <Square v-else :size="17" />
      </button>
      <div class="min-w-0 flex-1">
        <div class="flex flex-wrap items-center gap-2">
          <h3 class="min-w-0 flex-1 truncate text-sm font-semibold text-gray-950">{{ item.title }}</h3>
          <span class="rounded px-2 py-0.5 text-[10px] font-bold uppercase"
            :class="caseType === 'api' ? 'bg-blue-50 text-blue-700' : caseType === 'ui' ? 'bg-indigo-50 text-indigo-700' : 'bg-gray-100 text-gray-600'">
            {{ caseType }}
          </span>
          <span class="rounded px-2 py-0.5 text-[10px] font-bold" :class="priorityClass(item.priority)">
            {{ item.priority }}
          </span>
        </div>
        <p class="mt-2 line-clamp-2 text-xs leading-5 text-gray-500">{{ previewText }}</p>
      </div>
    </div>

    <div class="mt-4 grid gap-2 sm:grid-cols-3">
      <div class="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
        <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">来源</div>
        <div class="mt-1 truncate text-xs font-semibold text-gray-800">{{ sourceKind }}</div>
      </div>
      <div class="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
        <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">最近结果</div>
        <div class="mt-1 truncate text-xs font-semibold text-gray-800">{{ resultLabel }}</div>
      </div>
      <div class="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
        <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">步骤 / 预期</div>
        <div class="mt-1 text-xs font-semibold text-gray-800">{{ steps.length }} / {{ expected.length }}</div>
      </div>
    </div>

    <div class="mt-3 space-y-1 text-[11px] text-gray-500">
      <div class="truncate font-mono">{{ sourceDetail }}</div>
      <div v-if="sourceRunId" class="truncate font-mono">run {{ sourceRunId.slice(0, 8) }}</div>
      <div v-if="projectLabel" class="truncate font-mono">{{ projectLabel }}</div>
      <div v-if="suiteNames.length" class="flex flex-wrap gap-1 pt-1">
        <span
          v-for="suite in suiteNames.slice(0, 2)"
          :key="suite"
          class="max-w-[180px] truncate rounded border border-emerald-100 bg-emerald-50 px-2 py-0.5 text-[10px] font-bold text-emerald-700"
        >
          {{ suite }}
        </span>
        <span v-if="suiteNames.length > 2" class="text-[10px] text-gray-400">+{{ suiteNames.length - 2 }}</span>
      </div>
      <div v-else class="pt-1 text-xs text-gray-400">未入套件</div>
    </div>

    <div class="mt-4 flex flex-wrap items-center gap-2">
      <button
        type="button"
        class="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-bold text-gray-700 transition-all hover:border-gray-300 hover:bg-gray-50"
        @click="$emit('detail')"
      >
        <Eye :size="13" /> 查看
      </button>
      <button
        type="button"
        class="inline-flex items-center gap-1.5 rounded-lg border border-blue-200 bg-blue-50 px-3 py-1.5 text-xs font-bold text-blue-700 transition-all hover:bg-blue-100"
        @click="$emit('plan')"
      >
        <Sparkles :size="13" /> 加入计划
      </button>
      <div class="ml-auto flex items-center gap-1">
        <button
          type="button"
          class="rounded-lg p-1.5 text-gray-400 transition-all hover:bg-blue-50 hover:text-blue-600"
          title="编辑"
          aria-label="编辑用例"
          @click="$emit('edit')"
        >
          <Pencil :size="14" />
        </button>
        <button
          type="button"
          class="rounded-lg p-1.5 text-gray-400 transition-all hover:bg-red-50 hover:text-red-600"
          title="删除"
          aria-label="删除用例"
          @click="$emit('delete')"
        >
          <Trash2 :size="14" />
        </button>
      </div>
    </div>
  </article>
</template>
