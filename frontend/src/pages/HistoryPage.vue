<script setup lang="ts">
import { onMounted, onUnmounted, ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import api from '../lib/api'
import StatusBadge from '../components/StatusBadge.vue'
import LoadingSpinner from '../components/LoadingSpinner.vue'
import EmptyState from '../components/EmptyState.vue'
import Pagination from '../components/Pagination.vue'
import { useToast } from '../composables/useToast'
import {
  Activity,
  AlertTriangle,
  BrainCircuit,
  Eye,
  FileCheck2,
  Filter,
  ShieldCheck,
  Target,
  Trash2,
  TrendingUp,
} from 'lucide-vue-next'

const router = useRouter()
const toast = useToast()
const runs = ref<any[]>([])
const insights = ref<any | null>(null)
const loading = ref(false)
const insightsLoading = ref(false)
const hasLoadedRuns = ref(false)
const hasLoadedInsights = ref(false)
const insightsError = ref('')
const filterStatus = ref('')
const filterType = ref('')
const page = ref(1)
const pageSize = 20
const total = ref(0)

const statusCounts = computed(() => insights.value?.status_counts || {})
const trend = computed(() => insights.value?.quality_trend || {})
const trendBuckets = computed(() => (trend.value?.buckets || []).slice(-14))
const maxTrendTotal = computed(() => Math.max(...trendBuckets.value.map((item: any) => item.total || 0), 1))
const affectedTargets = computed(() => (insights.value?.affected_targets || []).slice(0, 4))
const affectedSurfaces = computed(() => (insights.value?.affected_surfaces || []).slice(0, 5))
const recurringThemes = computed(() => (insights.value?.recurring_themes || []).slice(0, 4))
const evidenceSummary = computed(() => insights.value?.evidence_reproduction || {})
const nextActions = computed(() => insights.value?.recommended_next_actions || [])

const qualityCards = computed(() => [
  {
    label: '近 30 天运行',
    value: insights.value?.analyzed_runs || 0,
    detail: insights.value?.window_run_count > insights.value?.analyzed_runs
      ? `采样 ${insights.value?.analyzed_runs} / ${insights.value?.window_run_count}`
      : '已纳入质量记忆',
    icon: BrainCircuit,
    color: 'text-blue-600',
    bg: 'bg-blue-50',
  },
  {
    label: '完成通过率',
    value: `${formatNumber(statusCounts.value.pass_rate)}%`,
    detail: `${statusCounts.value.succeeded || 0} 通过 / ${statusCounts.value.completed || 0} 完成`,
    icon: ShieldCheck,
    color: 'text-emerald-600',
    bg: 'bg-emerald-50',
  },
  {
    label: '问题运行',
    value: (statusCounts.value.failed || 0) + (statusCounts.value.bug_found || 0),
    detail: `${statusCounts.value.failed || 0} 失败，${statusCounts.value.bug_found || 0} 缺陷`,
    icon: AlertTriangle,
    color: 'text-rose-600',
    bg: 'bg-rose-50',
  },
  {
    label: '证据覆盖',
    value: `${formatNumber(evidenceSummary.value.evidence_rate)}%`,
    detail: `${evidenceSummary.value.runs_with_evidence || 0} 次运行有证据`,
    icon: FileCheck2,
    color: 'text-amber-600',
    bg: 'bg-amber-50',
  },
])

function formatNumber(value: any) {
  const numeric = Number(value || 0)
  return Number.isInteger(numeric) ? String(numeric) : numeric.toFixed(1)
}

function trendToneClass(direction: string | undefined) {
  if (direction === 'improving') return 'bg-emerald-50 text-emerald-700 border-emerald-100'
  if (direction === 'regressing') return 'bg-rose-50 text-rose-700 border-rose-100'
  if (direction === 'stable') return 'bg-blue-50 text-blue-700 border-blue-100'
  return 'bg-gray-50 text-gray-600 border-gray-100'
}

function severityClass(severity: string | undefined) {
  if (severity === 'CRITICAL' || severity === 'HIGH') return 'text-rose-700 bg-rose-50'
  if (severity === 'MEDIUM') return 'text-amber-700 bg-amber-50'
  return 'text-gray-600 bg-gray-100'
}

function formatTime(value: string | null | undefined) {
  if (!value) return ''
  return new Date(value).toLocaleString('zh-CN')
}

async function fetchRuns() {
  loading.value = true
  try {
    const params: any = { page: page.value, page_size: pageSize }
    if (filterStatus.value) params.status = filterStatus.value
    if (filterType.value) params.test_type = filterType.value
    const { data, headers } = await api.get('/runs', { params })
    runs.value = Array.isArray(data) ? data : data.items || []
    total.value = parseInt(headers?.['x-total-count'] || String(runs.value.length))
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '加载历史记录失败')
  } finally {
    loading.value = false
    hasLoadedRuns.value = true
  }
}

