<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import type { Component } from 'vue'
import { useRouter } from 'vue-router'
import api from '../lib/api'
import StatusBadge from '../components/StatusBadge.vue'
import LoadingSpinner from '../components/LoadingSpinner.vue'
import EmptyState from '../components/EmptyState.vue'
import Pagination from '../components/Pagination.vue'
import StyledSelect from '../components/StyledSelect.vue'
import SearchInput from '../components/SearchInput.vue'
import TcButton from '../components/ui/TcButton.vue'
import { useToast } from '../composables/useToast'
import { BarChart3, Bug, Clock3, Download, Eye, Filter, RotateCcw, ShieldCheck, Target, Trash2 } from 'lucide-vue-next'

type RunItem = {
  id: string
  objective?: string | null
  target_url?: string | null
  status?: string | null
  test_type?: string | null
  created_at?: string | null
  updated_at?: string | null
  error_message?: string | null
  issue_count?: number | string | null
  finding_count?: number | string | null
  bug_count?: number | string | null
  evidence_count?: number | string | null
  triage_summary?: {
    blocking_count?: number | string | null
    release_risk?: { label?: string | null; level?: string | null } | null
    evidence?: { count?: number | string | null } | null
  } | null
  [key: string]: unknown
}

type RunListResponse = RunItem[] | { items?: RunItem[] }

type StatCard = {
  label: string
  value: string
  hint: string
  icon: Component
  classes: string
}

type RunSnippet = {
  label: string
  value: string
  tone: string
}

const router = useRouter()
const toast = useToast()
const runs = ref<RunItem[]>([])
const loading = ref(false)
const hasLoadedRuns = ref(false)
const filterStatus = ref('')
const filterType = ref('')
const filterWindow = ref('')
const customStartDate = ref('')
const customEndDate = ref('')
const searchTerm = ref('')
const page = ref(1)
const pageSize = ref(5)
const pageSizeOptions = [5, 10, 15, 20]
const total = ref(0)
const rerunningId = ref<string | null>(null)
const exportingId = ref<string | null>(null)

function formatTime(value: string | null | undefined) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('zh-CN')
}

function formatQueryDateTime(date: Date) {
  return date.toISOString()
}

function dateFromInput(value: string, endOfDay = false) {
  if (!value) return null
  const date = new Date(`${value}T${endOfDay ? '23:59:59' : '00:00:00'}`)
  return Number.isNaN(date.getTime()) ? null : date
}

function historyDateRangeParams() {
  const now = new Date()
  const params: Record<string, string> = {}

  if (filterWindow.value === 'today') {
    const start = new Date(now.getFullYear(), now.getMonth(), now.getDate())
    params.created_after = formatQueryDateTime(start)
  } else if (filterWindow.value === '7d' || filterWindow.value === '30d') {
    const days = filterWindow.value === '7d' ? 7 : 30
    const start = new Date(now.getTime() - (days * 24 * 60 * 60 * 1000))
    params.created_after = formatQueryDateTime(start)
  } else if (filterWindow.value === 'custom') {
    const start = dateFromInput(customStartDate.value)
    const end = dateFromInput(customEndDate.value, true)
    if (start) params.created_after = formatQueryDateTime(start)
    if (end) params.created_before = formatQueryDateTime(end)
  }

  return params
}

function errorMessage(err: unknown, fallback: string) {
  const response = (err as { response?: { data?: { detail?: unknown } } } | null)?.response
  return typeof response?.data?.detail === 'string' ? response.data.detail : fallback
}

function statusValue(run: RunItem) {
  return String(run.status || '').toLowerCase()
}

function numberValue(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value)) return value
    if (typeof value === 'string' && value.trim()) {
      const parsed = Number.parseFloat(value)
      if (Number.isFinite(parsed)) return parsed
    }
  }
  return null
}

function durationMs(run: RunItem) {
  if (!run.created_at || !run.updated_at) return null
  const start = new Date(run.created_at).getTime()
  const end = new Date(run.updated_at).getTime()
  if (Number.isNaN(start) || Number.isNaN(end) || end <= start) return null
  return end - start
}

