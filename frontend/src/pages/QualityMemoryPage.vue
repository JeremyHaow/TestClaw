<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type {
  TrendChart,
  TrendChartOption,
} from '../lib/qualityTrendChart'
import api from '../lib/api'
import { useToast } from '../composables/useToast'
import {
  Activity,
  AlertTriangle,
  BrainCircuit,
  FileCheck2,
  ShieldCheck,
  Target,
  TrendingUp,
} from 'lucide-vue-next'

type TrendBucket = {
  date: string
  succeeded?: number
  failed?: number
  bug_found?: number
  active?: number
  total?: number
}

type TrendChartModule = typeof import('../lib/qualityTrendChart')

const trendSegments = [
  { key: 'succeeded', label: '通过', tooltipLabel: '通过', color: '#10b981' },
  { key: 'failed', label: '失败', tooltipLabel: '失败', color: '#f59e0b' },
  { key: 'bug_found', label: '缺陷', tooltipLabel: '缺陷', color: '#f43f5e' },
  { key: 'active', label: '进行中', tooltipLabel: '进行中', color: '#60a5fa' },
] as const

const toast = useToast()
const insights = ref<any | null>(null)
const loading = ref(false)
const hasLoaded = ref(false)
const error = ref('')
const trendChartEl = ref<HTMLDivElement | null>(null)
let trendChart: TrendChart | null = null
let trendResizeObserver: ResizeObserver | null = null
let observedTrendEl: HTMLDivElement | null = null
let trendChartModule: TrendChartModule | null = null
let trendChartModulePromise: Promise<TrendChartModule> | null = null
let isUnmounted = false

const statusCounts = computed(() => insights.value?.status_counts || {})
const trend = computed(() => insights.value?.quality_trend || {})
const trendBuckets = computed<TrendBucket[]>(() => (trend.value?.buckets || []).slice(-14))
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

function countValue(value: unknown) {
  const numeric = Number(value || 0)
  return Number.isFinite(numeric) ? numeric : 0
}

function escapeHtml(value: unknown) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => {
    const replacements: Record<string, string> = {
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }
    return replacements[char]
  })
}

function formatBucketDateLabel(value: string) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value) ? value.slice(5) : value
}

async function loadTrendChartModule() {
  if (trendChartModule) return trendChartModule
  if (!trendChartModulePromise) {
    trendChartModulePromise = import('../lib/qualityTrendChart').then((module) => {
      trendChartModule = module
      return module
    })
  }
  return trendChartModulePromise
}

function buildTrendTooltip(bucket: TrendBucket) {
  const rows = trendSegments.map((segment) => {
    const value = countValue(bucket[segment.key])
    return `
      <div style="display:flex;align-items:center;justify-content:space-between;gap:18px;margin-top:4px;">
        <span style="display:flex;align-items:center;gap:6px;color:#4b5563;">
          <span style="width:8px;height:8px;border-radius:999px;background:${segment.color};display:inline-block;"></span>
          ${segment.tooltipLabel}
        </span>
        <strong style="color:#111827;">${value}</strong>
      </div>
    `
  }).join('')

  return `
    <div style="min-width:170px;">
      <div style="font-weight:700;color:#111827;margin-bottom:6px;">${escapeHtml(bucket.date)}</div>
      ${rows}
      <div style="display:flex;align-items:center;justify-content:space-between;gap:18px;margin-top:7px;padding-top:7px;border-top:1px solid #e5e7eb;">
        <span style="color:#4b5563;">合计</span>
        <strong style="color:#111827;">${countValue(bucket.total)}</strong>
      </div>
    </div>
  `
}

function buildTrendChartOption(buckets: TrendBucket[]): TrendChartOption {
  return {
    color: trendSegments.map((segment) => segment.color),
    tooltip: {
      trigger: 'axis',
      confine: true,
      axisPointer: {
        type: 'shadow',
        shadowStyle: {
          color: 'rgba(15, 23, 42, 0.06)',
        },
      },
      borderColor: '#e5e7eb',
      borderWidth: 1,
      padding: 10,
      textStyle: {
        fontSize: 12,
      },
      formatter: (params: any) => {
        const item = Array.isArray(params) ? params[0] : params
        const bucket = buckets[item?.dataIndex ?? 0]
        return buildTrendTooltip(bucket || { date: '' })
      },
    },
    legend: {
      top: 0,
      right: 0,
      itemWidth: 8,
      itemHeight: 8,
      icon: 'circle',
      data: trendSegments.map((segment) => segment.label),
      textStyle: {
        color: '#6b7280',
        fontSize: 11,
        fontWeight: 600,
      },
    },
    grid: {
      top: 34,
      right: 8,
      bottom: 28,
      left: 8,
      containLabel: true,
    },
    xAxis: {
      type: 'category',
      data: buckets.map((bucket) => formatBucketDateLabel(bucket.date)),
      axisTick: { show: false },
      axisLine: {
        lineStyle: {
          color: '#e5e7eb',
        },
      },
      axisLabel: {
        color: '#9ca3af',
        fontSize: 10,
        hideOverlap: true,
        margin: 10,
      },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      axisTick: { show: false },
      axisLine: { show: false },
      splitLine: {
        lineStyle: {
          color: '#f3f4f6',
        },
      },
      axisLabel: {
        color: '#9ca3af',
        fontSize: 10,
      },
    },
    series: trendSegments.map((segment) => ({
      name: segment.label,
      type: 'bar',
      stack: 'quality',
      barMaxWidth: 30,
      emphasis: {
        focus: 'series',
      },
      data: buckets.map((bucket) => countValue(bucket[segment.key])),
    })),
  }
}

