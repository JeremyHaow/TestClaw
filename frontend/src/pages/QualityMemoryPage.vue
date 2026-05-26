<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { Component } from 'vue'
import { useRouter } from 'vue-router'
import type {
  TrendChart,
  TrendChartOption,
} from '../lib/qualityTrendChart'
import api from '../lib/api'
import { useToast } from '../composables/useToast'
import TcButton from '../components/ui/TcButton.vue'
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  BrainCircuit,
  CheckCircle2,
  ClipboardList,
  FileCheck2,
  RefreshCcw,
  ShieldCheck,
  Sparkles,
  Target,
  TrendingUp,
  X,
} from 'lucide-vue-next'

type TrendBucket = {
  date: string
  succeeded?: number
  failed?: number
  bug_found?: number
  active?: number
  total?: number
}

type StatusCounts = {
  total?: number
  pending?: number
  queued?: number
  running?: number
  completed?: number
  succeeded?: number
  failed?: number
  bug_found?: number
  cancelled?: number
  active?: number
  pass_rate?: number
  issue_rate?: number
  bug_rate?: number
}

type QualityTrend = {
  direction?: string
  label?: string
  rationale?: string
  buckets?: TrendBucket[]
}

type AffectedTarget = {
  target: string
  run_count: number
  issue_run_count: number
  failed_count: number
  bug_count: number
  last_seen?: string | null
}

type AffectedSurface = {
  type: string
  name: string
  issue_count: number
  last_seen?: string | null
  detail?: string | null
}

type RecurringTheme = {
  theme: string
  category: string
  count: number
  severity: string
  surfaces?: string[]
  examples?: string[]
  last_seen?: string | null
  recommended_action?: string
}

type EvidenceSummary = {
  runs_with_evidence?: number
  runs_with_api_evidence?: number
  runs_with_screenshots?: number
  runs_with_tool_calls?: number
  runs_with_reproduction?: number
  runs_with_scripts?: number
  evidence_rate?: number
  reproduction_rate?: number
}

type RunHistoryInsights = {
  generated_at?: string
  window_days?: number
  sample_limit?: number
  window_run_count?: number
  analyzed_runs?: number
  status_counts?: StatusCounts
  quality_trend?: QualityTrend
  affected_targets?: AffectedTarget[]
  affected_surfaces?: AffectedSurface[]
  recurring_themes?: RecurringTheme[]
  evidence_reproduction?: EvidenceSummary
  recommended_next_actions?: string[]
}

type StatCard = {
  label: string
  value: string
  hint: string
  icon: Component
  tone: string
}

type ReusableAsset = {
  key: string
  label: string
  count: number
  unit: string
  hint: string
  planFocus: string
  icon: Component
  tone: string
}

type BreakdownItem = {
  label: string
  value: number
  percent: number
  barClass: string
  textClass: string
}

type TrendChartModule = typeof import('../lib/qualityTrendChart')

const trendSegments = [
  { key: 'succeeded', label: '通过', tooltipLabel: '通过', color: '#10b981' },
  { key: 'failed', label: '失败', tooltipLabel: '失败', color: '#f59e0b' },
  { key: 'bug_found', label: '缺陷', tooltipLabel: '缺陷', color: '#f43f5e' },
  { key: 'active', label: '进行中', tooltipLabel: '进行中', color: '#60a5fa' },
] as const

const REDACTED_VALUE = '[REDACTED]'

const router = useRouter()
const toast = useToast()
const insights = ref<RunHistoryInsights | null>(null)
const loading = ref(false)
const hasLoaded = ref(false)
const error = ref('')
const selectedMemoryTarget = ref('')
const trendChartEl = ref<HTMLDivElement | null>(null)
let trendChart: TrendChart | null = null
let trendResizeObserver: ResizeObserver | null = null
let observedTrendEl: HTMLDivElement | null = null
let trendChartModule: TrendChartModule | null = null
let trendChartModulePromise: Promise<TrendChartModule> | null = null
let isUnmounted = false