function formatDuration(ms: number | null) {
  if (!ms) return ''
  const seconds = Math.max(1, Math.round(ms / 1000))
  if (seconds < 60) return `${seconds} 秒`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) {
    const remaining = seconds % 60
    return remaining ? `${minutes} 分 ${remaining} 秒` : `${minutes} 分`
  }
  const hours = seconds / 3600
  return `${hours.toFixed(hours >= 10 ? 0 : 1)} 小时`
}

function issueCount(run: RunItem) {
  const explicit = numberValue(
    run.issue_count,
    run.finding_count,
    run.bug_count,
    run.triage_summary?.blocking_count,
  )
  if (explicit !== null) return explicit
  return ['failed', 'bug_found'].includes(statusValue(run)) ? 1 : 0
}

function evidenceCount(run: RunItem) {
  return numberValue(run.evidence_count, run.triage_summary?.evidence?.count)
}

function runModeLabel(run: RunItem) {
  const mode = String(run.test_type || '').toLowerCase()
  const labels: Record<string, string> = {
    auto: '自动',
    api: 'API',
    ui: 'UI',
    full: '全量',
    functional: '功能',
    suite: '套件',
  }
  return labels[mode] || String(run.test_type || '未知').toUpperCase()
}

function runTitle(run: RunItem) {
  return run.objective?.trim() || '未命名测试运行'
}

function runTarget(run: RunItem) {
  return run.target_url?.trim() || '未记录目标'
}

function riskLabel(run: RunItem) {
  return run.triage_summary?.release_risk?.label || run.triage_summary?.release_risk?.level || ''
}

function runSnippets(run: RunItem): RunSnippet[] {
  const snippets: RunSnippet[] = []
  const issues = issueCount(run)
  const evidence = evidenceCount(run)
  const risk = riskLabel(run)
  const duration = formatDuration(durationMs(run))

  if (issues > 0) snippets.push({ label: '发现问题', value: `${issues} 个`, tone: 'border-red-100 bg-red-50 text-red-700' })
  if (risk) snippets.push({ label: '发布风险', value: risk, tone: 'border-amber-100 bg-amber-50 text-amber-700' })
  if (evidence !== null) snippets.push({ label: '证据', value: `${evidence} 条`, tone: 'border-blue-100 bg-blue-50 text-blue-700' })
  if (duration) snippets.push({ label: '耗时', value: duration, tone: 'border-slate-200 bg-slate-50 text-slate-600' })
  if (run.error_message) snippets.push({ label: '错误', value: run.error_message, tone: 'border-red-100 bg-red-50 text-red-700' })

  return snippets.slice(0, 4)
}

const visibleRuns = computed(() => {
  const keyword = searchTerm.value.trim().toLowerCase()
  if (!keyword) return runs.value
  return runs.value.filter((run) => {
    return [
      run.objective,
      run.target_url,
      run.id,
      run.test_type,
      run.status,
      run.error_message,
      riskLabel(run),
    ].some((value) => {
      return String(value || '').toLowerCase().includes(keyword)
    })
  })
})

const loadedRunCount = computed(() => runs.value.length)
const successRate = computed(() => {
  if (!loadedRunCount.value) return 0
  const succeeded = runs.value.filter((run) => statusValue(run) === 'succeeded').length
  return Math.round((succeeded / loadedRunCount.value) * 100)
})
const totalIssueCount = computed(() => runs.value.reduce((sum, run) => sum + issueCount(run), 0))
const averageDuration = computed(() => {
  const samples = runs.value.map(durationMs).filter((value): value is number => Boolean(value))
  if (!samples.length) return null
  return Math.round(samples.reduce((sum, value) => sum + value, 0) / samples.length)
})
const evidenceCompleteness = computed(() => {
  if (!loadedRunCount.value) return 0
  const withEvidence = runs.value.filter((run) => {
    const count = evidenceCount(run)
    return count !== null && count > 0
  }).length
  return Math.round((withEvidence / loadedRunCount.value) * 100)
})

