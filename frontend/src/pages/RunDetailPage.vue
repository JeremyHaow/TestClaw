<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../lib/api'
import StatusBadge from '../components/StatusBadge.vue'
import LoadingSpinner from '../components/LoadingSpinner.vue'
import { useToast } from '../composables/useToast'
import { Activity, AlertTriangle, ArrowLeft, Camera, CheckCircle2, ChevronDown, ChevronRight, Clock, FileText, Loader2, Monitor, RotateCcw, Terminal, XCircle, XCircleIcon, Zap } from 'lucide-vue-next'

const expandedApiRow = ref<number | null>(null)
const lightboxUrl = ref<string | null>(null)

function toggleApiRow(idx: number) {
  expandedApiRow.value = expandedApiRow.value === idx ? null : idx
}

function screenshotUrl(runId: string, filename: string) {
  const token = localStorage.getItem('testclaw_token')
  const query = token ? `?token=${encodeURIComponent(token)}` : ''
  return `/api/v1/runs/${runId}/screenshots/${encodeURIComponent(filename)}${query}`
}

function screenshotFilename(path: string) {
  return path.split('/').pop() || path
}

function screenshotPath(value: any) {
  if (!value) return ''
  if (typeof value === 'string') return value
  return value.path || value.screenshot || value.filename || ''
}

function screenshotDisplayUrl(runId: string, shot: any) {
  const url = shot?.url || shot?.storage?.url
  if (url) return url
  return screenshotUrl(runId, screenshotFilename(screenshotPath(shot)))
}

function screenshotIdentity(shot: any) {
  return shot?.content_hash || shot?.storage?.etag || shot?.url || shot?.storage?.url || ''
}

function openLightbox(runId: string, filename: string) {
  lightboxUrl.value = screenshotUrl(runId, filename)
}

function openScreenshot(runId: string, shot: any) {
  const url = shot?.url || shot?.storage?.url
  if (url) {
    lightboxUrl.value = url
    return
  }
  openLightbox(runId, screenshotFilename(screenshotPath(shot)))
}

function closeLightbox() {
  lightboxUrl.value = null
}

const route = useRoute()
const router = useRouter()
const toast = useToast()
const loading = ref(false)
const run = ref<any>(null)
const liveRawLog = ref('')
let eventSource: EventSource | null = null

const terminalStatuses = ['succeeded', 'failed', 'bug_found', 'cancelled']
const activeStatuses = ['queued', 'running']
const snapshotKeys = [
  'execution_result',
  'test_plan',
  'test_cases',
  'workflow_steps',
  'bug_report',
  'api_plan',
  'ui_plan',
  'api_cases',
  'ui_cases',
  'api_execution_result',
  'ui_execution_result',
  'final_report',
  'artifacts',
  'tool_registry',
  'skill_plan',
  'tool_calls',
  'tool_summary',
  'input_type',
  'source_input',
  'current_step',
  'progress_events',
  'cancelled',
  'cancelled_at',
  'last_error',
  'scene_hints',
  'auth_chain',
  'setup_instructions',
  'setup_result',
  'login_instructions',
  'ui_login_snapshot',
  'login_playwright_commands',
  'ui_reproducible_script',
  'api_execution_policy',
  'api_path_prefix_rewrite',
]

function parseExecutionLog(value: any) {
  if (!value || typeof value !== 'string') return {}
  try {
    const parsed = JSON.parse(value)
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

function formatJson(value: any) {
  if (!value) return ''
  if (typeof value === 'string') {
    try {
      return JSON.stringify(JSON.parse(value), null, 2)
    } catch {
      return value
    }
  }
  return JSON.stringify(value, null, 2)
}

function formatPreview(value: any, limit = 6000) {
  const text = formatJson(value)
  if (!text || text.length <= limit) return text
  return `${text.slice(0, limit)}\n\n...已截断，完整内容请查看下方「日志」页签。`
}

function isTerminalStatus(status: string) {
  return terminalStatuses.includes(String(status || '').toLowerCase())
}

function isActiveStatus(status: string) {
  return activeStatuses.includes(String(status || '').toLowerCase())
}

function applySnapshot(snapshot: any) {
  if (!run.value || !snapshot || typeof snapshot !== 'object') return
  for (const key of snapshotKeys) {
    if (snapshot[key] !== undefined) run.value[key] = snapshot[key]
  }
  const previous = parseExecutionLog(run.value.execution_log)
  run.value.execution_log = JSON.stringify({ ...previous, ...snapshot })
}

function hydrateRun(data: any) {
  run.value = data
  const parsed = parseExecutionLog(data.execution_log)
  applySnapshot(parsed)
}

function connectSSE(runId: string) {
  disconnectSSE()
  const token = localStorage.getItem('testclaw_token')
  const url = `/api/v1/runs/${runId}/stream`
  eventSource = new EventSource(token ? `${url}?token=${token}` : url)
  eventSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      if (data.type === 'status' && run.value) {
        run.value.status = data.status
      }
      if (data.type === 'snapshot' && data.snapshot) {
        applySnapshot(data.snapshot)
      }
      if (data.type === 'workflow' && run.value) {
        run.value.workflow_steps = data.steps
      }
      if (data.type === 'log') {
        liveRawLog.value = data.log || ''
      }
      if (data.type === 'done') {
        if (run.value) run.value.status = data.status || run.value.status
        disconnectSSE()
        loadRun(runId)
      }
    } catch {}
  }
  eventSource.onerror = () => disconnectSSE()
}

function disconnectSSE() {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
}

async function loadRun(runId: string) {
  loading.value = true
  try {
    const { data } = await api.get(`/runs/${runId}`)
    hydrateRun(data)
    if (isActiveStatus(data.status)) {
      connectSSE(runId)
    }
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '加载运行详情失败')
  } finally {
    loading.value = false
  }
}

async function rerunRun() {
  try {
    const { data } = await api.post(`/runs/${route.params.id}/rerun`)
    router.push(`/runs/${data.id}`)
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '重跑失败')
  }
}

async function cancelRun() {
  try {
    await api.post(`/runs/${route.params.id}/cancel`)
    toast.success('已取消')
    await loadRun(String(route.params.id))
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '取消失败')
  }
}

onMounted(() => loadRun(String(route.params.id)))
onUnmounted(() => disconnectSSE())
watch(() => route.params.id, (id) => { if (id) loadRun(String(id)) })

const activeTab = ref('report')

function getScriptContent(): string {
  return run.value?.ui_reproducible_script || run.value?.artifacts?.ui_reproducible_script || ''
}

function copyScript() {
  const content = getScriptContent()
  if (content) {
    navigator.clipboard.writeText(content)
    toast.success('脚本已复制到剪贴板')
  }
}