const statusCounts = computed<StatusCounts>(() => insights.value?.status_counts || {})
const trend = computed<QualityTrend>(() => insights.value?.quality_trend || {})
const trendBuckets = computed<TrendBucket[]>(() => (trend.value?.buckets || []).slice(-14))
const targetMemories = computed<AffectedTarget[]>(() => (insights.value?.affected_targets || []).slice(0, 6))
const affectedSurfaces = computed<AffectedSurface[]>(() => (insights.value?.affected_surfaces || []).slice(0, 6))
const recurringThemes = computed<RecurringTheme[]>(() => (insights.value?.recurring_themes || []).slice(0, 6))
const highFrequencyThemes = computed<RecurringTheme[]>(() => recurringThemes.value.slice(0, 4))
const evidenceSummary = computed<EvidenceSummary>(() => insights.value?.evidence_reproduction || {})
const nextActions = computed<string[]>(() => (insights.value?.recommended_next_actions || []).slice(0, 4))
const selectedMemory = computed(() => {
  if (!selectedMemoryTarget.value) return null
  return targetMemories.value.find((memory) => memory.target === selectedMemoryTarget.value) || null
})
const blockerOccurrenceCount = computed(() => recurringThemes.value.reduce((sum, theme) => sum + numericValue(theme.count), 0))
const reusableCaseCount = computed(() => (
  numericValue(evidenceSummary.value.runs_with_api_evidence)
  + numericValue(evidenceSummary.value.runs_with_reproduction)
  + numericValue(evidenceSummary.value.runs_with_scripts)
))
const reusableAssetCount = computed(() => reusableAssets.value.reduce((sum, asset) => sum + asset.count, 0))
const reuseRate = computed(() => clampPercent(evidenceSummary.value.reproduction_rate || evidenceSummary.value.evidence_rate || 0))
const hasAnyMemory = computed(() => Boolean(
  insights.value
  && (
    numericValue(insights.value.analyzed_runs)
    || targetMemories.value.length
    || recurringThemes.value.length
    || reusableAssetCount.value
  ),
))
const statusTotal = computed(() => (
  numericValue(statusCounts.value.total)
  || numericValue(insights.value?.analyzed_runs)
  || numericValue(insights.value?.window_run_count)
  || (
    numericValue(statusCounts.value.succeeded)
    + numericValue(statusCounts.value.failed)
    + numericValue(statusCounts.value.bug_found)
    + numericValue(statusCounts.value.cancelled)
    + numericValue(statusCounts.value.active)
    + numericValue(statusCounts.value.running)
  )
))
const statusBreakdownItems = computed<BreakdownItem[]>(() => {
  const total = statusTotal.value
  return [
    {
      label: '通过',
      value: numericValue(statusCounts.value.succeeded),
      barClass: 'bg-emerald-500',
      textClass: 'text-emerald-700',
    },
    {
      label: '缺陷',
      value: numericValue(statusCounts.value.bug_found),
      barClass: 'bg-rose-500',
      textClass: 'text-rose-700',
    },
    {
      label: '失败',
      value: numericValue(statusCounts.value.failed),
      barClass: 'bg-amber-500',
      textClass: 'text-amber-700',
    },
    {
      label: '取消',
      value: numericValue(statusCounts.value.cancelled),
      barClass: 'bg-gray-400',
      textClass: 'text-gray-600',
    },
    {
      label: '进行中',
      value: numericValue(statusCounts.value.active) + numericValue(statusCounts.value.running),
      barClass: 'bg-blue-500',
      textClass: 'text-blue-700',
    },
  ].filter((item) => item.value > 0 || total === 0).map((item) => ({
    ...item,
    percent: total ? Number(((item.value / total) * 100).toFixed(1)) : 0,
  }))
})
const evidenceCoverageItems = computed<BreakdownItem[]>(() => {
  const total = numericValue(insights.value?.analyzed_runs) || statusTotal.value
  return [
    {
      label: '运行证据',
      value: numericValue(evidenceSummary.value.runs_with_evidence),
      barClass: 'bg-blue-500',
      textClass: 'text-blue-700',
    },
    {
      label: '工具调用',
      value: numericValue(evidenceSummary.value.runs_with_tool_calls),
      barClass: 'bg-violet-500',
      textClass: 'text-violet-700',
    },
    {
      label: '复现步骤',
      value: numericValue(evidenceSummary.value.runs_with_reproduction),
      barClass: 'bg-emerald-500',
      textClass: 'text-emerald-700',
    },
    {
      label: '截图证据',
      value: numericValue(evidenceSummary.value.runs_with_screenshots),
      barClass: 'bg-indigo-500',
      textClass: 'text-indigo-700',
    },
  ].map((item) => ({
    ...item,
    percent: total ? clampPercent((item.value / total) * 100) : 0,
  }))
})
const topRiskThemes = computed<RecurringTheme[]>(() => highFrequencyThemes.value.slice(0, 3))

const memoryStatCards = computed<StatCard[]>(() => [
  {
    label: '已记忆目标',
    value: String(targetMemories.value.length),
    hint: `${insights.value?.analyzed_runs || 0} 次运行已纳入质量记忆`,
    icon: Target,
    tone: 'border-blue-100 bg-blue-50 text-blue-700',
  },
  {
    label: '复用用例',
    value: String(reusableCaseCount.value),
    hint: `${numericValue(evidenceSummary.value.runs_with_reproduction)} 组复现步骤 / ${numericValue(evidenceSummary.value.runs_with_scripts)} 个脚本`,
    icon: ClipboardList,
    tone: 'border-emerald-100 bg-emerald-50 text-emerald-700',
  },
  {
    label: '高频阻塞',
    value: String(recurringThemes.value.length),
    hint: `${blockerOccurrenceCount.value} 次重复出现`,
    icon: AlertTriangle,
    tone: 'border-rose-100 bg-rose-50 text-rose-700',
  },
  {
    label: '平均复用率',
    value: `${formatNumber(reuseRate.value)}%`,
    hint: `${numericValue(evidenceSummary.value.runs_with_evidence)} 次运行有可复用证据`,
    icon: ShieldCheck,
    tone: 'border-amber-100 bg-amber-50 text-amber-700',
  },
])