const statCards = computed<StatCard[]>(() => {
  return [
    {
      label: '总运行次数',
      value: String(total.value || loadedRunCount.value),
      hint: `${loadedRunCount.value} 条已加载`,
      icon: BarChart3,
      classes: 'border-slate-200 bg-white text-slate-700',
    },
    {
      label: '成功率',
      value: loadedRunCount.value ? `${successRate.value}%` : '--',
      hint: '基于当前加载运行',
      icon: ShieldCheck,
      classes: 'border-emerald-100 bg-emerald-50/70 text-emerald-700',
    },
    {
      label: '发现问题数',
      value: String(totalIssueCount.value),
      hint: '失败 / 缺陷与报告发现',
      icon: Bug,
      classes: 'border-red-100 bg-red-50/70 text-red-700',
    },
    {
      label: '平均耗时',
      value: averageDuration.value ? formatDuration(averageDuration.value) : '--',
      hint: '基于当前加载运行',
      icon: Clock3,
      classes: 'border-amber-100 bg-amber-50/70 text-amber-700',
    },
    {
      label: '证据完整率',
      value: loadedRunCount.value ? `${evidenceCompleteness.value}%` : '--',
      hint: '含报告、截图或工具证据',
      icon: ShieldCheck,
      classes: 'border-blue-100 bg-blue-50/70 text-blue-700',
    },
  ]
})

async function fetchRuns() {
  loading.value = true
  try {
    const params: Record<string, string | number> = { page: page.value, page_size: pageSize.value }
    if (filterStatus.value) params.status = filterStatus.value
    if (filterType.value) params.test_type = filterType.value
    if (searchTerm.value.trim()) params.search = searchTerm.value.trim()
    Object.assign(params, historyDateRangeParams())
    const { data, headers } = await api.get('/runs', { params })
    const payload = data as RunListResponse
    runs.value = Array.isArray(payload) ? payload : payload.items || []
    const headerTotal = Number.parseInt(String(headers?.['x-total-count'] || headers?.['X-Total-Count'] || ''), 10)
    total.value = Number.isNaN(headerTotal) ? runs.value.length : headerTotal
  } catch (err: unknown) {
    toast.error(errorMessage(err, '加载历史记录失败'))
  } finally {
    loading.value = false
    hasLoadedRuns.value = true
  }
}

function resetAndFetchRuns() {
  page.value = 1
  fetchRuns()
}

function changePage(nextPage: number) {
  page.value = nextPage
  fetchRuns()
}

function viewRunDetails(run: RunItem) {
  router.push(`/runs/${run.id}`)
}

async function rerunRun(run: RunItem) {
  rerunningId.value = run.id
  try {
    const { data } = await api.post(`/runs/${run.id}/rerun`)
    router.push(`/runs/${data.id}`)
  } catch (err: unknown) {
    toast.error(errorMessage(err, '重新运行失败'))
  } finally {
    rerunningId.value = null
  }
}