function resizeTrendChart() {
  trendChart?.resize()
}

function syncTrendResizeObserver() {
  const element = trendChartEl.value
  if (!element || observedTrendEl === element || typeof ResizeObserver === 'undefined') return
  trendResizeObserver?.disconnect()
  trendResizeObserver = new ResizeObserver(() => resizeTrendChart())
  trendResizeObserver.observe(element)
  observedTrendEl = element
}

function disposeTrendChart() {
  trendResizeObserver?.disconnect()
  trendResizeObserver = null
  observedTrendEl = null
  trendChart?.dispose()
  trendChart = null
}

async function renderTrendChart() {
  const buckets = trendBuckets.value
  if (!buckets.length) {
    disposeTrendChart()
    return
  }

  await nextTick()
  const element = trendChartEl.value
  if (!element) return

  const chartModule = await loadTrendChartModule()
  if (isUnmounted || !trendBuckets.value.length || trendChartEl.value !== element) return

  if (!trendChart) {
    trendChart = chartModule.initTrendChart(element)
  }
  syncTrendResizeObserver()
  trendChart.setOption(buildTrendChartOption(trendBuckets.value), true)
  resizeTrendChart()
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

async function fetchInsights() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await api.get('/runs/insights', { params: { days: 30, limit: 100 } })
    insights.value = data
  } catch (err: any) {
    error.value = err?.response?.data?.detail || '加载质量记忆失败'
    toast.error(error.value)
  } finally {
    loading.value = false
    hasLoaded.value = true
  }
}

onMounted(() => {
  isUnmounted = false
  window.addEventListener('resize', resizeTrendChart)
  fetchInsights()
})

onBeforeUnmount(() => {
  isUnmounted = true
  window.removeEventListener('resize', resizeTrendChart)
  disposeTrendChart()
})

watch(trendBuckets, () => {
  renderTrendChart()
}, { deep: true, flush: 'post' })
</script>

<template>
  <div class="mx-auto max-w-7xl space-y-5 pb-10">
    <div class="flex flex-col gap-1 border-b border-gray-200/80 pb-5">
      <div class="tc-page-kicker">Insights</div>
      <h2 class="text-xl font-semibold tracking-tight text-gray-950">质量记忆</h2>
      <p class="text-gray-500 text-sm">近期趋势、反复问题、影响面和可复用证据。</p>
    </div>

    <div
      v-if="loading && !hasLoaded"
      class="bg-white border border-gray-200 rounded-lg shadow-sm p-4 flex items-center gap-3 text-sm text-gray-500"
    >
      <div class="w-4 h-4 border-2 border-gray-200 border-t-gray-900 rounded-full animate-spin shrink-0"></div>
      <span>正在加载质量记忆...</span>
    </div>
    <div
      v-else-if="error && !insights"
      class="bg-amber-50 border border-amber-100 rounded-lg shadow-sm p-4 text-sm text-amber-700"
    >
      质量记忆暂不可用，运行历史可继续查看。
    </div>
    <template v-else>
      <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
        <div
          v-for="card in qualityCards"
          :key="card.label"
          class="bg-white border border-gray-200 rounded-lg shadow-sm p-4"
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
        <div class="xl:col-span-2 bg-white border border-gray-200 rounded-lg shadow-sm p-4">
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

          <div v-if="trendBuckets.length" class="min-h-56 h-64 sm:h-72 w-full">
            <div ref="trendChartEl" class="h-full w-full"></div>
          </div>
          <div v-else class="h-36 flex items-center justify-center text-gray-400 text-sm">暂无趋势数据</div>
        </div>

        <div class="bg-white border border-gray-200 rounded-lg shadow-sm p-4">
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
        <div class="bg-white border border-gray-200 rounded-lg shadow-sm p-4">
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

        <div class="bg-white border border-gray-200 rounded-lg shadow-sm p-4">
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

        <div class="bg-white border border-gray-200 rounded-lg shadow-sm p-4">
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
  </div>
</template>