const reusableAssets = computed<ReusableAsset[]>(() => [
  {
    key: 'api-evidence',
    label: 'API 结果证据',
    count: numericValue(evidenceSummary.value.runs_with_api_evidence),
    unit: '次运行',
    hint: '复用已有请求、响应和断言线索，优先覆盖接口回归。',
    planFocus: '复用 API 结果证据，优先生成接口回归和契约检查计划。',
    icon: FileCheck2,
    tone: 'border-blue-100 bg-blue-50 text-blue-700',
  },
  {
    key: 'screenshots',
    label: '截图证据',
    count: numericValue(evidenceSummary.value.runs_with_screenshots),
    unit: '次运行',
    hint: '把历史截图作为 UI 路径、页面状态和缺陷定位参考。',
    planFocus: '复用截图证据，优先验证页面路径、关键状态和视觉可见问题。',
    icon: Target,
    tone: 'border-indigo-100 bg-indigo-50 text-indigo-700',
  },
  {
    key: 'reproduction',
    label: '可复现步骤',
    count: numericValue(evidenceSummary.value.runs_with_reproduction),
    unit: '次运行',
    hint: '用历史复现路径指导下一次任务的检查顺序。',
    planFocus: '复用可复现步骤，优先回归历史缺陷和阻塞路径。',
    icon: Activity,
    tone: 'border-emerald-100 bg-emerald-50 text-emerald-700',
  },
  {
    key: 'scripts',
    label: '复现脚本',
    count: numericValue(evidenceSummary.value.runs_with_scripts),
    unit: '次运行',
    hint: '将已有脚本作为可执行资产，减少重新探索成本。',
    planFocus: '复用复现脚本，优先检查脚本覆盖范围并补齐缺口。',
    icon: CheckCircle2,
    tone: 'border-amber-100 bg-amber-50 text-amber-700',
  },
])

function numericValue(value: unknown) {
  const numeric = Number(value || 0)
  return Number.isFinite(numeric) ? numeric : 0
}

function clampPercent(value: unknown) {
  return Math.max(0, Math.min(100, numericValue(value)))
}

function formatNumber(value: unknown) {
  const numeric = numericValue(value)
  return Number.isInteger(numeric) ? String(numeric) : numeric.toFixed(1)
}

function countValue(value: unknown) {
  return numericValue(value)
}

function barWidth(percent: unknown) {
  return `${clampPercent(percent)}%`
}

function limitText(value: string, maxLength = 900) {
  const text = value.trim()
  if (text.length <= maxLength) return text
  return `${text.slice(0, maxLength - 3).trim()}...`
}

function isSensitiveParam(key: string) {
  return /(^|[_-])(password|passwd|pwd|token|secret|api[_-]?key|authorization|auth|cookie|session|captcha|mfa|otp|csrf|xsrf|jwt)([_-]|$)/i.test(key)
}

function redactUrl(value: string) {
  try {
    const url = new URL(value)
    if (url.username) url.username = REDACTED_VALUE
    if (url.password) url.password = REDACTED_VALUE
    url.searchParams.forEach((_paramValue, key) => {
      if (isSensitiveParam(key)) {
        url.searchParams.set(key, REDACTED_VALUE)
      }
    })
    return url.toString()
  } catch (_err) {
    return value
  }
}

function redactSensitiveText(value: unknown) {
  let text = String(value ?? '')
  text = text.replace(/https?:\/\/[^\s,，。)]+/gi, (url) => redactUrl(url))
  text = text.replace(/\b(Bearer|Basic)\s+[A-Za-z0-9._~+/-]+=*/gi, `$1 ${REDACTED_VALUE}`)
  text = text.replace(
    /\b(password|passwd|pwd|token|secret|api[_-]?key|authorization|cookie|session|captcha|mfa|otp|csrf|xsrf|jwt)\s*[:=]\s*[^,\n;，。)]+/gi,
    (_match, key) => `${key}=${REDACTED_VALUE}`,
  )
  return limitText(text.replace(/\n{3,}/g, '\n\n'))
}