async function exportRunReport(run: RunItem) {
  exportingId.value = run.id
  try {
    const { data } = await api.get(`/runs/${run.id}/triage-export`, {
      params: { format: 'markdown' },
      responseType: 'blob',
    })
    const blob = data instanceof Blob ? data : new Blob([data], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `testclaw-report-${run.id.slice(0, 8)}.md`
    anchor.click()
    URL.revokeObjectURL(url)
    toast.success('报告导出已开始')
  } catch (err: unknown) {
    toast.error(errorMessage(err, '导出报告失败'))
  } finally {
    exportingId.value = null
  }
}

async function deleteRun(id: string) {
  if (!confirm('确定删除此运行记录？')) return
  try {
    await api.delete(`/runs/${id}`)
    toast.success('已删除')
    await fetchRuns()
  } catch (err: unknown) {
    toast.error(errorMessage(err, '删除失败'))
  }
}

let pollTimer: ReturnType<typeof setInterval> | null = null

function startPolling() {
  stopPolling()
  pollTimer = setInterval(() => {
    const hasActive = runs.value.some((r: RunItem) => ['queued', 'running'].includes(statusValue(r)))
    if (hasActive) {
      fetchRuns()
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
  startPolling()
})
onUnmounted(() => stopPolling())

watch([searchTerm, filterWindow, customStartDate, customEndDate], () => {
  resetAndFetchRuns()
})
</script>

<template>
  <div class="mx-auto max-w-7xl space-y-5 pb-10">
    <div class="flex flex-col gap-1 border-b border-gray-200/80 pb-5">
      <div class="tc-page-kicker">Runs</div>
      <h2 class="text-xl font-semibold tracking-tight text-gray-950">运行历史</h2>
      <p class="max-w-3xl text-sm leading-6 text-gray-500">按目标、状态和测试类型回看智能体运行，快速进入详情、重新运行或导出报告。</p>
    </div>

    <section class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
      <div
        v-for="card in statCards"
        :key="card.label"
        class="rounded-lg border bg-white p-4 shadow-[0_8px_24px_rgba(15,23,42,0.04)]"
      >
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <p class="text-xs font-semibold text-gray-500">{{ card.label }}</p>
            <p class="mt-2 text-2xl font-bold tracking-tight text-gray-950">{{ card.value }}</p>
          </div>
          <span class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border" :class="card.classes">
            <component :is="card.icon" :size="17" />
          </span>
        </div>
        <p class="mt-3 truncate text-xs text-gray-500">{{ card.hint }}</p>
      </div>
    </section>

    <section class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <div class="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div class="flex min-w-0 items-center gap-2">
          <span class="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 bg-slate-50 text-slate-600">
            <Filter :size="15" />
          </span>
          <div class="min-w-0">
            <h3 class="text-sm font-semibold text-gray-950">过滤器</h3>
            <p class="text-xs text-gray-500">按状态、类型、时间窗口和关键词筛选服务端列表。</p>
          </div>
        </div>
        <span class="whitespace-nowrap rounded-lg bg-gray-50 px-2.5 py-1.5 text-xs font-medium text-gray-500">
          {{ loading ? '正在加载记录...' : `${visibleRuns.length}/${total || runs.length} 条记录` }}
        </span>
      </div>
      <div class="grid min-w-0 grid-cols-1 gap-3 xl:grid-cols-[minmax(220px,1fr)_minmax(132px,168px)_minmax(124px,160px)_minmax(132px,160px)_minmax(104px,132px)]">
        <label class="grid min-w-0 gap-1">
          <span class="whitespace-nowrap text-[10px] font-bold uppercase tracking-widest text-gray-400">搜索</span>
          <SearchInput v-model="searchTerm" placeholder="搜索目标、任务、问题或运行 ID" />
        </label>
        <label class="grid min-w-0 gap-1">
          <span class="whitespace-nowrap text-[10px] font-bold uppercase tracking-widest text-gray-400">状态</span>
          <StyledSelect
            v-model="filterStatus"
            @change="resetAndFetchRuns"
            class="w-full"
            size="sm"
          >
            <option value="">全部状态</option>
            <option value="succeeded">通过</option>
            <option value="failed">失败</option>
            <option value="bug_found">发现缺陷</option>
            <option value="queued">排队中</option>
            <option value="running">运行中</option>
            <option value="cancelled">已取消</option>
          </StyledSelect>
        </label>
        <label class="grid min-w-0 gap-1">
          <span class="whitespace-nowrap text-[10px] font-bold uppercase tracking-widest text-gray-400">类型</span>
          <StyledSelect
            v-model="filterType"
            @change="resetAndFetchRuns"
            class="w-full"
            size="sm"
          >
            <option value="">全部类型</option>
            <option value="auto">自动</option>
            <option value="api">API</option>
            <option value="ui">UI</option>
            <option value="full">Full</option>
            <option value="suite">套件</option>
          </StyledSelect>
        </label>
        <label class="grid min-w-0 gap-1">
          <span class="whitespace-nowrap text-[10px] font-bold uppercase tracking-widest text-gray-400">时间</span>
          <StyledSelect
            v-model="filterWindow"
            class="w-full"
            size="sm"
          >
            <option value="">全部时间</option>
            <option value="today">今天</option>
            <option value="7d">7 天</option>
            <option value="30d">30 天</option>
            <option value="custom">自定义</option>
          </StyledSelect>
        </label>
        <label class="grid min-w-0 gap-1">
          <span class="whitespace-nowrap text-[10px] font-bold uppercase tracking-widest text-gray-400">每页</span>
          <StyledSelect
            v-model.number="pageSize"
            @change="resetAndFetchRuns"
            class="w-full"
            size="sm"
          >
            <option v-for="size in pageSizeOptions" :key="size" :value="size">{{ size }} 条</option>
          </StyledSelect>
        </label>
      </div>
      <div v-if="filterWindow === 'custom'" class="mt-3 grid gap-3 sm:grid-cols-2 lg:max-w-lg">
        <label class="grid min-w-0 gap-1">
          <span class="whitespace-nowrap text-[10px] font-bold uppercase tracking-widest text-gray-400">开始日期</span>
          <input
            v-model="customStartDate"
            type="date"
            class="min-h-8 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs text-gray-700 outline-none transition focus:border-gray-400 focus:ring-2 focus:ring-gray-100"
          >
        </label>
        <label class="grid min-w-0 gap-1">
          <span class="whitespace-nowrap text-[10px] font-bold uppercase tracking-widest text-gray-400">结束日期</span>
          <input
            v-model="customEndDate"
            type="date"
            class="min-h-8 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs text-gray-700 outline-none transition focus:border-gray-400 focus:ring-2 focus:ring-gray-100"
          >
        </label>
      </div>
    </section>

    <LoadingSpinner v-if="loading || !hasLoadedRuns" text="加载运行记录中..." />
    <EmptyState
      v-else-if="!visibleRuns.length"
      :icon="Filter"
      :title="total ? '没有匹配的记录' : '暂无运行记录'"
      :description="total ? '请尝试调整筛选或搜索条件' : '点击任务委派创建第一次运行'"
    />
    <section v-else class="space-y-3" aria-label="运行卡片列表">
      <article
        v-for="run in visibleRuns"
        :key="run.id"
        data-testid="history-run-card"
        @click="viewRunDetails(run)"
        class="group cursor-pointer rounded-lg border border-gray-200 bg-white p-4 shadow-sm transition hover:border-blue-200 hover:shadow-[0_14px_34px_rgba(15,23,42,0.08)]"
      >
        <div class="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div class="min-w-0 flex-1">
            <div class="mb-2 flex flex-wrap items-center gap-2 text-xs">
              <StatusBadge :status="run.status || 'pending'" />
              <span class="rounded-md border border-gray-200 bg-gray-50 px-2 py-0.5 text-[10px] font-bold uppercase text-gray-600">{{ runModeLabel(run) }}</span>
              <span class="inline-flex items-center gap-1 text-[11px] text-gray-400">
                <Clock3 :size="12" /> {{ formatTime(run.created_at) || '时间未知' }}
              </span>
            </div>
            <h3 class="truncate text-base font-semibold text-gray-950">{{ runTitle(run) }}</h3>
            <p class="mt-1 flex min-w-0 items-center gap-1.5 truncate font-mono text-xs text-gray-500">
              <Target :size="13" class="shrink-0 text-gray-400" />
              <span class="truncate">{{ runTarget(run) }}</span>
            </p>
            <div v-if="runSnippets(run).length" class="mt-3 flex flex-wrap gap-2">
              <span
                v-for="snippet in runSnippets(run)"
                :key="`${run.id}-${snippet.label}`"
                class="inline-flex max-w-full items-center gap-1 rounded-lg border px-2.5 py-1 text-xs font-medium"
                :class="snippet.tone"
              >
                <span class="shrink-0 text-[10px] font-bold text-current/70">{{ snippet.label }}</span>
                <span class="truncate">{{ snippet.value }}</span>
              </span>
            </div>
          </div>
          <div class="flex shrink-0 flex-wrap items-center gap-2 border-t border-gray-100 pt-3 lg:border-t-0 lg:pt-0">
            <TcButton size="sm" variant="primary" @click.stop="viewRunDetails(run)">
              <template #leading><Eye :size="14" /></template>
              查看详情
            </TcButton>
            <TcButton
              size="sm"
              variant="secondary"
              :loading="rerunningId === run.id"
              @click.stop="rerunRun(run)"
            >
              <template #leading><RotateCcw :size="14" /></template>
              重新运行
            </TcButton>
            <TcButton
              size="sm"
              variant="secondary"
              :loading="exportingId === run.id"
              @click.stop="exportRunReport(run)"
            >
              <template #leading><Download :size="14" /></template>
              导出报告
            </TcButton>
            <button
              type="button"
              title="删除运行"
              :aria-label="`删除运行 ${run.objective || run.id}`"
              @click.stop="deleteRun(run.id)"
              class="inline-flex min-h-8 w-8 items-center justify-center rounded-lg text-gray-400 transition-colors hover:bg-red-50 hover:text-red-600"
            >
              <Trash2 :size="14" />
            </button>
          </div>
        </div>
      </article>
    </section>

    <Pagination
      :page="page"
      :page-size="pageSize"
      :total="total"
      @update:page="changePage"
    />
  </div>
</template>