async function fetchInsights() {
  insightsLoading.value = true
  insightsError.value = ''
  try {
    const { data } = await api.get('/runs/insights', { params: { days: 30, limit: 100 } })
    insights.value = data
  } catch (err: any) {
    insightsError.value = err?.response?.data?.detail || '加载质量记忆失败'
    toast.error(insightsError.value)
  } finally {
    insightsLoading.value = false
    hasLoadedInsights.value = true
  }
}

async function deleteRun(id: string, e: Event) {
  e.stopPropagation()
  if (!confirm('确定删除此运行记录？')) return
  try {
    await api.delete(`/runs/${id}`)
    toast.success('已删除')
    await fetchRuns()
    fetchInsights()
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '删除失败')
  }
}

let pollTimer: ReturnType<typeof setInterval> | null = null

function startPolling() {
  stopPolling()
  pollTimer = setInterval(() => {
    const hasActive = runs.value.some((r: any) => ['queued', 'running'].includes(r.status))
    if (hasActive) {
      fetchRuns()
      fetchInsights()
    }
  }, 5000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

onMounted(() => {
  fetchRuns()
  fetchInsights()
  startPolling()
})
onUnmounted(() => stopPolling())
</script>

<template>
  <div class="space-y-6 pb-12">
    <div class="flex flex-col gap-1">
      <h2 class="text-2xl font-bold text-gray-900">质量记忆</h2>
      <p class="text-gray-500 text-sm">近期趋势、反复问题、影响面和可复用证据。</p>
    </div>

    <div
      v-if="insightsLoading && !hasLoadedInsights"
      class="bg-white border border-gray-200 rounded-xl shadow-sm p-5 flex items-center gap-3 text-sm text-gray-500"
    >
      <div class="w-4 h-4 border-2 border-gray-200 border-t-blue-600 rounded-full animate-spin shrink-0"></div>
      <span>正在加载质量记忆...</span>
    </div>
    <div
      v-else-if="insightsError && !insights"
      class="bg-white border border-amber-100 rounded-xl shadow-sm p-5 text-sm text-amber-700 bg-amber-50"
    >
      质量记忆暂不可用，运行历史可继续查看。
    </div>
    <template v-else>
      <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        <div
          v-for="card in qualityCards"
          :key="card.label"
          class="bg-white border border-gray-200 rounded-xl shadow-sm p-5"
        >
          <div class="flex items-start justify-between gap-4">
            <div>
              <p class="text-xs font-bold text-gray-500">{{ card.label }}</p>
              <div class="text-2xl font-semibold text-gray-900 mt-2">{{ card.value }}</div>
              <p class="text-xs text-gray-400 mt-1">{{ card.detail }}</p>
            </div>
            <div class="p-2.5 rounded-lg" :class="[card.bg, card.color]">
              <component :is="card.icon" :size="18" />
            </div>
          </div>
        </div>
      </div>

      <div class="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <div class="xl:col-span-2 bg-white border border-gray-200 rounded-xl shadow-sm p-5">
          <div class="flex flex-wrap items-start justify-between gap-3 mb-5">
            <div>
              <h3 class="font-semibold text-gray-900">质量趋势</h3>
              <p class="text-xs text-gray-500 mt-1">{{ trend.rationale || '暂无足够样本判断趋势。' }}</p>
            </div>
            <div class="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs font-bold" :class="trendToneClass(trend.direction)">
              <TrendingUp :size="14" />
              <span>{{ trend.label || '样本不足' }}</span>
            </div>
          </div>

          <div v-if="trendBuckets.length" class="h-36 flex items-end gap-2">
            <div v-for="bucket in trendBuckets" :key="bucket.date" class="flex-1 h-full flex flex-col items-center justify-end gap-2 min-w-0">
              <div class="text-[10px] text-gray-400 font-semibold">{{ bucket.total || 0 }}</div>
              <div
                class="w-full max-w-8 bg-gray-100 rounded-sm overflow-hidden flex flex-col justify-end"
                :style="{ height: Math.max(((bucket.total || 0) / maxTrendTotal) * 100, bucket.total ? 10 : 4) + '%' }"
              >
                <div
                  v-if="bucket.bug_found"
                  class="w-full bg-rose-500"
                  :style="{ height: Math.max((bucket.bug_found / Math.max(bucket.total, 1)) * 100, 12) + '%' }"
                />
                <div
                  v-if="bucket.failed"
                  class="w-full bg-amber-500"
                  :style="{ height: Math.max((bucket.failed / Math.max(bucket.total, 1)) * 100, 12) + '%' }"
                />
                <div
                  v-if="bucket.succeeded"
                  class="w-full bg-emerald-500"
                  :style="{ height: Math.max((bucket.succeeded / Math.max(bucket.total, 1)) * 100, 12) + '%' }"
                />
                <div
                  v-if="bucket.active"
                  class="w-full bg-blue-400"
                  :style="{ height: Math.max((bucket.active / Math.max(bucket.total, 1)) * 100, 12) + '%' }"
                />
              </div>
              <span class="text-[10px] text-gray-400 font-mono truncate">{{ bucket.date }}</span>
            </div>
          </div>
          <div v-else class="h-36 flex items-center justify-center text-gray-400 text-sm">暂无趋势数据</div>
        </div>

        <div class="bg-white border border-gray-200 rounded-xl shadow-sm p-5">
          <div class="flex items-center justify-between mb-4">
            <h3 class="font-semibold text-gray-900">证据与复现</h3>
            <FileCheck2 :size="16" class="text-gray-400" />
          </div>
          <div class="space-y-3">
            <div class="flex items-center justify-between text-sm">
              <span class="text-gray-500">API 结果证据</span>
              <span class="font-semibold text-gray-900">{{ evidenceSummary.runs_with_api_evidence || 0 }}</span>
            </div>
            <div class="flex items-center justify-between text-sm">
              <span class="text-gray-500">截图证据</span>
              <span class="font-semibold text-gray-900">{{ evidenceSummary.runs_with_screenshots || 0 }}</span>
            </div>
            <div class="flex items-center justify-between text-sm">
              <span class="text-gray-500">可复现步骤</span>
              <span class="font-semibold text-gray-900">{{ evidenceSummary.runs_with_reproduction || 0 }}</span>
            </div>
            <div class="flex items-center justify-between text-sm">
              <span class="text-gray-500">复现脚本</span>
              <span class="font-semibold text-gray-900">{{ evidenceSummary.runs_with_scripts || 0 }}</span>
            </div>
          </div>
          <div v-if="nextActions.length" class="mt-5 pt-4 border-t border-gray-100 space-y-2">
            <div v-for="action in nextActions.slice(0, 3)" :key="action" class="flex gap-2 text-xs text-gray-600 leading-relaxed">
              <Activity :size="13" class="text-blue-500 mt-0.5 shrink-0" />
              <span>{{ action }}</span>
            </div>
          </div>
        </div>
      </div>

      <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div class="bg-white border border-gray-200 rounded-xl shadow-sm p-5">
          <div class="flex items-center justify-between mb-4">
            <h3 class="font-semibold text-gray-900">受影响目标</h3>
            <Target :size="16" class="text-gray-400" />
          </div>
          <div v-if="affectedTargets.length" class="space-y-3">
            <div v-for="target in affectedTargets" :key="target.target" class="border-b border-gray-100 pb-3 last:border-b-0 last:pb-0">
              <div class="text-sm font-medium text-gray-900 truncate">{{ target.target }}</div>
              <div class="text-xs text-gray-500 mt-1">
                {{ target.issue_run_count }} 次问题 / {{ target.run_count }} 次运行
              </div>
              <div class="text-[11px] text-gray-400 mt-1">{{ formatTime(target.last_seen) }}</div>
            </div>
          </div>
          <p v-else class="text-sm text-gray-400 py-6 text-center">暂无高频受影响目标</p>
        </div>

        <div class="bg-white border border-gray-200 rounded-xl shadow-sm p-5">
          <div class="flex items-center justify-between mb-4">
            <h3 class="font-semibold text-gray-900">反复问题</h3>
            <BrainCircuit :size="16" class="text-gray-400" />
          </div>
          <div v-if="recurringThemes.length" class="space-y-3">
            <div v-for="themeItem in recurringThemes" :key="themeItem.theme" class="border-b border-gray-100 pb-3 last:border-b-0 last:pb-0">
              <div class="flex items-start justify-between gap-3">
                <div class="min-w-0">
                  <div class="text-sm font-medium text-gray-900 line-clamp-2">{{ themeItem.theme }}</div>
                  <div class="text-xs text-gray-500 mt-1">{{ themeItem.count }} 次出现</div>
                </div>
                <span class="px-2 py-0.5 rounded text-[10px] font-bold shrink-0" :class="severityClass(themeItem.severity)">
                  {{ themeItem.severity }}
                </span>
              </div>
              <div v-if="themeItem.surfaces?.length" class="text-[11px] text-gray-400 mt-2 truncate">
                {{ themeItem.surfaces.join('，') }}
              </div>
            </div>
          </div>
          <p v-else class="text-sm text-gray-400 py-6 text-center">暂无可归并的反复问题</p>
        </div>

        <div class="bg-white border border-gray-200 rounded-xl shadow-sm p-5">
          <div class="flex items-center justify-between mb-4">
            <h3 class="font-semibold text-gray-900">测试面记忆</h3>
            <AlertTriangle :size="16" class="text-gray-400" />
          </div>
          <div v-if="affectedSurfaces.length" class="space-y-3">
            <div v-for="surface in affectedSurfaces" :key="`${surface.type}-${surface.name}`" class="border-b border-gray-100 pb-3 last:border-b-0 last:pb-0">
              <div class="flex items-center gap-2 min-w-0">
                <span class="px-2 py-0.5 bg-gray-100 text-gray-500 rounded text-[10px] font-bold">{{ surface.type }}</span>
                <span class="text-sm font-medium text-gray-900 truncate">{{ surface.name }}</span>
              </div>
              <div class="text-xs text-gray-500 mt-1">{{ surface.issue_count }} 次关联问题</div>
              <p v-if="surface.detail" class="text-[11px] text-gray-400 mt-1 line-clamp-2">{{ surface.detail }}</p>
            </div>
          </div>
          <p v-else class="text-sm text-gray-400 py-6 text-center">暂无受影响测试面</p>
        </div>
      </div>
    </template>

      <!-- Filters -->
      <div class="bg-white border border-gray-200 rounded-xl shadow-sm p-4 flex flex-wrap gap-4 items-center">
        <div class="flex items-center gap-2">
          <Filter :size="14" class="text-gray-400" />
          <span class="text-xs text-gray-500 font-bold">筛选</span>
        </div>
        <select v-model="filterStatus" @change="page = 1; fetchRuns()"
          class="px-3 py-1.5 bg-gray-50 border border-gray-200 rounded-lg text-xs outline-none focus:border-blue-500">
          <option value="">全部状态</option>
          <option value="succeeded">通过</option>
          <option value="failed">失败</option>
          <option value="bug_found">缺陷</option>
          <option value="queued">排队中</option>
          <option value="running">运行中</option>
        </select>
        <select v-model="filterType" @change="page = 1; fetchRuns()"
          class="px-3 py-1.5 bg-gray-50 border border-gray-200 rounded-lg text-xs outline-none focus:border-blue-500">
          <option value="">全部类型</option>
          <option value="AUTO">自动</option>
          <option value="API">API</option>
          <option value="UI">UI</option>
        </select>
        <span class="text-xs text-gray-400 ml-auto">
          {{ !hasLoadedRuns ? '正在加载记录...' : `${total} 条记录` }}
        </span>
      </div>

      <!-- Run List -->
      <LoadingSpinner v-if="loading || !hasLoadedRuns" text="加载运行记录中..." />
      <EmptyState
        v-else-if="!runs.length"
        :icon="Filter"
        :title="total ? '没有匹配的记录' : '暂无运行记录'"
        :description="total ? '请尝试调整筛选条件' : '点击开始测试创建第一次运行'"
      />
      <div v-else class="space-y-3">
        <div v-for="run in runs" :key="run.id"
          @click="router.push(`/runs/${run.id}`)"
          class="bg-white border border-gray-200 rounded-xl p-5 shadow-sm hover:border-blue-200 transition-all cursor-pointer group">
          <div class="flex items-start justify-between">
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-3 mb-2">
                <StatusBadge :status="run.status" />
                <span class="px-2 py-0.5 bg-gray-100 text-gray-600 rounded text-[10px] font-bold uppercase">{{ run.test_type }}</span>
              </div>
              <div class="font-bold text-gray-900 text-sm truncate">{{ run.objective }}</div>
              <div class="text-xs font-mono text-gray-400 mt-0.5 truncate">{{ run.target_url }}</div>
              <div class="text-xs text-gray-400 mt-1">{{ run.created_at ? new Date(run.created_at).toLocaleString('zh-CN') : '' }}</div>
            </div>
            <div class="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
              <button @click="deleteRun(run.id, $event)" class="p-2 text-gray-400 hover:text-red-600 transition-colors">
                <Trash2 :size="14" />
              </button>
              <button class="p-2 text-gray-400 group-hover:text-blue-600 transition-colors">
                <Eye :size="16" />
              </button>
            </div>
          </div>
        </div>
      </div>

      <Pagination :page="page" :page-size="pageSize" :total="total" @update:page="page = $event; fetchRuns()" />
  </div>
</template>