function safeTargetLabel(value: string) {
  return redactSensitiveText(value || '未记录目标')
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

function formatTime(value: string | null | undefined) {
  if (!value) return '暂无记录'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '暂无记录'
  return date.toLocaleString('zh-CN')
}

function recentIssueSummary(memory?: AffectedTarget | null) {
  const surfacedThemes = highFrequencyThemes.value.filter((theme) => {
    if (!memory) return true
    const target = memory.target.toLowerCase()
    return (theme.surfaces || []).some((surface) => surface.toLowerCase().includes(target))
  })
  const themes = (surfacedThemes.length ? surfacedThemes : highFrequencyThemes.value)
    .map((theme) => theme.theme)
    .filter(Boolean)
    .slice(0, 2)
  return themes.length ? themes.join('、') : '暂无高频失败主题'
}

function recommendedStrategy(memory?: AffectedTarget | null) {
  const themeAction = highFrequencyThemes.value[0]?.recommended_action
  if (themeAction) return themeAction
  if (memory && memory.issue_run_count > 0) return '先回归历史阻塞点，再补充只读接口和关键 UI 路径。'
  return nextActions.value[0] || '先运行鉴权预检，再执行安全只读的核心路径回归。'
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

function buildMemoryPlanContext(memory?: AffectedTarget | null, extraFocus = '') {
  const safeTarget = memory ? safeTargetLabel(memory.target) : '从质量记忆选择目标'
  const lines = [
    '从 TestClaw 质量记忆创建新测试计划。',
    `目标：${safeTarget}`,
    memory ? `历史运行：${memory.run_count} 次；问题运行：${memory.issue_run_count} 次；最近记录：${formatTime(memory.last_seen)}` : '',
    `高频主题：${recentIssueSummary(memory)}`,
    `可复用资产：API 证据 ${numericValue(evidenceSummary.value.runs_with_api_evidence)}，复现步骤 ${numericValue(evidenceSummary.value.runs_with_reproduction)}，复现脚本 ${numericValue(evidenceSummary.value.runs_with_scripts)}。`,
    `推荐策略：${recommendedStrategy(memory)}`,
    extraFocus,
    '安全边界：默认只读；不要复用历史凭证、Token、Cookie、会话或验证码值。',
  ].filter(Boolean)

  return redactSensitiveText(lines.join('\n'))
}

function routeToAgentPlan(context: string, target = '') {
  const safeTarget = safeTargetLabel(target)
  router.push({
    path: '/agent-plan',
    query: {
      from: 'quality-memory',
      target: safeTarget,
      context: redactSensitiveText(context),
    },
  })
}

function useMemoryForNewPlan(memory?: AffectedTarget | null) {
  routeToAgentPlan(buildMemoryPlanContext(memory), memory?.target || '')
}

function useThemeForNewPlan(theme: RecurringTheme) {
  const context = buildMemoryPlanContext(selectedMemory.value, `本次优先回归高频主题：${theme.theme}；建议动作：${theme.recommended_action || '复查相关历史证据并补齐断言。'}`)
  routeToAgentPlan(context, selectedMemory.value?.target || '')
}

function useAssetForNewPlan(asset: ReusableAsset) {
  const context = buildMemoryPlanContext(selectedMemory.value || targetMemories.value[0], asset.planFocus)
  routeToAgentPlan(context, selectedMemory.value?.target || targetMemories.value[0]?.target || '')
}

function viewMemory(memory: AffectedTarget) {
  selectedMemoryTarget.value = memory.target
}

function closeMemory() {
  selectedMemoryTarget.value = ''
}

async function fetchInsights() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await api.get<RunHistoryInsights>('/runs/insights', { params: { days: 30, limit: 100 } })
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
  <div class="flex min-h-[calc(100vh-7.25rem)] flex-col gap-4 pb-4">
    <section class="rounded-lg border border-blue-100 bg-white p-4 shadow-sm">
      <div class="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div class="max-w-3xl">
          <div class="tc-page-kicker">Quality Memory</div>
          <h2 class="mt-1 text-2xl font-semibold tracking-tight text-gray-950">质量记忆</h2>
          <p class="mt-2 text-sm leading-6 text-gray-500">
            把历史运行沉淀为目标记忆、失败主题和可复用资产，让下一次智能计划直接继承已验证的上下文。
          </p>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <TcButton
            variant="secondary"
            size="sm"
            :loading="loading && hasLoaded"
            @click="fetchInsights"
          >
            <template #leading>
              <RefreshCcw :size="14" />
            </template>
            刷新
          </TcButton>
          <TcButton
            size="sm"
            :disabled="!hasAnyMemory"
            @click="useMemoryForNewPlan(targetMemories[0])"
          >
            <template #leading>
              <Sparkles :size="14" />
            </template>
            一键用于新计划
          </TcButton>
        </div>
      </div>
    </section>

    <div
      v-if="loading && !hasLoaded"
      class="flex items-center gap-3 rounded-lg border border-gray-200 bg-white p-4 text-sm text-gray-500 shadow-sm"
    >
      <div class="h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-gray-200 border-t-gray-900"></div>
      <span>正在加载质量记忆...</span>
    </div>

    <div
      v-else-if="error && !insights"
      class="rounded-lg border border-amber-100 bg-amber-50 p-4 text-sm text-amber-700 shadow-sm"
    >
      <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <span>质量记忆暂不可用，运行历史可继续查看。</span>
        <TcButton variant="secondary" size="sm" @click="fetchInsights">重新加载</TcButton>
      </div>
    </div>

    <template v-else>
      <div class="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
        <div
          v-for="card in memoryStatCards"
          :key="card.label"
          class="rounded-lg border bg-white p-4 shadow-sm"
          :class="card.tone"
        >
          <div class="flex items-start justify-between gap-4">
            <div>
              <p class="text-xs font-bold text-gray-500">{{ card.label }}</p>
              <div class="mt-2 text-2xl font-semibold text-gray-950">{{ card.value }}</div>
              <p class="mt-1 text-xs text-gray-500">{{ card.hint }}</p>
            </div>
            <div class="rounded-lg bg-white/70 p-2.5">
              <component :is="card.icon" :size="18" />
            </div>
          </div>
        </div>
      </div>

      <div
        v-if="!hasAnyMemory"
        class="rounded-lg border border-gray-200 bg-white p-8 text-center shadow-sm"
      >
        <BrainCircuit :size="28" class="mx-auto text-gray-300" />
        <h3 class="mt-3 text-sm font-semibold text-gray-900">暂无质量记忆</h3>
        <p class="mt-1 text-sm text-gray-500">完成一次运行后，这里会显示目标记忆、高频主题和可复用资产。</p>
      </div>

      <template v-else>
        <div class="grid min-h-0 flex-1 grid-cols-1 items-stretch gap-4 xl:grid-cols-[minmax(0,1.7fr)_minmax(340px,0.9fr)]">
          <section class="flex min-h-[420px] flex-col rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
            <div class="mb-5 flex flex-wrap items-start justify-between gap-3">
              <div>
                <h3 class="font-semibold text-gray-900">目标记忆列表</h3>
                <p class="mt-1 text-xs text-gray-500">按问题运行频次和最近出现时间排序，供 Agent 在新计划中复用。</p>
              </div>
              <span class="rounded-full border border-blue-100 bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-700">
                {{ targetMemories.length }} 个目标
              </span>
            </div>

            <div v-if="targetMemories.length" class="grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-y-auto pr-1 lg:grid-cols-2 xl:max-h-[calc(100vh-25rem)]">
              <article
                v-for="memory in targetMemories"
                :key="memory.target"
                data-testid="quality-memory-target-card"
                class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm transition hover:border-blue-200 hover:shadow-md"
              >
                <div class="flex items-start justify-between gap-3">
                  <div class="min-w-0">
                    <div class="truncate text-sm font-semibold text-gray-950">{{ safeTargetLabel(memory.target) }}</div>
                    <p class="mt-1 text-xs text-gray-500">
                      历史运行：{{ memory.run_count }} 次 / 问题运行：{{ memory.issue_run_count }} 次
                    </p>
                  </div>
                  <span class="rounded-full bg-rose-50 px-2 py-1 text-[11px] font-bold text-rose-700">
                    {{ memory.failed_count + memory.bug_count }} 个阻塞
                  </span>
                </div>

                <div class="mt-4 space-y-2 text-xs text-gray-600">
                  <p class="line-clamp-2">
                    <span class="font-semibold text-gray-800">最近问题：</span>{{ recentIssueSummary(memory) }}
                  </p>
                  <p>
                    <span class="font-semibold text-gray-800">可复用资产：</span>{{ reusableAssetCount }} 条
                  </p>
                  <p class="line-clamp-2">
                    <span class="font-semibold text-gray-800">建议策略：</span>{{ recommendedStrategy(memory) }}
                  </p>
                  <p class="text-[11px] text-gray-400">最近记录：{{ formatTime(memory.last_seen) }}</p>
                </div>

                <div class="mt-4 flex flex-wrap gap-2">
                  <TcButton variant="secondary" size="sm" @click="viewMemory(memory)">查看记忆</TcButton>
                  <TcButton size="sm" @click="useMemoryForNewPlan(memory)">
                    <template #leading>
                      <ArrowRight :size="14" />
                    </template>
                    用于新计划
                  </TcButton>
                </div>
              </article>
            </div>
            <p v-else class="rounded-lg border border-dashed border-gray-200 py-8 text-center text-sm text-gray-400">
              暂无目标记忆列表，后续运行会自动沉淀。
            </p>
          </section>

          <aside class="grid min-h-0 grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-1 xl:overflow-y-auto xl:pr-1">
            <section class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
              <div class="mb-4 flex items-center justify-between gap-3">
                <div>
                  <h3 class="font-semibold text-gray-900">质量趋势</h3>
                  <p class="mt-1 text-xs text-gray-500">{{ trend.rationale || '暂无足够样本判断趋势。' }}</p>
                </div>
                <div class="inline-flex shrink-0 items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-bold" :class="trendToneClass(trend.direction)">
                  <TrendingUp :size="14" />
                  <span>{{ trend.label || '样本不足' }}</span>
                </div>
              </div>

              <div v-if="trendBuckets.length" class="h-72 min-h-56 w-full">
                <div ref="trendChartEl" class="h-full w-full"></div>
              </div>
              <div v-else class="flex h-36 items-center justify-center text-sm text-gray-400">暂无趋势数据</div>
            </section>

            <section class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
              <div class="mb-4 flex items-center justify-between gap-3">
                <div>
                  <h3 class="font-semibold text-gray-900">运行结果分布</h3>
                  <p class="mt-1 text-xs text-gray-500">最近 {{ insights?.window_days || 30 }} 天已分析 {{ statusTotal }} 次运行。</p>
                </div>
                <span class="rounded-full border border-gray-200 bg-gray-50 px-3 py-1 text-xs font-semibold text-gray-600">
                  通过率 {{ formatNumber(statusCounts.pass_rate) }}%
                </span>
              </div>
              <div class="space-y-3">
                <div v-for="item in statusBreakdownItems" :key="item.label">
                  <div class="mb-1 flex items-center justify-between text-xs">
                    <span class="font-semibold text-gray-700">{{ item.label }}</span>
                    <span class="font-bold" :class="item.textClass">{{ item.value }} / {{ formatNumber(item.percent) }}%</span>
                  </div>
                  <div class="h-2 overflow-hidden rounded-full bg-gray-100">
                    <div class="h-full rounded-full" :class="item.barClass" :style="{ width: barWidth(item.percent) }"></div>
                  </div>
                </div>
              </div>
            </section>

            <section class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
              <div class="mb-4 flex items-center justify-between gap-3">
                <div>
                  <h3 class="font-semibold text-gray-900">证据复用覆盖</h3>
                  <p class="mt-1 text-xs text-gray-500">检查历史运行是否沉淀了下一次可复用证据。</p>
                </div>
                <span class="rounded-full border border-emerald-100 bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-700">
                  {{ formatNumber(reuseRate) }}%
                </span>
              </div>
              <div class="space-y-3">
                <div v-for="item in evidenceCoverageItems" :key="item.label">
                  <div class="mb-1 flex items-center justify-between text-xs">
                    <span class="font-semibold text-gray-700">{{ item.label }}</span>
                    <span class="font-bold" :class="item.textClass">{{ item.value }} / {{ formatNumber(item.percent) }}%</span>
                  </div>
                  <div class="h-2 overflow-hidden rounded-full bg-gray-100">
                    <div class="h-full rounded-full" :class="item.barClass" :style="{ width: barWidth(item.percent) }"></div>
                  </div>
                </div>
              </div>
            </section>

            <section class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
              <div class="mb-4 flex items-center justify-between gap-3">
                <div>
                  <h3 class="font-semibold text-gray-900">风险优先级</h3>
                  <p class="mt-1 text-xs text-gray-500">按重复出现次数选择下次计划重点。</p>
                </div>
                <AlertTriangle :size="16" class="text-gray-400" />
              </div>
              <div v-if="topRiskThemes.length" class="space-y-3">
                <div v-for="themeItem in topRiskThemes" :key="themeItem.theme" class="border-b border-gray-100 pb-3 last:border-b-0 last:pb-0">
                  <div class="flex items-start justify-between gap-3">
                    <p class="line-clamp-2 text-sm font-semibold text-gray-900">{{ themeItem.theme }}</p>
                    <span class="shrink-0 rounded px-2 py-0.5 text-[10px] font-bold" :class="severityClass(themeItem.severity)">
                      {{ themeItem.severity }}
                    </span>
                  </div>
                  <p class="mt-1 text-xs text-gray-500">{{ themeItem.count }} 次出现 / {{ themeItem.category }}</p>
                </div>
              </div>
              <p v-else class="py-6 text-center text-sm text-gray-400">暂无风险优先级</p>
            </section>
          </aside>
        </div>

        <div class="grid grid-cols-1 items-stretch gap-4 lg:grid-cols-[minmax(0,1.5fr)_minmax(300px,0.8fr)]">
          <section class="flex min-h-[320px] flex-col rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
            <div class="mb-4 flex flex-wrap items-start justify-between gap-3">
              <div>
                <h3 class="font-semibold text-gray-900">高频主题</h3>
                <p class="mt-1 text-xs text-gray-500">来自多次失败或缺陷运行的归并主题，用于下一次回归重点。</p>
              </div>
              <span class="rounded-full border border-rose-100 bg-rose-50 px-3 py-1 text-xs font-semibold text-rose-700">
                {{ blockerOccurrenceCount }} 次出现
              </span>
            </div>

            <div v-if="highFrequencyThemes.length" class="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
              <article
                v-for="themeItem in highFrequencyThemes"
                :key="themeItem.theme"
                data-testid="quality-memory-theme"
                class="rounded-lg border border-gray-200 p-3"
              >
                <div class="flex items-start justify-between gap-3">
                  <div class="min-w-0">
                    <div class="line-clamp-2 text-sm font-semibold text-gray-950">{{ themeItem.theme }}</div>
                    <p class="mt-1 text-xs text-gray-500">{{ themeItem.count }} 次出现 / {{ themeItem.category }}</p>
                  </div>
                  <span class="shrink-0 rounded px-2 py-0.5 text-[10px] font-bold" :class="severityClass(themeItem.severity)">
                    {{ themeItem.severity }}
                  </span>
                </div>
                <div v-if="themeItem.surfaces?.length" class="mt-2 truncate text-[11px] text-gray-400">
                  {{ themeItem.surfaces.join('，') }}
                </div>
                <div class="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                  <p class="text-xs text-gray-600">{{ themeItem.recommended_action || '建议优先补充断言并回归历史失败路径。' }}</p>
                  <TcButton variant="secondary" size="sm" @click="useThemeForNewPlan(themeItem)">用于新计划</TcButton>
                </div>
              </article>
            </div>
            <p v-else class="rounded-lg border border-dashed border-gray-200 py-8 text-center text-sm text-gray-400">
              暂无高频主题
            </p>
          </section>

          <section class="flex min-h-[320px] flex-col rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
            <div class="mb-4 flex items-center justify-between">
              <div>
                <h3 class="font-semibold text-gray-900">已知阻塞点</h3>
                <p class="mt-1 text-xs text-gray-500">影响接口、页面或目标区域的记忆片段。</p>
              </div>
              <AlertTriangle :size="16" class="text-gray-400" />
            </div>
            <div v-if="affectedSurfaces.length" class="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
              <div
                v-for="surface in affectedSurfaces"
                :key="`${surface.type}-${surface.name}`"
                class="border-b border-gray-100 pb-3 last:border-b-0 last:pb-0"
              >
                <div class="flex min-w-0 items-center gap-2">
                  <span class="rounded bg-gray-100 px-2 py-0.5 text-[10px] font-bold text-gray-500">{{ surface.type }}</span>
                  <span class="truncate text-sm font-medium text-gray-900">{{ surface.name }}</span>
                </div>
                <div class="mt-1 text-xs text-gray-500">{{ surface.issue_count }} 次关联问题</div>
                <p v-if="surface.detail" class="mt-1 line-clamp-2 text-[11px] text-gray-400">{{ surface.detail }}</p>
              </div>
            </div>
            <p v-else class="py-6 text-center text-sm text-gray-400">暂无已知阻塞点</p>
          </section>
        </div>

        <section class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <div class="mb-4 flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 class="font-semibold text-gray-900">可复用资产</h3>
              <p class="mt-1 text-xs text-gray-500">复用证据、步骤和脚本，减少 Agent 重新探索成本。</p>
            </div>
            <span class="rounded-full border border-emerald-100 bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-700">
              {{ reusableAssetCount }} 条资产线索
            </span>
          </div>

          <div class="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
            <article
              v-for="asset in reusableAssets"
              :key="asset.key"
              data-testid="quality-memory-asset"
              class="rounded-lg border p-4"
              :class="asset.tone"
            >
              <div class="flex items-start justify-between gap-3">
                <div>
                  <p class="text-sm font-semibold text-gray-950">{{ asset.label }}</p>
                  <div class="mt-2 text-2xl font-semibold text-gray-950">{{ asset.count }}</div>
                  <p class="mt-1 text-xs text-gray-500">{{ asset.unit }}</p>
                </div>
                <div class="rounded-lg bg-white/70 p-2.5">
                  <component :is="asset.icon" :size="18" />
                </div>
              </div>
              <p class="mt-3 min-h-10 text-xs leading-5 text-gray-600">{{ asset.hint }}</p>
              <TcButton
                class="mt-4"
                variant="secondary"
                size="sm"
                block
                :disabled="asset.count <= 0"
                @click="useAssetForNewPlan(asset)"
              >
                用于新计划
              </TcButton>
            </article>
          </div>
        </section>

        <section class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <div class="mb-4 flex items-center justify-between">
            <div>
              <h3 class="font-semibold text-gray-900">推荐下次策略</h3>
              <p class="mt-1 text-xs text-gray-500">只显示结构化摘要，避免暴露原始日志和敏感上下文。</p>
            </div>
            <BrainCircuit :size="16" class="text-gray-400" />
          </div>
          <div v-if="nextActions.length" class="grid grid-cols-1 gap-3 md:grid-cols-2">
            <div
              v-for="action in nextActions"
              :key="action"
              class="flex gap-2 rounded-lg border border-blue-100 bg-blue-50/60 p-3 text-xs leading-relaxed text-blue-800"
            >
              <Activity :size="14" class="mt-0.5 shrink-0" />
              <span>{{ action }}</span>
            </div>
          </div>
          <p v-else class="rounded-lg border border-dashed border-gray-200 py-6 text-center text-sm text-gray-400">
            暂无推荐策略
          </p>
        </section>
      </template>
    </template>

    <div
      v-if="selectedMemory"
      class="fixed inset-0 z-40 flex justify-end bg-gray-950/25 px-3 py-3 sm:px-6"
      @click.self="closeMemory"
    >
      <aside class="flex h-full w-full max-w-xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
        <div class="flex items-start justify-between gap-3 border-b border-gray-100 p-5">
          <div class="min-w-0">
            <div class="tc-page-kicker">Memory Detail</div>
            <h3 class="mt-1 truncate text-lg font-semibold text-gray-950">{{ safeTargetLabel(selectedMemory.target) }}</h3>
            <p class="mt-1 text-xs text-gray-500">传入计划的上下文会先在前端脱敏。</p>
          </div>
          <button
            type="button"
            aria-label="关闭记忆详情"
            class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-gray-200 text-gray-500 hover:bg-gray-50 hover:text-gray-900"
            @click="closeMemory"
          >
            <X :size="16" />
          </button>
        </div>

        <div class="flex-1 space-y-5 overflow-y-auto p-5">
          <section>
            <h4 class="text-sm font-semibold text-gray-900">高频失败主题</h4>
            <div v-if="highFrequencyThemes.length" class="mt-3 space-y-2">
              <div v-for="themeItem in highFrequencyThemes" :key="themeItem.theme" class="rounded-lg border border-gray-200 p-3">
                <div class="flex items-start justify-between gap-3">
                  <p class="line-clamp-2 text-sm font-medium text-gray-900">{{ themeItem.theme }}</p>
                  <span class="shrink-0 rounded px-2 py-0.5 text-[10px] font-bold" :class="severityClass(themeItem.severity)">
                    {{ themeItem.severity }}
                  </span>
                </div>
                <p class="mt-1 text-xs text-gray-500">{{ themeItem.count }} 次出现</p>
              </div>
            </div>
            <p v-else class="mt-3 text-sm text-gray-400">暂无高频失败主题</p>
          </section>

          <section>
            <h4 class="text-sm font-semibold text-gray-900">已沉淀资产</h4>
            <div class="mt-3 grid grid-cols-2 gap-2">
              <div v-for="asset in reusableAssets" :key="asset.key" class="rounded-lg border border-gray-200 p-3">
                <p class="text-xs font-semibold text-gray-600">{{ asset.label }}</p>
                <p class="mt-1 text-xl font-semibold text-gray-950">{{ asset.count }}</p>
              </div>
            </div>
          </section>

          <section>
            <h4 class="text-sm font-semibold text-gray-900">已知阻塞点</h4>
            <div v-if="affectedSurfaces.length" class="mt-3 space-y-3">
              <div v-for="surface in affectedSurfaces.slice(0, 4)" :key="`${surface.type}-${surface.name}`" class="rounded-lg border border-gray-200 p-3">
                <div class="flex items-center gap-2">
                  <span class="rounded bg-gray-100 px-2 py-0.5 text-[10px] font-bold text-gray-500">{{ surface.type }}</span>
                  <span class="truncate text-sm font-medium text-gray-900">{{ surface.name }}</span>
                </div>
                <p class="mt-1 text-xs text-gray-500">{{ surface.issue_count }} 次关联问题</p>
              </div>
            </div>
            <p v-else class="mt-3 text-sm text-gray-400">暂无已知阻塞点</p>
          </section>

          <section class="rounded-lg border border-blue-100 bg-blue-50 p-4">
            <h4 class="text-sm font-semibold text-blue-950">推荐下次策略</h4>
            <p class="mt-2 text-sm leading-6 text-blue-800">{{ recommendedStrategy(selectedMemory) }}</p>
          </section>

          <section class="grid grid-cols-3 gap-2">
            <div class="rounded-lg border border-gray-200 p-3">
              <p class="text-xs text-gray-500">相关历史运行</p>
              <p class="mt-1 text-lg font-semibold text-gray-950">{{ selectedMemory.run_count }}</p>
            </div>
            <div class="rounded-lg border border-gray-200 p-3">
              <p class="text-xs text-gray-500">问题运行</p>
              <p class="mt-1 text-lg font-semibold text-gray-950">{{ selectedMemory.issue_run_count }}</p>
            </div>
            <div class="rounded-lg border border-gray-200 p-3">
              <p class="text-xs text-gray-500">最近记录</p>
              <p class="mt-1 truncate text-sm font-semibold text-gray-950">{{ formatTime(selectedMemory.last_seen) }}</p>
            </div>
          </section>
        </div>

        <div class="border-t border-gray-100 p-5">
          <TcButton block @click="useMemoryForNewPlan(selectedMemory)">
            <template #leading>
              <Sparkles :size="15" />
            </template>
            用于新计划
          </TcButton>
        </div>
      </aside>
    </div>
  </div>
</template>