function downloadScript() {
  const content = getScriptContent()
  if (!content) return
  const blob = new Blob([content], { type: 'text/plain' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `testclaw-${run.value?.id?.slice(0, 8) || 'script'}.sh`
  a.click()
  URL.revokeObjectURL(url)
}

function ensureSteps(steps: any): string[] {
  if (Array.isArray(steps)) return steps.map(String)
  if (typeof steps === 'string') {
    // Split numbered steps like "1. Do X\n2. Do Y" or just a long string
    const lines = steps.split(/\n/).map((l: string) => l.trim()).filter(Boolean)
    if (lines.length > 1) return lines
    // Single long string — split by sentence
    return steps.split(/(?<=[.!?。！？])\s*/).map((s: string) => s.trim()).filter(Boolean)
  }
  return []
}

function ensureList(value: any): any[] {
  return Array.isArray(value) ? value : []
}

function screenshotStepLabel(path: string, fallbackIndex: number) {
  const match = screenshotFilename(path).match(/step_(\d+)/)
  if (!match) return `截图 ${fallbackIndex + 1}`
  return `步骤 ${Number(match[1]) + 1}`
}

function screenshotLabel(shot: any, fallbackIndex: number) {
  if (shot?.title) return shot.title
  if (shot?.label) return shot.label
  return screenshotStepLabel(screenshotPath(shot), fallbackIndex)
}

function screenshotDetail(shot: any) {
  return shot?.detail || shot?.after_command || shot?.source_command || screenshotFilename(screenshotPath(shot))
}

const workflowSteps = computed(() => run.value?.workflow_steps || [])
const progressEvents = computed(() => run.value?.progress_events || [])
const currentStep = computed(() => {
  return run.value?.current_step || progressEvents.value.at(-1) || workflowSteps.value.at(-1) || null
})
const isActiveRun = computed(() => Boolean(run.value && isActiveStatus(run.value.status)))
const isFailedRun = computed(() => ['failed', 'bug_found'].includes(String(run.value?.status || '').toLowerCase()))
const apiSummary = computed(() => run.value?.api_execution_result || null)
const uiSummary = computed(() => run.value?.ui_execution_result || null)
const runType = computed(() => String(run.value?.test_type || '').toLowerCase())
const isApiOnlyRun = computed(() => runType.value === 'api')
const isUiOnlyRun = computed(() => runType.value === 'ui')
const hasApiSurface = computed(() => {
  if (!run.value || isUiOnlyRun.value) return false
  return Boolean(
    run.value.api_execution_result
    || ensureList(run.value.api_cases).length
    || run.value.final_report?.api_test_summary,
  )
})
const hasUiSurface = computed(() => {
  if (!run.value || isApiOnlyRun.value) return false
  return Boolean(
    run.value.ui_execution_result
    || ensureList(run.value.ui_cases).length
    || getScriptContent()
    || run.value.final_report?.ui_test_summary,
  )
})
const apiPassedCount = computed(() => apiSummary.value?.passed ?? run.value?.final_report?.api_test_summary?.passed ?? 0)
const apiTotalCount = computed(() => apiSummary.value?.executed ?? apiSummary.value?.total ?? run.value?.final_report?.api_test_summary?.total ?? 0)
const apiSkippedCount = computed(() => apiSummary.value?.skipped ?? run.value?.final_report?.api_test_summary?.skipped ?? 0)
const uiPassedCount = computed(() => uiSummary.value?.passed ?? run.value?.final_report?.ui_test_summary?.passed ?? 0)
const uiTotalCount = computed(() => uiSummary.value?.total ?? run.value?.final_report?.ui_test_summary?.total ?? 0)
const uiCaseEvidence = computed(() => {
  if (!run.value) return []
  const commands = ensureList(run.value.ui_execution_result?.commands)
  const commandsByCase = new Map<string, any[]>()
  const evidenceByPath = new Map<string, any>()
  const seenScreenshotIdentities = new Set<string>()
  for (const command of commands) {
    const key = String(command.case_index ?? 'run')
    commandsByCase.set(key, [...(commandsByCase.get(key) || []), command])
    if (command.screenshot) {
      const evidence = command.screenshot_evidence || {
        path: command.screenshot,
        label: command.evidence_label,
        detail: command.evidence_detail || command.normalized_command || command.command,
      }
      evidenceByPath.set(command.screenshot, evidence)
      evidenceByPath.set(screenshotFilename(command.screenshot), evidence)
    }
  }

  const normalizeShots = (items: any[], caseCommands: any[]) => {
    return items.map((item: any, index: number) => {
      const path = screenshotPath(item)
      const existing = typeof item === 'object' ? item : {}
      const byPath = evidenceByPath.get(path) || evidenceByPath.get(screenshotFilename(path)) || {}
      const byCommand = caseCommands.find((cmd) => cmd.screenshot === path || screenshotFilename(cmd.screenshot || '') === screenshotFilename(path))
      return {
        path,
        ...byPath,
        ...existing,
        label: existing.label || byPath.label || byCommand?.evidence_label || screenshotStepLabel(path, index),
        detail: existing.detail || byPath.detail || byCommand?.evidence_detail || byCommand?.normalized_command || byCommand?.command || screenshotFilename(path),
      }
    }).filter((item: any) => {
      if (!item.path) return false
      if (item.is_duplicate || item.duplicate_of) return false
      const identity = screenshotIdentity(item)
      if (identity && seenScreenshotIdentities.has(identity)) return false
      if (identity) seenScreenshotIdentities.add(identity)
      return true
    })
  }

  const cases = ensureList(run.value.ui_execution_result?.cases)
  if (cases.length) {
    return cases.map((caseItem: any, index: number) => {
      const caseIndex = caseItem.case_index ?? index
      const key = String(caseIndex)
      const caseCommands = commandsByCase.get(key) || []
      const screenshots = ensureList(caseItem.screenshot_evidence).length
        ? ensureList(caseItem.screenshot_evidence)
        : ensureList(caseItem.screenshots)
      return {
        case_index: caseIndex,
        title: caseItem.title || caseItem.case_title || `UI Case ${index + 1}`,
        status: caseItem.status || (caseItem.passed ? 'passed' : 'failed'),
        screenshots: normalizeShots(
          screenshots.length ? screenshots : caseCommands.map((cmd) => cmd.screenshot).filter(Boolean),
          caseCommands,
        ),
        commands: caseCommands,
      }
    })
  }

  const artifactEvidence = ensureList(run.value.artifacts?.ui_case_evidence)
  if (artifactEvidence.length) {
    return artifactEvidence.map((caseItem: any, index: number) => {
      const caseIndex = caseItem.case_index ?? index
      const key = String(caseIndex)
      return {
        case_index: caseIndex,
        title: caseItem.title || `UI Case ${index + 1}`,
        status: caseItem.status || 'completed',
        screenshots: normalizeShots(
          ensureList(caseItem.screenshot_evidence).length ? ensureList(caseItem.screenshot_evidence) : ensureList(caseItem.screenshots),
          commandsByCase.get(key) || [],
        ),
        commands: commandsByCase.get(key) || [],
      }
    })
  }

  const flatScreenshots = ensureList(run.value.artifacts?.ui_screenshots)
  if (!flatScreenshots.length) return []
  return [{
    case_index: 'run',
    title: '运行截图',
    status: 'completed',
    screenshots: normalizeShots(flatScreenshots, commands.filter((cmd) => cmd.screenshot)),
    commands: commands.filter((cmd) => cmd.screenshot),
  }]
})
const uiScreenshotCount = computed(() => uiCaseEvidence.value.reduce((total: number, item: any) => {
  return total + ensureList(item.screenshots).length
}, 0))
const hasScreenshots = computed(() => hasUiSurface.value && uiScreenshotCount.value > 0)
const toolCalls = computed(() => ensureList(run.value?.tool_calls || run.value?.artifacts?.tool_calls))
const skillPlan = computed(() => ensureList(run.value?.skill_plan || run.value?.final_report?.skill_plan))
const toolSummary = computed(() => run.value?.tool_summary || run.value?.final_report?.tool_summary || run.value?.artifacts?.tool_summary || null)
const hasToolSurface = computed(() => toolCalls.value.length > 0 || skillPlan.value.length > 0 || Boolean(toolSummary.value))
function toolStatusClass(status: string) {
  const value = String(status || '').toLowerCase()
  if (['success', 'passed', 'done'].includes(value)) return 'bg-emerald-100 text-emerald-700'
  if (value === 'skipped') return 'bg-amber-100 text-amber-700'
  if (['failed', 'error'].includes(value)) return 'bg-red-100 text-red-700'
  return 'bg-gray-100 text-gray-600'
}
const visibleTabs = computed(() => {
  const tabs = [
    { key: 'report', label: '报告', icon: FileText },
  ]
  if (hasApiSurface.value) tabs.push({ key: 'api', label: 'API 测试', icon: Zap })
  if (hasUiSurface.value) tabs.push({ key: 'ui', label: 'UI 测试', icon: Monitor })
  if (hasScreenshots.value) tabs.push({ key: 'screenshots', label: '截图证据', icon: Camera })
  if (hasToolSurface.value) tabs.push({ key: 'tools', label: '工具', icon: Activity })
  tabs.push({ key: 'cases', label: '测试用例', icon: FileText })
  if (hasUiSurface.value && getScriptContent()) tabs.push({ key: 'script', label: '脚本', icon: Terminal })
  tabs.push({ key: 'logs', label: '日志', icon: Terminal })
  return tabs
})
const legacyApiMisreport = computed(() => {
  if (!run.value || !isApiOnlyRun.value) return false
  const failed = Number(run.value.api_execution_result?.failed || 0)
  if (failed <= 0) return false
  const sample = ensureList(run.value.api_execution_result?.results).slice(0, 30)
  return sample.some((item: any) => String(item.url || '').includes('/dev-api/'))
    || String(run.value.execution_result?.stdout || '').includes('/dev-api/')
})
const progressPercent = computed(() => {
  if (!run.value) return 0
  if (isTerminalStatus(run.value.status)) return 100
  if (run.value.status === 'queued') return 5

  const steps = workflowSteps.value
  const events = progressEvents.value
  const current = currentStep.value
  const doneSteps = steps.filter((step: any) => ['done', 'failed', 'cancelled'].includes(String(step.status || '').toLowerCase())).length
  const runningSteps = steps.filter((step: any) => String(step.status || '').toLowerCase() === 'running').length

  if (steps.length) {
    const ratio = (doneSteps + (runningSteps > 0 || String(current?.status || '').toLowerCase() === 'running' ? 0.35 : 0)) / steps.length
    return Math.max(12, Math.min(88, Math.round(ratio * 100)))
  }

  if (events.length) {
    const terminalEvents = events.filter((event: any) => ['done', 'failed', 'cancelled'].includes(String(event.status || '').toLowerCase())).length
    const runningBonus = String(current?.status || '').toLowerCase() === 'running' ? 6 : 0
    return Math.max(10, Math.min(72, terminalEvents * 12 + Math.min(events.length, 6) * 4 + runningBonus))
  }

  return run.value.status === 'running' ? 12 : 0
})
const activityFeed = computed(() => {
  const feed = [
    ...progressEvents.value.map((event: any) => ({
      ...event,
      source: 'progress',
      timestamp: event.timestamp || '',
    })),
  ]
  if (!feed.length) {
    feed.push(...workflowSteps.value.map((step: any, index: number) => ({
      node: step.node,
      status: step.status,
      detail: step.detail,
      source: 'workflow',
      timestamp: '',
      index,
    })))
  }
  return feed.slice(-12).reverse()
})
const statusTitle = computed(() => {
  const status = String(run.value?.status || '').toLowerCase()
  if (status === 'queued') return '等待执行'
  if (status === 'running') return '正在执行'
  if (status === 'succeeded') return '执行完成'
  if (status === 'bug_found') return '发现缺陷'
  if (status === 'failed') return '执行失败'
  if (status === 'cancelled') return '已取消'
  return 'Agent Cockpit'
})
const statusDescription = computed(() => {
  if (isActiveRun.value) return '测试智能体正在准备测试计划、执行动作并采集证据'
  if (run.value?.last_error) return run.value.last_error
  if (run.value?.final_report?.summary) return run.value.final_report.summary
  if (currentStep.value?.detail) return currentStep.value.detail
  return '暂无更多执行信息'
})

watch(visibleTabs, (tabs) => {
  if (!tabs.some((tab) => tab.key === activeTab.value)) {
    activeTab.value = 'report'
  }
})
</script>

<template>
  <LoadingSpinner v-if="loading && !run" text="加载运行详情..." />

  <div class="space-y-6 pb-12" v-else-if="run">
    <!-- Header -->
    <div class="flex items-center gap-4">
      <button @click="router.push('/history')" class="p-2 hover:bg-gray-100 rounded-lg text-gray-400 hover:text-gray-600 transition-all">
        <ArrowLeft :size="20" />
      </button>
      <div class="flex-1 min-w-0">
        <h2 class="text-xl font-bold tracking-tight text-gray-900 truncate">{{ run.objective }}</h2>
        <p class="text-gray-400 text-xs font-mono truncate">{{ run.target_url }}</p>
      </div>
      <div class="flex items-center gap-2 shrink-0">
        <button
          v-if="['queued','running'].includes(run.status)"
          @click="cancelRun"
          class="px-3 py-1.5 bg-red-50 text-red-600 border border-red-200 hover:bg-red-100 rounded-lg text-xs font-bold transition-all flex items-center gap-1.5"
        >
          <XCircle :size="14" /> 取消
        </button>
        <button
          v-if="['succeeded','failed','bug_found'].includes(run.status)"
          @click="rerunRun"
          class="px-3 py-1.5 bg-blue-50 text-blue-600 border border-blue-200 hover:bg-blue-100 rounded-lg text-xs font-bold transition-all flex items-center gap-1.5"
        >
          <RotateCcw :size="14" /> 重跑
        </button>
        <StatusBadge :status="run.status" />
      </div>
    </div>

    <!-- Meta Bar -->
    <div class="flex flex-wrap gap-2">
      <span class="px-2 py-1 bg-gray-100 text-gray-600 rounded-lg text-[10px] font-bold border border-gray-200">
        {{ run.test_type?.toUpperCase() }}
      </span>
      <span v-if="run.input_type" class="px-2 py-1 bg-blue-50 text-blue-600 rounded-lg text-[10px] font-bold border border-blue-100">
        {{ run.input_type }}
      </span>
      <span v-if="run.created_at" class="px-2 py-1 bg-gray-50 text-gray-500 rounded-lg text-[10px] font-mono">
        {{ new Date(run.created_at).toLocaleString('zh-CN') }}
      </span>
    </div>

    <!-- Execution Cockpit -->
    <section class="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">
      <div
        class="p-5 border-b"
        :class="isFailedRun ? 'bg-red-50/70 border-red-100' : isActiveRun ? 'bg-blue-50/70 border-blue-100' : 'bg-gray-50 border-gray-100'"
      >
        <div class="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2">
              <Loader2 v-if="isActiveRun" :size="18" class="text-blue-600 animate-spin" />
              <AlertTriangle v-else-if="isFailedRun" :size="18" class="text-red-600" />
              <CheckCircle2 v-else-if="run.status === 'succeeded'" :size="18" class="text-emerald-600" />
              <Clock v-else :size="18" class="text-gray-500" />
              <h3 class="text-lg font-bold text-gray-900">{{ statusTitle }}</h3>
            </div>
            <p class="mt-1 text-sm text-gray-600 line-clamp-2">{{ statusDescription }}</p>
          </div>
          <div class="flex flex-col items-start gap-2 lg:items-end">
            <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">当前步骤</div>
            <div class="max-w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-bold text-gray-700 shadow-sm lg:max-w-md">
              {{ currentStep?.node || currentStep?.name || currentStep?.stage || currentStep || '初始化' }}
            </div>
          </div>
        </div>

        <div class="mt-5">
          <div class="mb-2 flex items-center justify-between text-[10px] font-bold uppercase tracking-widest text-gray-400">
            <span>执行进度</span>
            <span>{{ progressPercent }}%</span>
          </div>
          <div class="h-2 overflow-hidden rounded-full bg-white border border-gray-200">
            <div
              class="h-full rounded-full transition-all duration-500"
              :class="isFailedRun ? 'bg-red-500' : run.status === 'succeeded' ? 'bg-emerald-500' : 'bg-blue-500'"
              :style="{ width: `${progressPercent}%` }"
            ></div>
          </div>
        </div>

        <div
          v-if="isActiveRun || isFailedRun || run.status === 'cancelled'"
          class="mt-4 flex flex-col gap-3 rounded-lg border px-4 py-3 text-xs sm:flex-row sm:items-center sm:justify-between"
          :class="isFailedRun ? 'border-red-200 bg-white text-red-700' : run.status === 'cancelled' ? 'border-gray-200 bg-white text-gray-600' : 'border-blue-200 bg-white text-blue-700'"
        >
          <div class="flex min-w-0 items-start gap-2">
            <Activity v-if="isActiveRun" :size="15" class="mt-0.5 shrink-0" />
            <AlertTriangle v-else :size="15" class="mt-0.5 shrink-0" />
            <span class="min-w-0">
              <template v-if="isActiveRun">运行中会持续刷新智能体动作、证据和实时日志。</template>
              <template v-else-if="isFailedRun">{{ run.last_error || '执行失败，请查看下方日志定位原因。' }}</template>
              <template v-else>运行已取消，历史日志仍保留在页面中。</template>
            </span>
          </div>
          <button
            v-if="isActiveRun"
            @click="cancelRun"
            class="shrink-0 rounded-lg border border-red-200 bg-red-50 px-3 py-1.5 text-[11px] font-bold text-red-600 transition-all hover:bg-red-100"
          >
            取消运行
          </button>
        </div>

        <div
          v-if="legacyApiMisreport"
          class="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-xs leading-5 text-amber-800"
        >
          这是旧版 API 执行策略产生的历史结果：当时会请求 /dev-api 写入接口并把缺少鉴权的正向断言计为失败。请使用当前安全只读策略重跑，新的运行会跳过写入接口并显示跳过原因。
        </div>
      </div>

      <div class="grid gap-0 lg:grid-cols-[minmax(0,1fr)_minmax(320px,0.75fr)]">
        <div class="border-b border-gray-100 p-5 lg:border-b-0 lg:border-r">
          <div
            class="grid gap-3"
            :class="hasApiSurface && hasUiSurface ? 'sm:grid-cols-2' : 'sm:grid-cols-1'"
          >
            <div v-if="hasApiSurface" class="rounded-lg border border-gray-200 bg-gray-50 p-4">
              <div class="flex items-center justify-between">
                <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">API 结果</div>
                <Zap :size="15" class="text-blue-500" />
              </div>
              <div class="mt-3 flex items-end gap-2">
                <span class="text-2xl font-bold text-gray-900">{{ apiPassedCount }}</span>
                <span class="pb-1 text-xs font-bold text-gray-400">/ {{ apiTotalCount }} 通过</span>
              </div>
              <div class="mt-2 text-xs text-gray-500">
                失败 {{ apiSummary?.failed ?? ((run.final_report?.api_test_summary?.total || 0) - (run.final_report?.api_test_summary?.passed || 0)) }}
                <span v-if="apiSkippedCount"> · 跳过 {{ apiSkippedCount }}</span> ·
                {{ apiSummary?.pass_rate || run.final_report?.api_test_summary?.pass_rate || '等待结果' }}
              </div>
            </div>

            <div v-if="hasUiSurface" class="rounded-lg border border-gray-200 bg-gray-50 p-4">
              <div class="flex items-center justify-between">
                <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">UI 结果</div>
                <Monitor :size="15" class="text-violet-500" />
              </div>
              <div class="mt-3 flex items-end gap-2">
                <span class="text-2xl font-bold text-gray-900">{{ uiPassedCount }}</span>
                <span class="pb-1 text-xs font-bold text-gray-400">/ {{ uiTotalCount }} 通过</span>
              </div>
              <div class="mt-2 text-xs text-gray-500">
                失败 {{ uiSummary?.failed ?? ((run.final_report?.ui_test_summary?.total || 0) - (run.final_report?.ui_test_summary?.passed || 0)) }} ·
                {{ uiSummary?.pass_rate || run.final_report?.ui_test_summary?.pass_rate || '等待结果' }}
              </div>
            </div>
          </div>

          <div class="mt-5">
            <div class="mb-3 flex items-center justify-between">
              <h3 class="text-xs font-bold uppercase tracking-widest text-gray-400">最近活动</h3>
              <span v-if="activityFeed.length" class="text-[10px] font-bold text-gray-400">{{ activityFeed.length }} 条</span>
            </div>
            <div v-if="activityFeed.length" class="space-y-2">
              <div
                v-for="(item, idx) in activityFeed"
                :key="`${item.source}-${item.timestamp || item.node || idx}`"
                class="flex items-start gap-3 rounded-lg border border-gray-100 bg-white px-3 py-2 text-xs"
              >
                <span
                  class="mt-0.5 h-2 w-2 shrink-0 rounded-full"
                  :class="item.status === 'failed' ? 'bg-red-500' : item.status === 'done' ? 'bg-emerald-500' : item.status === 'cancelled' ? 'bg-gray-400' : 'bg-blue-500'"
                ></span>
                <div class="min-w-0 flex-1">
                  <div class="flex flex-wrap items-center gap-2">
                    <span class="font-bold text-gray-800">{{ item.node || item.stage || item.event || '执行事件' }}</span>
                    <span class="rounded bg-gray-100 px-1.5 py-0.5 text-[10px] font-bold text-gray-500">{{ item.status || item.source }}</span>
                  </div>
                  <p v-if="item.detail || item.message" class="mt-0.5 text-gray-500">{{ item.detail || item.message }}</p>
                </div>
                <span v-if="item.timestamp" class="shrink-0 font-mono text-[10px] text-gray-400">{{ new Date(item.timestamp).toLocaleTimeString('zh-CN') }}</span>
              </div>
            </div>
            <div v-else class="rounded-lg border border-dashed border-gray-200 bg-gray-50 px-4 py-8 text-center text-sm text-gray-400">
              等待执行活动
            </div>
          </div>
        </div>

        <div class="bg-gray-950 p-5 text-gray-100">
          <div class="mb-3 flex items-center justify-between">
            <h3 class="flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-gray-400">
              <Terminal :size="14" /> 实时日志
            </h3>
            <span v-if="isActiveRun" class="flex items-center gap-1.5 text-[10px] font-bold text-blue-300">
              <span class="h-1.5 w-1.5 rounded-full bg-blue-400"></span>
              LIVE
            </span>
          </div>
          <pre
            v-if="liveRawLog || run.execution_log"
            class="max-h-[600px] overflow-auto whitespace-pre-wrap rounded-lg border border-white/10 bg-black/30 p-4 text-[11px] leading-relaxed text-gray-100"
          >{{ liveRawLog || formatPreview(run.execution_log) }}</pre>
          <div v-else class="flex min-h-48 items-center justify-center rounded-lg border border-dashed border-white/10 bg-black/20 px-4 text-center text-xs text-gray-500">
            <span v-if="isActiveRun">等待第一条执行日志...</span>
            <span v-else>暂无实时日志</span>
          </div>
        </div>
      </div>
    </section>

    <!-- Final Report Summary Card -->
    <div v-if="run.final_report" class="bg-white border border-gray-200 rounded-xl shadow-sm p-6">
      <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4">测试总结</h3>
      <div class="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-4">
        <div class="p-3 rounded-lg border" :class="run.final_report.overall_verdict === 'PASS' ? 'bg-emerald-50 border-emerald-200' : run.final_report.overall_verdict === 'PARTIAL' ? 'bg-amber-50 border-amber-200' : 'bg-red-50 border-red-200'">
          <div class="text-[10px] font-bold text-gray-400 uppercase">最终结论</div>
          <div class="text-lg font-bold mt-1" :class="run.final_report.overall_verdict === 'PASS' ? 'text-emerald-600' : run.final_report.overall_verdict === 'PARTIAL' ? 'text-amber-600' : 'text-red-600'">
            {{ run.final_report.overall_verdict }}
          </div>
        </div>
        <div v-if="hasApiSurface" class="p-3 bg-gray-50 rounded-lg border border-gray-100">
          <div class="text-[10px] font-bold text-gray-400 uppercase">API 测试</div>
          <div class="text-sm font-bold text-gray-900 mt-1">{{ run.final_report.api_test_summary?.pass_rate || 'N/A' }}</div>
          <div class="text-[10px] text-gray-500">{{ run.final_report.api_test_summary?.passed || 0 }}/{{ run.final_report.api_test_summary?.total || 0 }} 通过</div>
        </div>
        <div v-if="hasUiSurface" class="p-3 bg-gray-50 rounded-lg border border-gray-100">
          <div class="text-[10px] font-bold text-gray-400 uppercase">UI 测试</div>
          <div class="text-sm font-bold text-gray-900 mt-1">{{ run.final_report.ui_test_summary?.pass_rate || 'N/A' }}</div>
          <div class="text-[10px] text-gray-500">{{ run.final_report.ui_test_summary?.passed || 0 }}/{{ run.final_report.ui_test_summary?.total || 0 }} 通过</div>
        </div>
        <div class="p-3 bg-gray-50 rounded-lg border border-gray-100">
          <div class="text-[10px] font-bold text-gray-400 uppercase">发现缺陷</div>
          <div class="text-lg font-bold text-red-600 mt-1">{{ run.final_report.bugs_found?.length || 0 }}</div>
        </div>
      </div>
      <p v-if="run.final_report.summary" class="text-sm text-gray-600">{{ run.final_report.summary }}</p>
      <div v-if="run.final_report.recommendations?.length" class="mt-3 space-y-1">
        <div v-for="(rec, i) in run.final_report.recommendations" :key="i" class="text-xs text-gray-500 flex gap-2">
          <span class="text-blue-400 font-bold">-</span> {{ rec }}
        </div>
      </div>
    </div>

    <!-- Workflow Timeline -->
    <div v-if="run.workflow_steps?.length" class="bg-white border border-gray-200 rounded-xl shadow-sm p-6">
      <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4">Agent 工作流</h3>
      <div class="flex flex-wrap gap-2">
        <div
          v-for="(step, idx) in run.workflow_steps"
          :key="idx"
          class="flex items-center gap-2 px-3 py-2 rounded-lg border text-xs"
          :class="step.status === 'done' ? 'bg-emerald-50 border-emerald-200 text-emerald-700' : step.status === 'failed' ? 'bg-red-50 border-red-200 text-red-700' : 'bg-gray-50 border-gray-200 text-gray-600'"
        >
          <span class="font-bold">{{ step.node }}</span>
          <span class="text-[10px] opacity-70">{{ step.detail }}</span>
        </div>
      </div>
    </div>

    <!-- Tab Navigation -->
    <div class="flex flex-wrap gap-1 bg-gray-100 rounded-lg p-1">
      <button
        v-for="tab in visibleTabs"
        :key="tab.key"
        @click="activeTab = tab.key"
        class="flex items-center gap-1.5 px-4 py-2 rounded-md text-xs font-bold transition-all"
        :class="activeTab === tab.key ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'"
      >
        <component :is="tab.icon" :size="14" />
        {{ tab.label }}
      </button>
    </div>

    <!-- Tab: Report -->
    <div v-if="activeTab === 'report'" class="space-y-4">
      <div v-if="run.final_report" class="bg-white border border-gray-200 rounded-xl shadow-sm p-6">
        <div class="flex flex-col gap-3 border-b border-gray-100 pb-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest">完整测试报告</h3>
            <p class="mt-2 text-sm leading-6 text-gray-700">{{ run.final_report.summary || '暂无报告摘要' }}</p>
          </div>
          <span
            class="shrink-0 rounded-lg px-3 py-1.5 text-xs font-bold"
            :class="run.final_report.overall_verdict === 'PASS' ? 'bg-emerald-100 text-emerald-700' : run.final_report.overall_verdict === 'PARTIAL' ? 'bg-amber-100 text-amber-700' : 'bg-red-100 text-red-700'"
          >
            {{ run.final_report.overall_verdict || 'UNKNOWN' }}
          </span>
        </div>

        <div
          class="mt-4 grid gap-4"
          :class="hasApiSurface && hasUiSurface ? 'lg:grid-cols-2' : 'lg:grid-cols-1'"
        >
          <div v-if="hasApiSurface" class="rounded-lg border border-gray-100 bg-gray-50 p-4">
            <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">API 结论</div>
            <div class="mt-2 text-sm font-bold text-gray-900">
              {{ run.final_report.api_test_summary?.passed || 0 }}/{{ run.final_report.api_test_summary?.total || 0 }} 通过
            </div>
            <ul class="mt-3 space-y-1">
              <li v-for="(finding, i) in ensureList(run.final_report.api_test_summary?.key_findings)" :key="`api-finding-${i}`" class="text-xs leading-5 text-gray-600">
                {{ finding }}
              </li>
            </ul>
          </div>
          <div v-if="hasUiSurface" class="rounded-lg border border-gray-100 bg-gray-50 p-4">
            <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">UI 结论</div>
            <div class="mt-2 text-sm font-bold text-gray-900">
              {{ run.final_report.ui_test_summary?.passed || 0 }}/{{ run.final_report.ui_test_summary?.total || 0 }} 通过
            </div>
            <ul class="mt-3 space-y-1">
              <li v-for="(finding, i) in ensureList(run.final_report.ui_test_summary?.key_findings)" :key="`ui-finding-${i}`" class="text-xs leading-5 text-gray-600">
                {{ finding }}
              </li>
            </ul>
          </div>
        </div>

        <div v-if="run.final_report.execution_notes?.length" class="mt-4 rounded-lg border border-blue-100 bg-blue-50 p-4">
          <div class="text-[10px] font-bold uppercase tracking-widest text-blue-500">执行说明</div>
          <ul class="mt-2 space-y-1">
            <li v-for="(note, i) in run.final_report.execution_notes" :key="`note-${i}`" class="text-xs leading-5 text-blue-700">{{ note }}</li>
          </ul>
        </div>

        <div v-if="toolSummary" class="mt-4 rounded-lg border border-gray-100 bg-gray-50 p-4">
          <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">工具调用</div>
          <div class="mt-2 text-sm font-bold text-gray-900">{{ toolSummary.total || 0 }} 次</div>
          <div class="mt-3 grid grid-cols-2 gap-2 md:grid-cols-4">
            <div v-for="(item, layer) in (toolSummary.by_layer || {})" :key="layer" class="rounded-lg border border-gray-100 bg-white p-3">
              <div class="text-[10px] font-bold uppercase text-gray-400">{{ layer }}</div>
              <div class="mt-1 text-xs text-gray-700">{{ item.success || 0 }} 成功 · {{ item.failed || 0 }} 失败 · {{ item.skipped || 0 }} 跳过</div>
            </div>
          </div>
        </div>

        <div v-if="run.final_report.recommendations?.length" class="mt-4 rounded-lg border border-amber-100 bg-amber-50 p-4">
          <div class="text-[10px] font-bold uppercase tracking-widest text-amber-500">后续建议</div>
          <ul class="mt-2 space-y-1">
            <li v-for="(rec, i) in run.final_report.recommendations" :key="`report-rec-${i}`" class="text-xs leading-5 text-amber-800">{{ rec }}</li>
          </ul>
        </div>
      </div>

      <!-- Bugs Found -->
      <div v-if="run.final_report?.bugs_found?.length" class="bg-white border border-red-200 rounded-xl shadow-sm p-6">
        <h3 class="text-xs font-bold text-red-400 uppercase tracking-widest mb-4">发现的缺陷</h3>
        <div class="space-y-3">
          <div v-for="(bug, i) in run.final_report.bugs_found" :key="i" class="p-4 bg-red-50 rounded-lg border border-red-100">
            <div class="flex items-center justify-between mb-2">
              <span class="font-bold text-sm text-red-900">{{ bug.title }}</span>
              <span class="px-2 py-0.5 bg-red-200 text-red-800 rounded text-[10px] font-bold">{{ bug.severity }}</span>
            </div>
            <p class="text-xs text-red-700">{{ bug.description }}</p>
          </div>
        </div>
      </div>

      <!-- Execution Result (legacy) -->
      <div v-if="run.execution_result && !run.final_report" class="bg-white border border-gray-200 rounded-xl shadow-sm p-6">
        <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4">执行结果</h3>
        <div class="grid grid-cols-2 gap-4 mb-4">
          <div class="p-3 bg-gray-50 rounded-lg border border-gray-100">
            <div class="text-[10px] font-bold text-gray-400 uppercase">退出码</div>
            <div class="text-lg font-bold mt-1" :class="run.execution_result.status_code === 0 ? 'text-emerald-600' : 'text-red-600'">
              {{ run.execution_result.status_code }}
            </div>
          </div>
          <div class="p-3 bg-gray-50 rounded-lg border border-gray-100">
            <div class="text-[10px] font-bold text-gray-400 uppercase">追踪</div>
            <div class="text-xs font-mono text-gray-600 mt-1 truncate">{{ run.execution_result.trace_path || '无' }}</div>
          </div>
        </div>
        <div v-if="run.execution_result.stderr" class="mt-3">
          <div class="text-[10px] font-bold text-red-400 uppercase mb-1">错误输出</div>
          <pre class="bg-red-50 border border-red-100 rounded-lg p-4 text-xs font-mono text-red-700 overflow-auto max-h-40">{{ run.execution_result.stderr }}</pre>
        </div>
      </div>
    </div>

    <!-- Tab: API Tests -->
    <div v-if="activeTab === 'api'" class="space-y-4">
      <div v-if="run.api_execution_result" class="bg-white border border-gray-200 rounded-xl shadow-sm p-6">
        <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4">API 测试结果</h3>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
          <div class="p-3 bg-gray-50 rounded-lg border border-gray-100">
            <div class="text-[10px] font-bold text-gray-400 uppercase">总计</div>
            <div class="text-lg font-bold text-gray-900 mt-1">{{ run.api_execution_result.total }}</div>
          </div>
          <div class="p-3 bg-emerald-50 rounded-lg border border-emerald-100">
            <div class="text-[10px] font-bold text-emerald-400 uppercase">通过</div>
            <div class="text-lg font-bold text-emerald-600 mt-1">{{ run.api_execution_result.passed }}</div>
          </div>
          <div class="p-3 bg-red-50 rounded-lg border border-red-100">
            <div class="text-[10px] font-bold text-red-400 uppercase">失败</div>
            <div class="text-lg font-bold text-red-600 mt-1">{{ run.api_execution_result.failed }}</div>
          </div>
          <div class="p-3 bg-amber-50 rounded-lg border border-amber-100">
            <div class="text-[10px] font-bold text-amber-500 uppercase">跳过</div>
            <div class="text-lg font-bold text-amber-700 mt-1">{{ run.api_execution_result.skipped || 0 }}</div>
          </div>
        </div>

        <div class="overflow-x-auto">
          <table class="w-full text-left text-xs">
            <thead>
              <tr class="bg-gray-50 text-gray-500 border-b border-gray-100">
                <th class="px-4 py-2 font-semibold uppercase tracking-wider text-[10px] w-6"></th>
                <th class="px-4 py-2 font-semibold uppercase tracking-wider text-[10px]">状态</th>
                <th class="px-4 py-2 font-semibold uppercase tracking-wider text-[10px]">分类</th>
                <th class="px-4 py-2 font-semibold uppercase tracking-wider text-[10px]">方法</th>
                <th class="px-4 py-2 font-semibold uppercase tracking-wider text-[10px]">URL</th>
                <th class="px-4 py-2 font-semibold uppercase tracking-wider text-[10px]">状态码</th>
                <th class="px-4 py-2 font-semibold uppercase tracking-wider text-[10px]">耗时</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-100">
              <template v-for="(r, i) in run.api_execution_result.results" :key="i">
              <tr @click="toggleApiRow(i)" class="hover:bg-gray-50 cursor-pointer">
                <td class="px-4 py-2">
                  <ChevronDown v-if="expandedApiRow === i" :size="12" class="text-gray-400" />
                  <ChevronRight v-else :size="12" class="text-gray-400" />
                </td>
                <td class="px-4 py-2">
                  <AlertTriangle v-if="r.skipped" :size="14" class="text-amber-500" />
                  <CheckCircle2 v-else-if="r.passed" :size="14" class="text-emerald-500" />
                  <XCircleIcon v-else :size="14" class="text-red-500" />
                </td>
                <td class="px-4 py-2 text-gray-500">{{ r.category }}</td>
                <td class="px-4 py-2 font-mono font-bold text-gray-700">{{ r.method }}</td>
                <td class="px-4 py-2 font-mono text-gray-500 truncate max-w-xs">{{ r.url }}</td>
                <td class="px-4 py-2 font-mono" :class="r.skipped ? 'text-amber-600' : r.status_code >= 200 && r.status_code < 400 ? 'text-emerald-600' : 'text-red-600'">{{ r.skipped ? 'SKIP' : r.status_code }}</td>
                <td class="px-4 py-2 font-mono text-gray-500">{{ r.elapsed_ms }}ms</td>
              </tr>
              <tr v-if="expandedApiRow === i">
                <td colspan="7" class="px-4 py-3 bg-gray-50">
                  <div class="grid grid-cols-2 gap-3">
                    <div>
                      <div class="text-[10px] font-bold text-gray-400 uppercase mb-1">请求头</div>
                      <pre class="bg-gray-900 text-gray-200 rounded-lg p-3 text-[10px] font-mono overflow-auto max-h-32">{{ JSON.stringify(r.request_headers || {}, null, 2) }}</pre>
                    </div>
                    <div>
                      <div class="text-[10px] font-bold text-gray-400 uppercase mb-1">响应体</div>
                      <pre class="bg-gray-900 text-gray-200 rounded-lg p-3 text-[10px] font-mono overflow-auto max-h-32">{{ typeof r.body === 'object' ? JSON.stringify(r.body, null, 2) : r.body }}</pre>
                    </div>
                  </div>
                  <div v-if="r.error" class="mt-2">
                    <div class="text-[10px] font-bold text-red-400 uppercase mb-1">错误</div>
                    <div class="text-xs text-red-600 font-mono">{{ r.error }}</div>
                  </div>
                  <div v-if="r.skip_reason" class="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                    {{ r.skip_reason }}
                  </div>
                  <div v-if="r.assertion_results?.length" class="mt-2">
                    <div class="text-[10px] font-bold text-gray-400 uppercase mb-1">断言结果</div>
                    <div v-for="(a, j) in r.assertion_results" :key="j" class="flex items-center gap-2 text-[10px]">
                      <CheckCircle2 v-if="a.passed" :size="10" class="text-emerald-500" />
                      <XCircleIcon v-else :size="10" class="text-red-500" />
                      <span class="font-mono text-gray-600">{{ a.type }}: {{ a.passed ? 'PASS' : 'FAIL' }}</span>
                      <span v-if="a.actual !== undefined" class="text-gray-400">actual={{ JSON.stringify(a.actual) }}</span>
                    </div>
                  </div>
                </td>
              </tr>
              </template>
            </tbody>
          </table>
        </div>
      </div>
      <div v-else class="text-center py-12 text-gray-400 text-sm">未执行 API 测试</div>
    </div>

    <!-- Tab: UI Tests -->
    <div v-if="activeTab === 'ui'" class="space-y-4">
      <div v-if="run.ui_execution_result" class="bg-white border border-gray-200 rounded-xl shadow-sm p-6">
        <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4">UI 测试结果</h3>
        <div class="grid grid-cols-3 gap-4 mb-4">
          <div class="p-3 bg-gray-50 rounded-lg border border-gray-100">
            <div class="text-[10px] font-bold text-gray-400 uppercase">用例数</div>
            <div class="text-lg font-bold text-gray-900 mt-1">{{ run.ui_execution_result.total }}</div>
            <div v-if="run.ui_execution_result.command_total !== undefined" class="text-[10px] text-gray-400 mt-0.5">
              {{ run.ui_execution_result.command_completed || 0 }}/{{ run.ui_execution_result.command_total }} 命令
            </div>
          </div>
          <div class="p-3 bg-emerald-50 rounded-lg border border-emerald-100">
            <div class="text-[10px] font-bold text-emerald-400 uppercase">通过</div>
            <div class="text-lg font-bold text-emerald-600 mt-1">{{ run.ui_execution_result.passed }}</div>
          </div>
          <div class="p-3 bg-red-50 rounded-lg border border-red-100">
            <div class="text-[10px] font-bold text-red-400 uppercase">失败</div>
            <div class="text-lg font-bold text-red-600 mt-1">{{ run.ui_execution_result.failed }}</div>
          </div>
        </div>

        <!-- Commands log -->
        <div class="space-y-2">
          <div v-for="(cmd, i) in run.ui_execution_result.commands" :key="i"
            class="flex items-center gap-3 p-3 rounded-lg border text-xs"
            :class="cmd.status_code === 0 ? 'bg-emerald-50 border-emerald-100' : 'bg-red-50 border-red-100'"
          >
            <span v-if="cmd.case_title" class="text-gray-400 shrink-0">{{ cmd.case_title }}</span>
            <span class="font-mono font-bold text-gray-700 shrink-0">{{ cmd.normalized_command || cmd.command }}</span>
            <span v-if="cmd.normalization" class="text-blue-500 truncate">{{ cmd.normalization }}</span>
            <span v-if="cmd.stderr" class="text-red-500 truncate">{{ cmd.stderr }}</span>
          </div>
        </div>

        <!-- Screenshots -->
        <div v-if="uiCaseEvidence.length" class="mt-6">
          <h4 class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-3 flex items-center gap-2">
            <Camera :size="14" /> 截图证据 ({{ uiScreenshotCount }})
          </h4>
          <div class="space-y-4">
            <div
              v-for="caseEvidence in uiCaseEvidence"
              :key="caseEvidence.case_index"
              class="rounded-lg border border-gray-200 bg-gray-50 p-3"
            >
              <div class="mb-3 flex items-center justify-between gap-3">
                <div class="min-w-0">
                  <div class="truncate text-xs font-bold text-gray-800">{{ caseEvidence.title }}</div>
                  <div class="mt-0.5 text-[10px] font-mono text-gray-400">case {{ caseEvidence.case_index }}</div>
                </div>
                <span
                  class="shrink-0 rounded px-2 py-0.5 text-[10px] font-bold"
                  :class="caseEvidence.status === 'passed' ? 'bg-emerald-100 text-emerald-700' : caseEvidence.status === 'failed' ? 'bg-red-100 text-red-700' : 'bg-gray-100 text-gray-500'"
                >
                  {{ caseEvidence.status }}
                </span>
              </div>
              <div v-if="caseEvidence.screenshots.length" class="grid grid-cols-2 gap-3 lg:grid-cols-3">
	                <div
	                  v-for="(shot, i) in caseEvidence.screenshots"
	                  :key="screenshotPath(shot)"
	                  class="cursor-pointer overflow-hidden rounded-lg border border-gray-200 bg-white transition-all hover:ring-2 hover:ring-blue-400"
	                  @click="openScreenshot(run.id, shot)"
	                >
	                  <img
	                    :src="screenshotDisplayUrl(run.id, shot)"
	                    :alt="`${caseEvidence.title} ${screenshotLabel(shot, i)}`"
	                    class="h-32 w-full object-cover"
	                    loading="lazy"
	                    @error="(e: any) => { e.target.style.display = 'none'; e.target.nextSibling && (e.target.nextSibling.style.display = 'flex') }"
	                  />
	                  <div class="hidden h-32 items-center justify-center text-xs text-gray-400">加载失败</div>
	                  <div class="border-t border-gray-100 p-2">
	                    <div class="text-[10px] font-bold text-gray-600">{{ screenshotLabel(shot, i) }}</div>
	                    <div class="truncate text-[10px] text-gray-500">{{ screenshotDetail(shot) }}</div>
	                    <div class="truncate font-mono text-[10px] text-gray-400">{{ screenshotFilename(screenshotPath(shot)) }}</div>
	                  </div>
	                </div>
              </div>
              <div v-else class="rounded-lg border border-dashed border-gray-200 bg-white px-4 py-6 text-center text-xs text-gray-400">
                此用例暂无截图证据
              </div>
            </div>
          </div>
        </div>
      </div>
      <div v-else class="text-center py-12 text-gray-400 text-sm">未执行 UI 测试</div>
    </div>

    <!-- Tab: Screenshot Evidence -->
    <div v-if="activeTab === 'screenshots'" class="space-y-4">
      <div v-if="uiCaseEvidence.length" class="bg-white border border-gray-200 rounded-xl shadow-sm p-6">
        <div class="mb-4 flex items-center justify-between">
          <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest flex items-center gap-2">
            <Camera :size="14" /> 截图证据
          </h3>
          <span class="rounded bg-gray-100 px-2 py-1 text-[10px] font-bold text-gray-500">{{ uiScreenshotCount }} 张</span>
        </div>
        <div class="space-y-5">
          <div
            v-for="caseEvidence in uiCaseEvidence"
            :key="caseEvidence.case_index"
            class="rounded-lg border border-gray-200 bg-gray-50 p-4"
          >
            <div class="mb-3 flex items-center justify-between gap-3">
              <div class="min-w-0">
                <div class="truncate text-sm font-bold text-gray-900">{{ caseEvidence.title }}</div>
                <div class="mt-1 text-[10px] font-mono text-gray-400">case {{ caseEvidence.case_index }}</div>
              </div>
              <span
                class="shrink-0 rounded px-2 py-0.5 text-[10px] font-bold"
                :class="caseEvidence.status === 'passed' ? 'bg-emerald-100 text-emerald-700' : caseEvidence.status === 'failed' ? 'bg-red-100 text-red-700' : 'bg-gray-100 text-gray-500'"
              >
                {{ caseEvidence.status }}
              </span>
            </div>
            <div v-if="caseEvidence.screenshots.length" class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              <div
                v-for="(shot, i) in caseEvidence.screenshots"
                :key="screenshotPath(shot)"
                class="cursor-pointer overflow-hidden rounded-lg border border-gray-200 bg-white transition-all hover:ring-2 hover:ring-blue-400"
                @click="openScreenshot(run.id, shot)"
              >
                <img
                  :src="screenshotDisplayUrl(run.id, shot)"
                  :alt="`${caseEvidence.title} ${screenshotLabel(shot, i)}`"
                  class="h-44 w-full object-cover"
                  loading="lazy"
                  @error="(e: any) => { e.target.style.display = 'none'; e.target.nextSibling && (e.target.nextSibling.style.display = 'flex') }"
                />
                <div class="hidden h-44 items-center justify-center text-xs text-gray-400">加载失败</div>
                <div class="border-t border-gray-100 p-3">
                  <div class="text-xs font-bold text-gray-700">{{ screenshotLabel(shot, i) }}</div>
                  <div class="mt-1 truncate text-[11px] text-gray-500">{{ screenshotDetail(shot) }}</div>
                  <div class="mt-1 truncate font-mono text-[10px] text-gray-400">{{ screenshotFilename(screenshotPath(shot)) }}</div>
                </div>
              </div>
            </div>
            <div v-else class="rounded-lg border border-dashed border-gray-200 bg-white px-4 py-8 text-center text-xs text-gray-400">
              此用例暂无截图证据
            </div>
          </div>
        </div>
      </div>
      <div v-else class="text-center py-12 text-gray-400 text-sm">暂无截图证据</div>
    </div>

    <!-- Tab: Tools -->
    <div v-if="activeTab === 'tools'" class="space-y-4">
      <div v-if="skillPlan.length" class="bg-white border border-gray-200 rounded-xl shadow-sm p-6">
        <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4 flex items-center gap-2">
          <Activity :size="14" /> Skill 调度
        </h3>
        <div class="grid gap-3 md:grid-cols-2">
          <div v-for="skill in skillPlan" :key="skill.name" class="rounded-lg border border-gray-100 bg-gray-50 p-4">
            <div class="flex items-center justify-between gap-3">
              <div class="min-w-0">
                <div class="truncate text-sm font-bold text-gray-900">{{ skill.name }}</div>
                <div class="mt-1 text-[10px] font-bold uppercase text-gray-400">{{ skill.layer }}</div>
              </div>
              <span class="shrink-0 rounded bg-blue-100 px-2 py-0.5 text-[10px] font-bold text-blue-700">
                {{ ensureList(skill.tools).length }} tools
              </span>
            </div>
            <p class="mt-2 text-xs leading-5 text-gray-600">{{ skill.description }}</p>
            <div v-if="ensureList(skill.tools).length" class="mt-3 flex flex-wrap gap-1.5">
              <span v-for="tool in ensureList(skill.tools)" :key="tool" class="rounded bg-white px-2 py-1 font-mono text-[10px] text-gray-500 border border-gray-100">
                {{ tool }}
              </span>
            </div>
          </div>
        </div>
      </div>

      <div v-if="toolCalls.length" class="bg-white border border-gray-200 rounded-xl shadow-sm p-6">
        <div class="mb-4 flex items-center justify-between">
          <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest flex items-center gap-2">
            <Terminal :size="14" /> 工具调用记录
          </h3>
          <span class="rounded bg-gray-100 px-2 py-1 text-[10px] font-bold text-gray-500">{{ toolCalls.length }} 次</span>
        </div>
        <div class="space-y-2">
          <div
            v-for="(call, i) in toolCalls.slice().reverse().slice(0, 120)"
            :key="`${call.tool}-${i}-${call.timestamp}`"
            class="rounded-lg border border-gray-100 bg-gray-50 p-3"
          >
            <div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div class="min-w-0">
                <div class="truncate font-mono text-xs font-bold text-gray-800">{{ call.tool }}</div>
                <div class="mt-1 flex flex-wrap items-center gap-2 text-[10px] text-gray-400">
                  <span>{{ call.layer }}</span>
                  <span v-if="call.case_title" class="truncate">{{ call.case_title }}</span>
                  <span v-if="call.elapsed_ms !== undefined">{{ call.elapsed_ms }}ms</span>
                </div>
              </div>
              <span class="shrink-0 rounded px-2 py-0.5 text-[10px] font-bold" :class="toolStatusClass(call.status)">
                {{ call.status }}
              </span>
            </div>
            <div class="mt-2 grid gap-2 md:grid-cols-2">
              <pre v-if="call.input" class="max-h-28 overflow-auto rounded bg-white p-2 text-[10px] font-mono text-gray-500 border border-gray-100">{{ formatPreview(call.input, 1000) }}</pre>
              <pre v-if="call.output" class="max-h-28 overflow-auto rounded bg-white p-2 text-[10px] font-mono text-gray-500 border border-gray-100">{{ formatPreview(call.output, 1000) }}</pre>
            </div>
          </div>
        </div>
      </div>

      <div v-if="!skillPlan.length && !toolCalls.length" class="text-center py-12 text-gray-400 text-sm">暂无工具调用记录</div>
    </div>

    <!-- Tab: Test Cases -->
    <div v-if="activeTab === 'cases'" class="space-y-4">
      <!-- API Cases -->
      <div v-if="run.api_cases?.length" class="bg-white border border-gray-200 rounded-xl shadow-sm p-6">
        <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4">API 测试用例 ({{ run.api_cases.length }})</h3>
        <div class="space-y-3">
          <div v-for="(tc, i) in run.api_cases" :key="i" class="p-4 bg-gray-50 rounded-lg border border-gray-100">
            <div class="flex items-center justify-between mb-2">
              <span class="font-bold text-sm text-gray-900">{{ tc.title }}</span>
              <div class="flex gap-2">
                <span class="px-2 py-0.5 bg-blue-100 text-blue-700 rounded text-[10px] font-bold">{{ tc.method }}</span>
                <span class="px-2 py-0.5 bg-gray-200 text-gray-600 rounded text-[10px] font-bold">{{ tc.category }}</span>
              </div>
            </div>
            <div v-if="tc.endpoint" class="text-xs font-mono text-gray-500 mb-1">{{ tc.endpoint }}</div>
            <ul v-if="ensureSteps(tc.steps).length" class="mt-2 space-y-1">
              <li v-for="(s, j) in ensureSteps(tc.steps)" :key="j" class="text-xs text-gray-500">{{ j + 1 }}. {{ s }}</li>
            </ul>
          </div>
        </div>
      </div>

      <!-- UI Cases -->
      <div v-if="run.ui_cases?.length" class="bg-white border border-gray-200 rounded-xl shadow-sm p-6">
        <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4">UI 测试用例 ({{ run.ui_cases.length }})</h3>
        <div class="space-y-3">
          <div v-for="(tc, i) in run.ui_cases" :key="i" class="p-4 bg-gray-50 rounded-lg border border-gray-100">
            <div class="flex items-center justify-between mb-2">
              <span class="font-bold text-sm text-gray-900">{{ tc.title }}</span>
              <span class="px-2 py-0.5 bg-gray-200 text-gray-600 rounded text-[10px] font-bold">{{ tc.category }}</span>
            </div>
            <ul v-if="ensureSteps(tc.steps).length" class="mt-2 space-y-1">
              <li v-for="(s, j) in ensureSteps(tc.steps)" :key="j" class="text-xs text-gray-500">{{ j + 1 }}. {{ s }}</li>
            </ul>
            <div v-if="tc.playwright_commands?.length" class="mt-2">
              <div class="text-[10px] font-bold text-gray-400 uppercase mb-1">Playwright 命令</div>
              <pre class="bg-gray-100 rounded p-2 text-[10px] font-mono text-gray-600 overflow-auto max-h-32">{{ tc.playwright_commands.join('\n') }}</pre>
            </div>
          </div>
        </div>
      </div>

      <div v-if="!run.api_cases?.length && !run.ui_cases?.length" class="text-center py-12 text-gray-400 text-sm">无测试用例</div>
    </div>

    <!-- Tab: Script -->
    <div v-if="activeTab === 'script'" class="space-y-4">
      <div class="bg-white border border-gray-200 rounded-xl shadow-sm p-6">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest flex items-center gap-2">
            <Terminal :size="14" /> 可复现 Playwright 脚本
          </h3>
          <div class="flex gap-2">
            <button
              @click="copyScript"
              class="px-3 py-1.5 bg-gray-100 hover:bg-gray-200 text-gray-600 rounded-lg text-xs font-bold transition-all"
            >
              复制
            </button>
            <button
              @click="downloadScript"
              class="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs font-bold transition-all"
            >
              下载
            </button>
          </div>
        </div>
        <div v-if="run.ui_reproducible_script || run.artifacts?.ui_reproducible_script">
          <pre class="bg-gray-900 text-green-400 rounded-xl p-4 text-xs font-mono overflow-auto max-h-[600px] whitespace-pre-wrap">{{ run.ui_reproducible_script || run.artifacts?.ui_reproducible_script }}</pre>
        </div>
        <div v-else class="text-center py-12 text-gray-400 text-sm">
          暂无脚本 — 运行 UI 测试后会自动生成可复现脚本
        </div>
      </div>

      <!-- Login Info -->
      <div v-if="run.setup_instructions || run.login_instructions" class="bg-white border border-gray-200 rounded-xl shadow-sm p-6">
        <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-3">测试前置说明</h3>
        <p class="text-sm text-gray-700 whitespace-pre-wrap">{{ run.setup_instructions || run.login_instructions }}</p>
      </div>

      <!-- Login Snapshot -->
      <div v-if="run.ui_login_snapshot" class="bg-white border border-gray-200 rounded-xl shadow-sm p-6">
        <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-3">登录后页面快照</h3>
        <pre class="bg-gray-50 rounded-lg p-3 text-[10px] font-mono text-gray-600 overflow-auto max-h-60 whitespace-pre-wrap">{{ run.ui_login_snapshot?.slice(0, 3000) }}</pre>
      </div>
    </div>

    <!-- Tab: Logs -->
    <div v-if="activeTab === 'logs'" class="space-y-4">
      <!-- Workflow Steps Log -->
      <div v-if="run.workflow_steps?.length" class="bg-white border border-gray-200 rounded-xl shadow-sm p-6">
        <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4 flex items-center gap-2">
          <Terminal :size="14" /> 工作流日志
        </h3>
        <div class="space-y-2">
          <div
            v-for="(step, idx) in run.workflow_steps"
            :key="idx"
            class="flex items-start gap-3 p-3 rounded-lg border font-mono text-xs"
            :class="step.status === 'done' ? 'bg-emerald-50 border-emerald-200' : step.status === 'failed' ? 'bg-red-50 border-red-200' : 'bg-gray-50 border-gray-200'"
          >
            <span class="text-gray-400 shrink-0 w-5 text-right">{{ idx + 1 }}</span>
            <span class="px-1.5 py-0.5 rounded text-[10px] font-bold shrink-0"
              :class="step.status === 'done' ? 'bg-emerald-200 text-emerald-800' : step.status === 'failed' ? 'bg-red-200 text-red-800' : 'bg-gray-200 text-gray-600'"
            >{{ step.node }}</span>
            <span class="text-gray-600 flex-1">{{ step.detail }}</span>
            <span class="text-[10px] shrink-0"
              :class="step.status === 'done' ? 'text-emerald-600' : step.status === 'failed' ? 'text-red-600' : 'text-gray-400'"
            >{{ step.status }}</span>
          </div>
        </div>
      </div>

      <!-- Execution Result Log -->
      <div v-if="run.execution_result" class="bg-white border border-gray-200 rounded-xl shadow-sm p-6">
        <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4 flex items-center gap-2">
          <Terminal :size="14" /> 执行日志
        </h3>
        <div class="grid grid-cols-3 gap-3 mb-4">
          <div class="p-2 bg-gray-50 rounded-lg border border-gray-100 text-center">
            <div class="text-[10px] font-bold text-gray-400 uppercase">退出码</div>
            <div class="text-sm font-bold mt-0.5" :class="run.execution_result.status_code === 0 ? 'text-emerald-600' : 'text-red-600'">
              {{ run.execution_result.status_code }}
            </div>
          </div>
          <div class="p-2 bg-gray-50 rounded-lg border border-gray-100 text-center">
            <div class="text-[10px] font-bold text-gray-400 uppercase">标准输出</div>
            <div class="text-sm font-bold text-gray-700 mt-0.5">{{ run.execution_result.stdout?.length || 0 }} chars</div>
          </div>
          <div class="p-2 bg-gray-50 rounded-lg border border-gray-100 text-center">
            <div class="text-[10px] font-bold text-gray-400 uppercase">错误输出</div>
            <div class="text-sm font-bold mt-0.5" :class="run.execution_result.stderr ? 'text-red-600' : 'text-emerald-600'">
              {{ run.execution_result.stderr?.length || 0 }} chars
            </div>
          </div>
        </div>
        <div v-if="run.execution_result.stdout" class="mb-3">
          <div class="text-[10px] font-bold text-gray-400 uppercase mb-1">标准输出 (stdout)</div>
          <pre class="bg-gray-900 text-gray-100 rounded-lg p-4 text-[11px] font-mono overflow-auto max-h-60 leading-relaxed">{{ run.execution_result.stdout }}</pre>
        </div>
        <div v-if="run.execution_result.stderr">
          <div class="text-[10px] font-bold text-red-400 uppercase mb-1">错误输出 (stderr)</div>
          <pre class="bg-gray-900 text-red-300 rounded-lg p-4 text-[11px] font-mono overflow-auto max-h-60 leading-relaxed">{{ run.execution_result.stderr }}</pre>
        </div>
        <div v-if="run.execution_result.trace_path" class="mt-3">
          <div class="text-[10px] font-bold text-gray-400 uppercase mb-1">Trace 路径</div>
          <div class="text-xs font-mono text-gray-600 bg-gray-50 rounded-lg p-3 border border-gray-100">{{ run.execution_result.trace_path }}</div>
        </div>
      </div>

      <!-- Generated Code -->
      <div v-if="run.generated_code" class="bg-white border border-gray-200 rounded-xl shadow-sm p-6">
        <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4 flex items-center gap-2">
          <FileText :size="14" /> 生成代码
        </h3>
        <pre class="bg-gray-900 text-gray-100 rounded-lg p-4 text-[11px] font-mono overflow-auto max-h-80 leading-relaxed">{{ run.generated_code }}</pre>
      </div>

      <!-- Raw Execution Log -->
      <div v-if="run.execution_log" class="bg-white border border-gray-200 rounded-xl shadow-sm p-6">
        <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4 flex items-center gap-2">
          <Terminal :size="14" /> 原始日志 (JSON)
        </h3>
        <pre class="bg-gray-900 text-gray-100 rounded-lg p-4 text-[11px] font-mono overflow-auto max-h-80 leading-relaxed">{{ (() => { try { return JSON.stringify(JSON.parse(run.execution_log), null, 2) } catch { return run.execution_log } })() }}</pre>
      </div>

      <div v-if="!run.workflow_steps?.length && !run.execution_result && !run.generated_code && !run.execution_log" class="text-center py-12 text-gray-400 text-sm">暂无日志</div>
    </div>
  </div>

  <div v-else-if="!loading" class="flex flex-col items-center justify-center py-24 text-center">
    <div class="text-gray-400 text-sm">运行不存在或加载失败</div>
    <button @click="router.push('/history')" class="mt-4 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-bold hover:bg-blue-700 transition-all">
      返回历史记录
    </button>
  </div>

  <!-- Lightbox Modal -->
  <Teleport to="body">
    <div v-if="lightboxUrl" class="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm" @click.self="closeLightbox">
      <div class="relative max-w-[90vw] max-h-[90vh]">
        <button @click="closeLightbox" class="absolute -top-10 right-0 text-white hover:text-gray-300 transition-colors">
          <XCircle :size="28" />
        </button>
        <img :src="lightboxUrl" class="max-w-full max-h-[85vh] rounded-lg shadow-2xl object-contain" @error="(e: any) => { e.target.alt = '截图加载失败' }" />
      </div>
    </div>
  </Teleport>
</template>
