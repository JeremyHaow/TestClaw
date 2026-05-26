<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api, { apiUrl } from '../lib/api'
import StatusBadge from '../components/StatusBadge.vue'
import LoadingSpinner from '../components/LoadingSpinner.vue'
import InlinePager from '../components/InlinePager.vue'
import StyledSelect from '../components/StyledSelect.vue'
import AgentCurrentActionCard from '../components/agent/AgentCurrentActionCard.vue'
import AgentEvidenceCard from '../components/agent/AgentEvidenceCard.vue'
import AgentInterventionDrawer from '../components/agent/AgentInterventionDrawer.vue'
import AgentRunSummary from '../components/agent/AgentRunSummary.vue'
import AgentTimeline from '../components/agent/AgentTimeline.vue'
import { useToast } from '../composables/useToast'
import { Activity, AlertTriangle, ArrowLeft, BookOpen, Camera, CheckCircle2, ChevronDown, ChevronRight, ClipboardCopy, Download, FileText, Loader2, Monitor, RotateCcw, Save, Terminal, XCircle, XCircleIcon, Zap } from 'lucide-vue-next'

const expandedApiRow = ref<number | null>(null)
const lightboxUrl = ref<string | null>(null)

function toggleApiRow(idx: number) {
  expandedApiRow.value = expandedApiRow.value === idx ? null : idx
}

function screenshotUrl(runId: string, filename: string) {
  const token = localStorage.getItem('testclaw_token')
  return apiUrl(`/runs/${runId}/screenshots/${encodeURIComponent(filename)}`, { token })
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
const triageExporting = ref<'markdown' | 'json' | null>(null)
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
  'triage_summary',
  'intervention_summary',
  'artifacts',
  'tool_registry',
  'skill_plan',
  'tool_calls',
  'tool_summary',
  'agent_mission_plan',
  'agent_roster',
  'agent_delegation_trace',
  'agent_react_trace',
  'agent_actions',
  'agent_action_observations',
  'agent_action_diagnostics',
  'evidence_evaluation',
  'agent_evaluations',
  'agent_attempt_history',
  'agent_replan_counts',
  'agent_replan_feedback',
  'agent_strategy_decision',
  'agent_tool_plan',
  'agent_strategy_diagnostics',
  'agent_case_diagnostics',
  'input_type',
  'source_input',
  'current_step',
  'progress_events',
  'cancelled',
  'cancelled_at',
  'last_error',
  'scene_hints',
  'auth_chain',
  'rag_context',
  'rag_retrieval',
  'setup_instructions',
  'setup_result',
  'login_instructions',
  'ui_login_snapshot',
  'login_playwright_commands',
  'ui_reproducible_script',
  'api_execution_policy',
  'allow_out_of_schema_api_cases',
  'api_path_prefix_rewrite',
  'auth_discovery',
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
  syncCaseAssetDrafts()
}

function hydrateRun(data: any) {
  run.value = data
  const parsed = parseExecutionLog(data.execution_log)
  applySnapshot(parsed)
  syncCaseAssetDrafts()
}

function connectSSE(runId: string) {
  disconnectSSE()
  const token = localStorage.getItem('testclaw_token')
  eventSource = new EventSource(apiUrl(`/runs/${runId}/stream`, { token }))
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

const interventionText = ref('')
const interventionCancelCurrent = ref(false)
const interventionSubmitting = ref(false)

async function submitInterventionRerun() {
  const supplemental = interventionText.value.trim()
  if (!supplemental) {
    toast.error('请先填写补充上下文')
    return
  }

  interventionSubmitting.value = true
  try {
    const { data } = await api.post(`/runs/${route.params.id}/interventions`, {
      supplemental_instructions: supplemental,
      cancel_current: interventionCancelCurrent.value,
    })
    interventionText.value = ''
    interventionCancelCurrent.value = false
    router.push(`/runs/${data.id}`)
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '发起辅助重跑失败')
  } finally {
    interventionSubmitting.value = false
  }
}

onMounted(() => loadRun(String(route.params.id)))
onUnmounted(() => disconnectSSE())
watch(() => route.params.id, (id) => {
  if (id) {
    interventionText.value = ''
    interventionCancelCurrent.value = false
    resetDetailPaging()
    loadRun(String(id))
  }
})

const activeTab = ref('report')
const apiResultPage = ref(1)
const uiCommandPage = ref(1)
const screenshotCasePage = ref(1)
const toolCallPage = ref(1)
const apiCaseAssetPage = ref(1)
const uiCaseAssetPage = ref(1)
const workflowStepPage = ref(1)
const progressEventPage = ref(1)
const triageSurfacePage = ref(1)
const triageFindingPage = ref(1)
const reportBugPage = ref(1)
const apiResultPageSize = 20
const uiCommandPageSize = 24
const screenshotCasePageSize = 3
const toolCallPageSize = 30
const caseAssetPageSize = 8
const workflowStepPageSize = 24
const progressEventPageSize = 30
const triageSurfacePageSize = 18
const triageFindingPageSize = 5
const reportBugPageSize = 5

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

function lastItem<T>(items: T[]): T | null {
  return items.length ? items[items.length - 1] : null
}

type IndexedItem<T> = {
  item: T
  index: number
}

function pageCount(total: number, pageSize: number) {
  return Math.max(1, Math.ceil(total / pageSize))
}

function indexedPage<T>(items: T[], page: number, pageSize: number): IndexedItem<T>[] {
  const start = Math.max(0, (page - 1) * pageSize)
  return items.slice(start, start + pageSize).map((item, offset) => ({
    item,
    index: start + offset,
  }))
}

function previewItems<T>(items: T[], limit: number): T[] {
  return items.slice(0, limit)
}

function clampPageRef(pageRef: { value: number }, total: number, pageSize: number) {
  const maxPage = pageCount(total, pageSize)
  if (pageRef.value > maxPage) pageRef.value = maxPage
  if (pageRef.value < 1) pageRef.value = 1
}

function resetDetailPaging() {
  apiResultPage.value = 1
  uiCommandPage.value = 1
  screenshotCasePage.value = 1
  toolCallPage.value = 1
  apiCaseAssetPage.value = 1
  uiCaseAssetPage.value = 1
  workflowStepPage.value = 1
  progressEventPage.value = 1
  triageSurfacePage.value = 1
  triageFindingPage.value = 1
  reportBugPage.value = 1
  expandedApiRow.value = null
}

type CaseAssetSource = 'api_cases' | 'ui_cases' | 'test_cases'
type CaseAssetStatus = 'accepted' | 'rejected'

type CaseAssetEntry = {
  key: string
  source: CaseAssetSource
  index: number
  raw: any
}

type CaseAssetDraft = CaseAssetEntry & {
  caseType: 'api' | 'ui'
  status: CaseAssetStatus
  title: string
  priority: string
  category: string
  stepsText: string
  expectedText: string
}

const caseAssetDrafts = ref<CaseAssetDraft[]>([])
const caseAssetSuiteName = ref('')
const caseAssetSaving = ref(false)
const caseAssetResult = ref<any>(null)

function ensureTextLines(value: any): string[] {
  if (Array.isArray(value)) {
    return value
      .map((item: any) => {
        if (typeof item === 'string') return item
        if (item && typeof item === 'object') return item.step || item.action || item.description || item.expected || JSON.stringify(item)
        return String(item || '')
      })
      .map((item: string) => item.trim())
      .filter(Boolean)
  }
  return ensureSteps(value)
}

function linesFromText(value: string): string[] {
  return String(value || '')
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean)
}

function generatedCaseEntries(): CaseAssetEntry[] {
  if (!run.value) return []
  const entries: CaseAssetEntry[] = []
  const runId = String(run.value.id || 'run')

  const apiCases = ensureList(run.value.api_cases)
  const uiCases = ensureList(run.value.ui_cases)
  apiCases.forEach((raw: any, index: number) => entries.push({ key: `${runId}:api_cases:${index}`, source: 'api_cases', index, raw }))
  uiCases.forEach((raw: any, index: number) => entries.push({ key: `${runId}:ui_cases:${index}`, source: 'ui_cases', index, raw }))

  if (!entries.length) {
    ensureList(run.value.test_cases).forEach((raw: any, index: number) => entries.push({ key: `${runId}:test_cases:${index}`, source: 'test_cases', index, raw }))
  }

  return entries
}

function requestTemplate(caseItem: any) {
  return caseItem?.request_template || caseItem?.test_data?.request_template || caseItem?.request || {}
}

function caseMethod(caseItem: any) {
  return caseItem?.method || requestTemplate(caseItem).method || ''
}

function caseEndpoint(caseItem: any) {
  const template = requestTemplate(caseItem)
  return caseItem?.endpoint || caseItem?.path || caseItem?.url || template.endpoint || template.path || template.url || ''
}

function caseCommands(caseItem: any): string[] {
  return ensureList(caseItem?.playwright_commands || caseItem?.test_data?.playwright_commands || caseItem?.commands).map(String)
}

function inferDraftCaseType(entry: CaseAssetEntry): 'api' | 'ui' {
  if (entry.source === 'api_cases') return 'api'
  if (entry.source === 'ui_cases') return 'ui'
  const declared = String(entry.raw?.case_type || entry.raw?.type || entry.raw?.category || '').toLowerCase()
  if (declared.includes('api') || caseMethod(entry.raw) || caseEndpoint(entry.raw)) return 'api'
  return 'ui'
}

function draftFromGeneratedCase(entry: CaseAssetEntry): CaseAssetDraft {
  const caseType = inferDraftCaseType(entry)
  const raw = entry.raw || {}
  return {
    ...entry,
    caseType,
    status: 'accepted',
    title: String(raw.title || raw.name || raw.case_title || `${caseType.toUpperCase()} Case ${entry.index + 1}`),
    priority: String(raw.priority || 'P2').toUpperCase(),
    category: String(raw.category || raw.case_type || caseType),
    stepsText: ensureTextLines(raw.steps || raw.actions).join('\n'),
    expectedText: ensureTextLines(raw.expected || raw.expected_result || raw.expected_results || raw.assertions).join('\n'),
  }
}

function syncCaseAssetDrafts() {
  if (!run.value) {
    caseAssetDrafts.value = []
    caseAssetResult.value = null
    return
  }

  const existing = new Map(caseAssetDrafts.value.map((draft) => [draft.key, draft]))
  caseAssetDrafts.value = generatedCaseEntries().map((entry) => existing.get(entry.key) || draftFromGeneratedCase(entry))
}

function setCaseAssetStatus(draft: CaseAssetDraft, status: CaseAssetStatus) {
  draft.status = status
}

const hasGeneratedCaseAssets = computed(() => caseAssetDrafts.value.length > 0)
const acceptedCaseAssetCount = computed(() => caseAssetDrafts.value.filter((draft) => draft.status === 'accepted').length)
const apiCaseAssetDrafts = computed(() => caseAssetDrafts.value.filter((draft) => draft.caseType === 'api'))
const uiCaseAssetDrafts = computed(() => caseAssetDrafts.value.filter((draft) => draft.caseType === 'ui'))
const pagedApiCaseAssetDrafts = computed(() => indexedPage(apiCaseAssetDrafts.value, apiCaseAssetPage.value, caseAssetPageSize))
const pagedUiCaseAssetDrafts = computed(() => indexedPage(uiCaseAssetDrafts.value, uiCaseAssetPage.value, caseAssetPageSize))

async function saveAcceptedCaseAssets() {
  if (!run.value) return
  const accepted = caseAssetDrafts.value.filter((draft) => draft.status === 'accepted')
  if (!accepted.length) {
    toast.error('没有已接受的测试用例')
    return
  }

  caseAssetSaving.value = true
  try {
    const { data } = await api.post(`/runs/${run.value.id}/case-assets`, {
      suite_name: caseAssetSuiteName.value.trim() || undefined,
      cases: accepted.map((draft) => ({
        source: draft.source,
        index: draft.index,
        case: {
          title: draft.title,
          priority: draft.priority,
          category: draft.category,
          steps: linesFromText(draft.stepsText),
          expected: linesFromText(draft.expectedText),
        },
      })),
    })
    caseAssetResult.value = data
    toast.success(`已保存 ${data.total} 个用例到套件`)
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '保存用例资产失败')
  } finally {
    caseAssetSaving.value = false
  }
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

const workflowSteps = computed<any[]>(() => ensureList(run.value?.workflow_steps))
const progressEvents = computed<any[]>(() => ensureList(run.value?.progress_events))
const pagedWorkflowSteps = computed(() => indexedPage(workflowSteps.value, workflowStepPage.value, workflowStepPageSize))
const pagedProgressEvents = computed(() => indexedPage(progressEvents.value, progressEventPage.value, progressEventPageSize))
const currentStep = computed(() => {
  return run.value?.current_step || lastItem(progressEvents.value) || lastItem(workflowSteps.value)
})
const isActiveRun = computed(() => Boolean(run.value && isActiveStatus(run.value.status)))
const isFailedRun = computed(() => ['failed', 'bug_found'].includes(String(run.value?.status || '').toLowerCase()))
const apiSummary = computed(() => run.value?.api_execution_result || null)
const uiSummary = computed(() => run.value?.ui_execution_result || null)
const apiResults = computed(() => ensureList(run.value?.api_execution_result?.results))
const pagedApiResults = computed(() => indexedPage(apiResults.value, apiResultPage.value, apiResultPageSize))
const uiCommandRows = computed(() => ensureList(run.value?.ui_execution_result?.commands))
const pagedUiCommandRows = computed(() => indexedPage(uiCommandRows.value, uiCommandPage.value, uiCommandPageSize))
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
const uiCaseEvidencePreview = computed(() => previewItems(uiCaseEvidence.value, 3))
const pagedScreenshotCases = computed(() => indexedPage(uiCaseEvidence.value, screenshotCasePage.value, screenshotCasePageSize))
const toolCalls = computed(() => ensureList(run.value?.tool_calls || run.value?.artifacts?.tool_calls))
const recentToolCalls = computed(() => toolCalls.value.slice().reverse())
const pagedToolCalls = computed(() => indexedPage(recentToolCalls.value, toolCallPage.value, toolCallPageSize))
const skillPlan = computed(() => ensureList(run.value?.skill_plan || run.value?.final_report?.skill_plan))
const agentActionObservations = computed(() => ensureList(run.value?.agent_action_observations))
const toolSummary = computed(() => run.value?.tool_summary || run.value?.final_report?.tool_summary || run.value?.artifacts?.tool_summary || null)
const agentEvaluations = computed<any[]>(() => ensureList(run.value?.agent_evaluations || run.value?.final_report?.agent_diagnostics?.evaluations))
const latestAgentEvaluation = computed(() => run.value?.evidence_evaluation || run.value?.final_report?.agent_diagnostics?.latest_evaluation || lastItem(agentEvaluations.value))
const agentReplanCounts = computed(() => run.value?.agent_replan_counts || run.value?.final_report?.agent_diagnostics?.replan_counts || {})
const agentReplanTotal = computed(() => Object.values(agentReplanCounts.value || {}).reduce((total: number, value: any) => total + Number(value || 0), 0))
const ragRetrieval = computed(() => run.value?.rag_retrieval || null)
const ragSources = computed(() => ensureList(ragRetrieval.value?.sources))
const ragDisplayStatus = computed(() => ragRetrieval.value?.status || 'metadata_unavailable')
const ragDisplayMode = computed(() => ragRetrieval.value?.mode || 'unavailable')
const ragBadgeLabel = computed(() => {
  const mode = String(ragDisplayMode.value || '').toLowerCase()
  const status = String(ragDisplayStatus.value || '').toLowerCase()
  if (!ragRetrieval.value && run.value?.rag_context) return 'RAG context-only'
  if (mode === 'vector') {
    if (status === 'matched') return `Vector RAG ${ragSources.value.length} source(s)`
    if (status === 'empty') return 'Vector RAG no match'
    return `Vector RAG ${ragDisplayStatus.value}`
  }
  if (mode === 'lexical_fallback') return `Lexical fallback ${ragSources.value.length} source(s)`
  if (status === 'metadata_unavailable') return 'RAG metadata unavailable'
  return `RAG ${ragDisplayStatus.value}`
})
const ragBadgeClass = computed(() => {
  const mode = String(ragDisplayMode.value || '').toLowerCase()
  const status = String(ragDisplayStatus.value || '').toLowerCase()
  if (mode === 'vector' && status === 'matched') return 'bg-emerald-50 text-emerald-700 border-emerald-100'
  if (mode === 'vector') return 'bg-blue-50 text-blue-700 border-blue-100'
  if (mode === 'lexical_fallback') return 'bg-amber-50 text-amber-700 border-amber-100'
  return 'bg-gray-50 text-gray-600 border-gray-200'
})
const ragPanelTitle = computed(() => {
  const mode = String(ragDisplayMode.value || '').toLowerCase()
  if (mode === 'vector') return 'Vector RAG Retrieval'
  if (mode === 'lexical_fallback') return 'Lexical Fallback Retrieval'
  if (mode === 'skipped') return 'RAG Retrieval Skipped'
  if (mode === 'error') return 'RAG Retrieval Error'
  return 'RAG Retrieval Unavailable'
})
const ragDisplayEffect = computed(() => {
  if (ragRetrieval.value?.effect) return ragRetrieval.value.effect
  if (run.value?.rag_context) return 'RAG context is present, but retrieval metadata is unavailable; this is not labeled as vector RAG.'
  return 'RAG retrieval metadata is unavailable.'
})
function ragModeLabel(mode: string) {
  const value = String(mode || '').toLowerCase()
  if (value === 'vector') return 'Vector'
  if (value === 'lexical_fallback') return 'Lexical fallback'
  if (value === 'unavailable') return 'Unavailable'
  if (value === 'skipped') return 'Skipped'
  if (value === 'error') return 'Error'
  return value || 'Unknown'
}
function ragStatusClass(status: string) {
  const value = String(status || '').toLowerCase()
  if (value === 'matched') return 'bg-emerald-100 text-emerald-700'
  if (['fallback_lexical', 'empty', 'skipped', 'unavailable', 'metadata_unavailable'].includes(value)) return 'bg-amber-100 text-amber-700'
  if (value === 'error') return 'bg-red-100 text-red-700'
  return 'bg-gray-100 text-gray-600'
}
function ragEffectClass(mode: string) {
  const value = String(mode || '').toLowerCase()
  if (value === 'vector') return 'border-emerald-100 bg-emerald-50 text-emerald-900'
  if (['lexical_fallback', 'unavailable', 'skipped'].includes(value)) return 'border-amber-100 bg-amber-50 text-amber-900'
  if (value === 'error') return 'border-red-100 bg-red-50 text-red-900'
  return 'border-gray-200 bg-gray-50 text-gray-700'
}
function ragQueryClass(mode: string) {
  const value = String(mode || '').toLowerCase()
  if (value === 'vector') return 'text-emerald-700'
  if (['lexical_fallback', 'unavailable', 'skipped'].includes(value)) return 'text-amber-700'
  if (value === 'error') return 'text-red-700'
  return 'text-gray-500'
}
function ragScoreLabel(source: any) {
  return source?.mode === 'vector' ? `sim ${source.score}` : `score ${source?.score ?? 0}`
}
const hasRagSurface = computed(() => Boolean(ragRetrieval.value || run.value?.rag_context))
const hasAgentEvaluationSurface = computed(() => Boolean(latestAgentEvaluation.value || agentEvaluations.value.length))
const hasToolSurface = computed(() => toolCalls.value.length > 0 || skillPlan.value.length > 0 || agentActionObservations.value.length > 0 || Boolean(toolSummary.value) || hasRagSurface.value || hasAgentEvaluationSurface.value)
const triageSummary = computed(() => run.value?.triage_summary || null)
const triageAffectedSurfaces = computed(() => ensureList(triageSummary.value?.affected_surfaces))
const pagedTriageAffectedSurfaces = computed(() => indexedPage(triageAffectedSurfaces.value, triageSurfacePage.value, triageSurfacePageSize))
const triageBlockingFindings = computed(() => ensureList(triageSummary.value?.blocking_findings))
const pagedTriageBlockingFindings = computed(() => indexedPage(triageBlockingFindings.value, triageFindingPage.value, triageFindingPageSize))
const reportBugs = computed(() => ensureList(run.value?.final_report?.bugs_found))
const pagedReportBugs = computed(() => indexedPage(reportBugs.value, reportBugPage.value, reportBugPageSize))
const interventionSummary = computed(() => run.value?.intervention_summary || null)
const showInterventionPanel = computed(() => Boolean(interventionSummary.value?.useful))
const interventionSuggestedInputs = computed(() => ensureList(interventionSummary.value?.suggested_inputs))
function toolStatusClass(status: string) {
  const value = String(status || '').toLowerCase()
  if (['success', 'passed', 'done'].includes(value)) return 'bg-emerald-100 text-emerald-700'
  if (value === 'skipped') return 'bg-amber-100 text-amber-700'
  if (['failed', 'error'].includes(value)) return 'bg-red-100 text-red-700'
  return 'bg-gray-100 text-gray-600'
}
function agentActionLabel(action: string) {
  const value = String(action || '').toLowerCase()
  if (value === 'continue_to_ui') return '继续 UI'
  if (value === 'replan_api') return '重规划 API'
  if (value === 'replan_ui') return '重规划 UI'
  if (value === 'report') return '生成报告'
  return value || '等待判断'
}
function agentEvidenceClass(evaluation: any) {
  if (!evaluation) return 'border-gray-200 bg-gray-50 text-gray-600'
  if (evaluation.sufficient_evidence) return 'border-emerald-100 bg-emerald-50 text-emerald-700'
  if (String(evaluation.next_action || '').startsWith('replan')) return 'border-blue-100 bg-blue-50 text-blue-700'
  return 'border-amber-100 bg-amber-50 text-amber-700'
}
function triageRiskClass(level: string) {
  const value = String(level || '').toLowerCase()
  if (value === 'high') return 'bg-red-50 border-red-200 text-red-700'
  if (value === 'medium') return 'bg-amber-50 border-amber-200 text-amber-700'
  if (value === 'low') return 'bg-emerald-50 border-emerald-200 text-emerald-700'
  return 'bg-gray-50 border-gray-200 text-gray-600'
}
function triageSeverityClass(severity: string) {
  const value = String(severity || '').toUpperCase()
  if (['CRITICAL', 'HIGH'].includes(value)) return 'bg-red-100 text-red-700'
  if (value === 'MEDIUM') return 'bg-amber-100 text-amber-700'
  return 'bg-gray-100 text-gray-600'
}
function triageConfidenceClass(level: string) {
  const value = String(level || '').toLowerCase()
  if (value === 'high') return 'bg-blue-100 text-blue-700'
  if (value === 'medium') return 'bg-amber-100 text-amber-700'
  return 'bg-gray-100 text-gray-600'
}
function formatTriageSummaryForCopy(summary: any) {
  const lines = [
    `发布风险：${summary?.release_risk?.label || '未知'} (${summary?.release_risk?.level || 'unknown'})`,
    `阻断项：${summary?.blocking_count || 0}`,
    `证据数：${summary?.evidence?.count || 0}`,
    `置信度：${summary?.confidence?.level || 'unknown'}`,
    '',
    summary?.summary || '',
  ].filter((line) => line !== undefined)

  const findings = ensureList(summary?.blocking_findings).slice(0, 5)
  if (findings.length) {
    lines.push('', '阻断发现：')
    findings.forEach((finding: any, index: number) => {
      lines.push(`${index + 1}. [${finding.severity || 'MEDIUM'}] ${finding.title || 'Finding'}`)
      if (finding.surface) lines.push(`   影响面：${finding.surface}`)
      if (finding.description) lines.push(`   说明：${finding.description}`)
      if (finding.next_action) lines.push(`   下一步：${finding.next_action}`)
    })
  }

  const actions = ensureList(summary?.recommended_next_actions).slice(0, 5)
  if (actions.length) {
    lines.push('', '建议动作：')
    actions.forEach((action: string, index: number) => lines.push(`${index + 1}. ${action}`))
  }

  const steps = ensureList(summary?.reproduction?.steps).slice(0, 5)
  if (steps.length) {
    lines.push('', '复现路径：')
    steps.forEach((step: string, index: number) => lines.push(`${index + 1}. ${step}`))
  }

  return lines.filter(Boolean).join('\n')
}
async function copyTriageSummary() {
  if (!triageSummary.value) return
  try {
    await navigator.clipboard.writeText(formatTriageSummaryForCopy(triageSummary.value))
    toast.success('分诊摘要已复制')
  } catch {
    toast.error('复制分诊摘要失败')
  }
}
async function downloadTriageExport(format: 'markdown' | 'json') {
  if (!run.value?.id) return
  triageExporting.value = format
  try {
    const { data } = await api.get(`/runs/${run.value.id}/triage-export`, {
      params: { format },
      responseType: 'blob',
    })
    const extension = format === 'markdown' ? 'md' : 'json'
    const mimeType = format === 'markdown' ? 'text/markdown' : 'application/json'
    const blob = data instanceof Blob ? data : new Blob([data], { type: mimeType })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `testclaw-triage-${run.value.id.slice(0, 8)}.${extension}`
    a.click()
    URL.revokeObjectURL(url)
    toast.success(format === 'markdown' ? 'Markdown 分诊已下载' : 'JSON 分诊已下载')
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '下载分诊导出失败')
  } finally {
    triageExporting.value = null
  }
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
const currentStepLabel = computed(() => {
  const step = currentStep.value
  if (!step) return '初始化'
  if (typeof step === 'string') return step
  return String(step.node || step.name || step.stage || step.detail || '初始化')
})
const generatedCaseCount = computed(() => {
  return ensureList(run.value?.api_cases).length + ensureList(run.value?.ui_cases).length + ensureList(run.value?.test_cases).length
})
const executedCount = computed(() => Number(apiTotalCount.value || 0) + Number(uiTotalCount.value || 0))
const passedCount = computed(() => Number(apiPassedCount.value || 0) + Number(uiPassedCount.value || 0))
const failedCount = computed(() => {
  const apiFailed = Number(apiSummary.value?.failed ?? Math.max(0, apiTotalCount.value - apiPassedCount.value))
  const uiFailed = Number(uiSummary.value?.failed ?? Math.max(0, uiTotalCount.value - uiPassedCount.value))
  return apiFailed + uiFailed
})
const skippedCount = computed(() => Number(apiSkippedCount.value || 0) + Number(uiSummary.value?.skipped || 0))
const evidenceCount = computed(() => {
  return Number(triageSummary.value?.evidence?.count || 0) + uiScreenshotCount.value + toolCalls.value.length
})
const currentBlockingText = computed(() => {
  return interventionSummary.value?.reason || run.value?.last_error || latestAgentEvaluation.value?.reason || ''
})
const releaseRiskLabel = computed(() => triageSummary.value?.release_risk?.label || triageSummary.value?.release_risk?.level || '等待结果')

watch(visibleTabs, (tabs) => {
  if (!tabs.some((tab) => tab.key === activeTab.value)) {
    activeTab.value = 'report'
  }
})

watch(
  [
    apiResults,
    uiCommandRows,
    uiCaseEvidence,
    recentToolCalls,
    apiCaseAssetDrafts,
    uiCaseAssetDrafts,
    workflowSteps,
    progressEvents,
    triageAffectedSurfaces,
    triageBlockingFindings,
    reportBugs,
  ],
  () => {
    clampPageRef(apiResultPage, apiResults.value.length, apiResultPageSize)
    clampPageRef(uiCommandPage, uiCommandRows.value.length, uiCommandPageSize)
    clampPageRef(screenshotCasePage, uiCaseEvidence.value.length, screenshotCasePageSize)
    clampPageRef(toolCallPage, recentToolCalls.value.length, toolCallPageSize)
    clampPageRef(apiCaseAssetPage, apiCaseAssetDrafts.value.length, caseAssetPageSize)
    clampPageRef(uiCaseAssetPage, uiCaseAssetDrafts.value.length, caseAssetPageSize)
    clampPageRef(workflowStepPage, workflowSteps.value.length, workflowStepPageSize)
    clampPageRef(progressEventPage, progressEvents.value.length, progressEventPageSize)
    clampPageRef(triageSurfacePage, triageAffectedSurfaces.value.length, triageSurfacePageSize)
    clampPageRef(triageFindingPage, triageBlockingFindings.value.length, triageFindingPageSize)
    clampPageRef(reportBugPage, reportBugs.value.length, reportBugPageSize)
  },
)
</script>

<template>
  <LoadingSpinner v-if="loading && !run" text="加载运行详情..." />

  <div class="mx-auto max-w-7xl space-y-5 pb-10" v-else-if="run">
    <!-- Header -->
    <div class="flex flex-col gap-4 border-b border-gray-200/80 pb-5 lg:flex-row lg:items-center">
      <button
        type="button"
        aria-label="返回运行历史"
        title="返回运行历史"
        @click="router.push('/history')"
        class="w-fit rounded-lg border border-gray-200 bg-white p-2 text-gray-500 transition-all hover:bg-gray-50 hover:text-gray-900"
      >
        <ArrowLeft :size="20" />
      </button>
      <div class="min-w-0 flex-1">
        <div class="tc-page-kicker">Agent Cockpit</div>
        <h2 class="mt-1 truncate text-xl font-semibold tracking-tight text-gray-950">{{ run.objective }}</h2>
        <p class="mt-1 truncate font-mono text-xs text-gray-500">{{ run.target_url }}</p>
      </div>
      <div class="flex shrink-0 flex-wrap items-center gap-2">
        <button
          v-if="['queued','running'].includes(run.status)"
          @click="cancelRun"
          class="flex items-center gap-1.5 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs font-bold text-red-600 transition-all hover:bg-red-100"
        >
          <XCircle :size="14" /> 取消
        </button>
        <button
          v-if="['succeeded','failed','bug_found'].includes(run.status)"
          @click="rerunRun"
          class="flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-bold text-gray-700 transition-all hover:border-gray-300 hover:bg-gray-50"
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
      <span v-if="hasRagSurface" class="px-2 py-1 rounded-lg text-[10px] font-bold border" :class="ragBadgeClass">
        {{ ragBadgeLabel }}
      </span>
      <span v-if="hasAgentEvaluationSurface" class="px-2 py-1 rounded-lg text-[10px] font-bold border" :class="agentEvidenceClass(latestAgentEvaluation)">
        证据评估 {{ agentActionLabel(latestAgentEvaluation?.next_action) }}
      </span>
      <span v-if="run.created_at" class="px-2 py-1 bg-gray-50 text-gray-500 rounded-lg text-[10px] font-mono">
        {{ new Date(run.created_at).toLocaleString('zh-CN') }}
      </span>
    </div>

    <AgentRunSummary
      :status="run.status"
      :progress-percent="progressPercent"
      :current-step="currentStepLabel"
      :blocking-text="currentBlockingText"
      :generated-cases="generatedCaseCount"
      :executed-count="executedCount"
      :passed-count="passedCount"
      :failed-count="failedCount"
      :skipped-count="skippedCount"
      :evidence-count="evidenceCount"
    >
      <template #badges>
        <span class="rounded-lg border border-gray-200 bg-gray-50 px-2 py-1 text-[10px] font-bold text-gray-500">测试计划</span>
        <span class="rounded-lg border border-gray-200 bg-gray-50 px-2 py-1 text-[10px] font-bold text-gray-500">测试用例</span>
        <span class="rounded-lg border border-gray-200 bg-gray-50 px-2 py-1 text-[10px] font-bold text-gray-500">执行日志</span>
        <span class="rounded-lg border border-gray-200 bg-gray-50 px-2 py-1 text-[10px] font-bold text-gray-500">证据</span>
        <span class="rounded-lg border border-gray-200 bg-gray-50 px-2 py-1 text-[10px] font-bold text-gray-500">报告</span>
      </template>
    </AgentRunSummary>

    <!-- Execution Cockpit -->
    <section class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <AgentCurrentActionCard
        :status="run.status"
        :title="statusTitle"
        :description="statusDescription"
        :current-step="currentStepLabel"
        :progress-percent="progressPercent"
        :active="isActiveRun"
        :failed="isFailedRun"
        :cancellable="isActiveRun"
        :show-notice="isActiveRun || isFailedRun || run.status === 'cancelled'"
        @cancel="cancelRun"
      >
        <template #notice>
          <template v-if="isActiveRun">运行中会持续刷新智能体动作、证据和实时日志。</template>
          <template v-else-if="isFailedRun">{{ run.last_error || '执行失败，请查看下方日志定位原因。' }}</template>
          <template v-else>运行已取消，历史日志仍保留在页面中。</template>
        </template>

        <div
          v-if="legacyApiMisreport"
          class="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-xs leading-5 text-amber-800"
        >
          这是旧版 API 执行策略产生的历史结果：当时会请求 /dev-api 写入接口并把缺少鉴权的正向断言计为失败。请使用当前安全只读策略重跑，新的运行会跳过写入接口并显示跳过原因。
        </div>
      </AgentCurrentActionCard>

      <div class="grid gap-0 lg:grid-cols-[minmax(0,1fr)_minmax(320px,0.75fr)] lg:items-stretch">
        <div class="min-h-0 border-b border-gray-100 p-4 lg:border-b-0 lg:border-r">
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

          <div
            v-if="latestAgentEvaluation"
            class="mt-3 rounded-lg border p-4 text-sm"
            :class="agentEvidenceClass(latestAgentEvaluation)"
          >
            <div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <Activity :size="15" />
                  <span class="text-[10px] font-bold uppercase tracking-widest opacity-70">证据评估</span>
                  <span class="rounded bg-white/70 px-2 py-0.5 text-[10px] font-bold">
                    {{ latestAgentEvaluation.stage || 'agent' }}
                  </span>
                </div>
                <p class="mt-2 leading-6">{{ latestAgentEvaluation.reason || '智能体已检查本轮证据质量。' }}</p>
                <div v-if="ensureList(latestAgentEvaluation.diagnostics).length" class="mt-2 flex flex-wrap gap-2">
                  <span
                    v-for="(item, index) in ensureList(latestAgentEvaluation.diagnostics).slice(0, 3)"
                    :key="`agent-diagnostic-${index}`"
                    class="rounded border border-current/10 bg-white/60 px-2 py-1 text-[11px]"
                  >
                    {{ item }}
                  </span>
                </div>
              </div>
              <div class="shrink-0 rounded-lg border border-current/10 bg-white/70 px-3 py-2 text-xs font-bold">
                {{ agentActionLabel(latestAgentEvaluation.next_action) }}
                <span v-if="agentReplanTotal"> · {{ agentReplanTotal }} 次重规划</span>
              </div>
            </div>
          </div>

          <AgentTimeline :items="activityFeed" :active="isActiveRun" />
        </div>

        <div class="min-h-0 overflow-hidden bg-gray-950 p-4 text-gray-100 lg:relative lg:p-0">
          <div class="flex min-h-0 flex-col lg:absolute lg:inset-4">
            <div class="mb-3 flex items-center justify-between">
              <h3 class="flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-gray-400">
                <Terminal :size="14" /> 实时日志
              </h3>
              <span v-if="isActiveRun" class="flex items-center gap-1.5 text-[10px] font-bold text-amber-300">
                <span class="h-1.5 w-1.5 rounded-full bg-amber-400"></span>
                LIVE
              </span>
            </div>
            <pre
              v-if="liveRawLog || run.execution_log"
              class="min-h-48 max-h-[420px] overflow-auto whitespace-pre-wrap rounded-lg border border-white/10 bg-black/30 p-4 text-[11px] leading-relaxed text-gray-100 lg:min-h-0 lg:max-h-none lg:flex-1 lg:basis-0"
            >{{ liveRawLog ? formatPreview(liveRawLog) : formatPreview(run.execution_log) }}</pre>
            <div v-else class="flex min-h-48 items-center justify-center rounded-lg border border-dashed border-white/10 bg-black/20 px-4 text-center text-xs text-gray-500 lg:min-h-0 lg:flex-1 lg:basis-0">
              <span v-if="isActiveRun">等待第一条执行日志...</span>
              <span v-else>暂无实时日志</span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- Human Intervention -->
    <AgentInterventionDrawer
      v-if="showInterventionPanel"
      v-model="interventionText"
      v-model:cancel-current="interventionCancelCurrent"
      :summary="interventionSummary"
      :suggested-inputs="interventionSuggestedInputs"
      :submitting="interventionSubmitting"
      @submit="submitInterventionRerun"
    />

    <!-- Triage Summary -->
    <AgentEvidenceCard
      v-if="triageSummary"
      :evidence-count="evidenceCount"
      :finding-count="triageBlockingFindings.length"
      :risk-label="releaseRiskLabel"
    >
    <div class="bg-white border border-gray-200 rounded-lg shadow-sm p-4">
      <div class="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest">分诊摘要</h3>
          <p class="mt-2 text-sm leading-6 text-gray-700">{{ triageSummary.summary }}</p>
        </div>
        <div class="flex shrink-0 flex-wrap gap-2">
          <button
            @click="copyTriageSummary"
            class="rounded-lg border border-gray-200 bg-gray-50 px-3 py-1.5 text-xs font-bold text-gray-600 transition-all hover:bg-gray-100 flex items-center gap-1.5"
          >
            <ClipboardCopy :size="14" /> 复制摘要
          </button>
          <button
            @click="downloadTriageExport('markdown')"
            :disabled="triageExporting === 'markdown'"
            class="rounded-lg border border-blue-200 bg-blue-50 px-3 py-1.5 text-xs font-bold text-blue-700 transition-all hover:bg-blue-100 disabled:cursor-not-allowed disabled:opacity-60 flex items-center gap-1.5"
          >
            <Download :size="14" /> Markdown
          </button>
          <button
            @click="downloadTriageExport('json')"
            :disabled="triageExporting === 'json'"
            class="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-bold text-gray-700 transition-all hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60 flex items-center gap-1.5"
          >
            <Download :size="14" /> JSON
          </button>
        </div>
      </div>

      <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div class="rounded-lg border p-3" :class="triageRiskClass(triageSummary.release_risk?.level)">
          <div class="text-[10px] font-bold uppercase tracking-widest opacity-70">发布风险</div>
          <div class="mt-1 text-sm font-bold">{{ triageSummary.release_risk?.label || '等待结果' }}</div>
          <div class="mt-1 text-[11px] leading-4 opacity-80">{{ triageSummary.release_risk?.rationale }}</div>
        </div>
        <div class="rounded-lg border border-gray-100 bg-gray-50 p-3">
          <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">阻断项</div>
          <div class="mt-1 text-lg font-bold" :class="triageSummary.blocking_count ? 'text-red-600' : 'text-emerald-600'">{{ triageSummary.blocking_count || 0 }}</div>
          <div class="mt-1 text-[11px] text-gray-500">{{ triageAffectedSurfaces.length }} 个影响面</div>
        </div>
        <div class="rounded-lg border border-gray-100 bg-gray-50 p-3">
          <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">证据</div>
          <div class="mt-1 text-lg font-bold text-gray-900">{{ triageSummary.evidence?.count || 0 }}</div>
          <div class="mt-1 text-[11px] text-gray-500">
            API {{ triageSummary.evidence?.api_result_count || 0 }} · 截图 {{ triageSummary.evidence?.screenshot_count || 0 }} · 工具 {{ triageSummary.evidence?.tool_call_count || 0 }}
          </div>
        </div>
        <div class="rounded-lg border border-gray-100 bg-gray-50 p-3">
          <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">置信度</div>
          <div class="mt-2">
            <span class="rounded px-2 py-0.5 text-[10px] font-bold" :class="triageConfidenceClass(triageSummary.confidence?.level)">
              {{ triageSummary.confidence?.level || 'unknown' }}
            </span>
          </div>
          <div class="mt-2 text-[11px] leading-4 text-gray-500">{{ triageSummary.confidence?.rationale }}</div>
        </div>
      </div>

      <div v-if="triageAffectedSurfaces.length" class="mt-4">
        <div class="mb-2 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">影响面</div>
          <InlinePager v-model:page="triageSurfacePage" :page-size="triageSurfacePageSize" :total="triageAffectedSurfaces.length" label="影响面" />
        </div>
        <div class="flex max-h-28 flex-wrap gap-2 overflow-auto pr-1">
          <span
            v-for="{ item: surface, index } in pagedTriageAffectedSurfaces"
            :key="`${surface.type}-${surface.name}-${index}`"
            class="rounded-lg border border-gray-200 bg-gray-50 px-2.5 py-1 text-[11px] font-mono text-gray-600"
          >
            {{ surface.name }}
          </span>
        </div>
      </div>

      <div v-if="triageBlockingFindings.length" class="mt-4">
        <div class="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div class="text-[10px] font-bold uppercase tracking-widest text-red-400">阻断发现</div>
          <InlinePager v-model:page="triageFindingPage" :page-size="triageFindingPageSize" :total="triageBlockingFindings.length" label="发现" />
        </div>
        <div class="max-h-[520px] space-y-3 overflow-auto pr-1">
          <div
            v-for="{ item: finding, index } in pagedTriageBlockingFindings"
            :key="`${finding.source}-${finding.surface}-${index}`"
            class="rounded-lg border border-red-100 bg-red-50 p-4"
          >
            <div class="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
              <div class="min-w-0">
                <div class="text-sm font-bold text-red-950">{{ finding.title }}</div>
                <div v-if="finding.surface" class="mt-1 truncate font-mono text-[11px] text-red-700">{{ finding.surface }}</div>
              </div>
              <div class="flex shrink-0 flex-wrap gap-1.5">
                <span class="rounded px-2 py-0.5 text-[10px] font-bold" :class="triageSeverityClass(finding.severity)">{{ finding.severity || 'MEDIUM' }}</span>
                <span class="rounded bg-white px-2 py-0.5 text-[10px] font-bold text-red-600">{{ finding.confidence || 'medium' }}</span>
              </div>
            </div>
            <p v-if="finding.description" class="mt-2 text-xs leading-5 text-red-800">{{ finding.description }}</p>
            <div v-if="ensureList(finding.evidence).length" class="mt-3 flex flex-wrap gap-2">
              <span
                v-for="(evidence, evidenceIndex) in ensureList(finding.evidence)"
                :key="`${finding.title}-evidence-${evidenceIndex}`"
                class="rounded border border-red-100 bg-white px-2 py-1 text-[11px] text-red-700"
              >
                {{ evidence.summary || evidence.kind }}
              </span>
            </div>
            <div v-if="ensureList(finding.reproduction_steps).length" class="mt-3 rounded-lg border border-red-100 bg-white p-3">
              <div class="text-[10px] font-bold uppercase tracking-widest text-red-400">复现</div>
              <ol class="mt-2 space-y-1">
                <li v-for="(step, stepIndex) in ensureList(finding.reproduction_steps).slice(0, 4)" :key="`${finding.title}-step-${stepIndex}`" class="text-xs leading-5 text-red-800">
                  {{ stepIndex + 1 }}. {{ step }}
                </li>
              </ol>
            </div>
            <div v-if="finding.next_action" class="mt-3 text-xs font-bold text-red-900">{{ finding.next_action }}</div>
          </div>
        </div>
      </div>
      <div v-else class="mt-4 rounded-lg border border-emerald-100 bg-emerald-50 px-4 py-3 text-xs text-emerald-700">
        当前报告未发现阻断缺陷，可将证据随发布评审归档。
      </div>

      <div class="mt-4 grid gap-4 lg:grid-cols-2">
        <div v-if="ensureList(triageSummary.recommended_next_actions).length" class="rounded-lg border border-amber-100 bg-amber-50 p-4">
          <div class="text-[10px] font-bold uppercase tracking-widest text-amber-500">建议动作</div>
          <ul class="mt-2 space-y-1">
            <li v-for="(action, index) in ensureList(triageSummary.recommended_next_actions)" :key="`triage-action-${index}`" class="text-xs leading-5 text-amber-800">
              {{ action }}
            </li>
          </ul>
        </div>
        <div class="rounded-lg border border-blue-100 bg-blue-50 p-4">
          <div class="text-[10px] font-bold uppercase tracking-widest text-blue-500">复现能力</div>
          <div class="mt-2 text-xs leading-5 text-blue-800">
            <template v-if="triageSummary.reproduction?.available">已生成可执行的复现路径<span v-if="triageSummary.reproduction?.script_available">，包含可复现脚本</span>。</template>
            <template v-else>当前没有失败路径需要复现。</template>
          </div>
          <ul v-if="ensureList(triageSummary.reproduction?.steps).length" class="mt-2 space-y-1">
            <li v-for="(step, index) in ensureList(triageSummary.reproduction?.steps).slice(0, 4)" :key="`triage-repro-${index}`" class="text-xs leading-5 text-blue-800">
              {{ index + 1 }}. {{ step }}
            </li>
          </ul>
        </div>
      </div>

      <div v-if="ensureList(triageSummary.triage_flow).length" class="mt-4 flex flex-wrap gap-2">
        <span
          v-for="item in ensureList(triageSummary.triage_flow)"
          :key="item.key"
          class="rounded-lg border px-2.5 py-1 text-[11px] font-bold"
          :class="item.enabled ? 'border-gray-200 bg-gray-50 text-gray-600' : 'border-gray-100 bg-gray-50 text-gray-300'"
        >
          {{ item.label }}
        </span>
      </div>
    </div>
    </AgentEvidenceCard>

    <!-- Tab Navigation -->
    <div class="sticky top-0 z-10 flex flex-wrap gap-1 rounded-lg border border-gray-200 bg-white/90 p-1 shadow-sm backdrop-blur">
      <button
        v-for="tab in visibleTabs"
        :key="tab.key"
        @click="activeTab = tab.key"
        class="flex items-center gap-1.5 px-4 py-2 rounded-md text-xs font-bold transition-all"
        :class="activeTab === tab.key ? 'bg-gray-950 text-white shadow-sm' : 'text-gray-500 hover:bg-gray-50 hover:text-gray-800'"
      >
        <component :is="tab.icon" :size="14" />
        {{ tab.label }}
      </button>
    </div>

    <!-- Tab: Report -->
    <div v-if="activeTab === 'report'" class="space-y-4">
      <div v-if="run.final_report" class="bg-white border border-gray-200 rounded-lg shadow-sm p-6">
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
            <ul class="mt-3 max-h-40 space-y-1 overflow-auto pr-1">
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
            <ul class="mt-3 max-h-40 space-y-1 overflow-auto pr-1">
              <li v-for="(finding, i) in ensureList(run.final_report.ui_test_summary?.key_findings)" :key="`ui-finding-${i}`" class="text-xs leading-5 text-gray-600">
                {{ finding }}
              </li>
            </ul>
          </div>
        </div>

        <div v-if="run.final_report.execution_notes?.length" class="mt-4 rounded-lg border border-blue-100 bg-blue-50 p-4">
          <div class="text-[10px] font-bold uppercase tracking-widest text-blue-500">执行说明</div>
          <ul class="mt-2 max-h-44 space-y-1 overflow-auto pr-1">
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
          <ul class="mt-2 max-h-44 space-y-1 overflow-auto pr-1">
            <li v-for="(rec, i) in run.final_report.recommendations" :key="`report-rec-${i}`" class="text-xs leading-5 text-amber-800">{{ rec }}</li>
          </ul>
        </div>
      </div>

      <!-- Bugs Found -->
      <div v-if="reportBugs.length" class="bg-white border border-red-200 rounded-lg shadow-sm p-6">
        <div class="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <h3 class="text-xs font-bold text-red-400 uppercase tracking-widest">发现的缺陷</h3>
          <InlinePager v-model:page="reportBugPage" :page-size="reportBugPageSize" :total="reportBugs.length" label="缺陷" />
        </div>
        <div class="max-h-[520px] space-y-3 overflow-auto pr-1">
          <div v-for="{ item: bug, index: i } in pagedReportBugs" :key="i" class="p-4 bg-red-50 rounded-lg border border-red-100">
            <div class="flex items-center justify-between mb-2">
              <span class="font-bold text-sm text-red-900">{{ bug.title }}</span>
              <span class="px-2 py-0.5 bg-red-200 text-red-800 rounded text-[10px] font-bold">{{ bug.severity }}</span>
            </div>
            <p class="text-xs text-red-700">{{ bug.description }}</p>
          </div>
        </div>
      </div>

      <!-- Execution Result (legacy) -->
      <div v-if="run.execution_result && !run.final_report" class="bg-white border border-gray-200 rounded-lg shadow-sm p-6">
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
      <div v-if="run.api_execution_result" class="bg-white border border-gray-200 rounded-lg shadow-sm p-6">
        <div class="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest">API 测试结果</h3>
          <InlinePager v-model:page="apiResultPage" :page-size="apiResultPageSize" :total="apiResults.length" label="接口" />
        </div>
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

        <div class="max-h-[640px] overflow-auto rounded-lg border border-gray-100">
          <table class="w-full min-w-[760px] text-left text-xs">
            <thead class="sticky top-0 z-[1]">
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
              <template v-for="{ item: r, index: i } in pagedApiResults" :key="i">
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
                  <div class="grid grid-cols-1 gap-3 md:grid-cols-2">
                    <div>
                      <div class="text-[10px] font-bold text-gray-400 uppercase mb-1">请求头</div>
                      <pre class="bg-gray-950 text-gray-200 rounded-lg p-3 text-[10px] font-mono overflow-auto max-h-32 whitespace-pre-wrap break-words">{{ JSON.stringify(r.request_headers || {}, null, 2) }}</pre>
                    </div>
                    <div>
                      <div class="text-[10px] font-bold text-gray-400 uppercase mb-1">响应体</div>
                      <pre class="bg-gray-950 text-gray-200 rounded-lg p-3 text-[10px] font-mono overflow-auto max-h-32 whitespace-pre-wrap break-words">{{ typeof r.body === 'object' ? JSON.stringify(r.body, null, 2) : r.body }}</pre>
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
      <div v-if="run.ui_execution_result" class="bg-white border border-gray-200 rounded-lg shadow-sm p-6">
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
        <div v-if="uiCommandRows.length" class="rounded-lg border border-gray-100 bg-gray-50 p-3">
          <div class="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">命令日志</div>
            <InlinePager v-model:page="uiCommandPage" :page-size="uiCommandPageSize" :total="uiCommandRows.length" label="命令" />
          </div>
          <div class="max-h-[420px] space-y-2 overflow-auto pr-1">
          <div v-for="{ item: cmd, index: i } in pagedUiCommandRows" :key="i"
            class="flex flex-col gap-2 rounded-lg border p-3 text-xs sm:flex-row sm:items-center sm:gap-3"
            :class="cmd.status_code === 0 ? 'bg-emerald-50 border-emerald-100' : 'bg-red-50 border-red-100'"
          >
            <span v-if="cmd.case_title" class="text-gray-400 shrink-0">{{ cmd.case_title }}</span>
            <span class="break-all font-mono font-bold text-gray-700 sm:shrink-0">{{ cmd.normalized_command || cmd.command }}</span>
            <span v-if="cmd.normalization" class="min-w-0 break-words text-blue-500 sm:truncate">{{ cmd.normalization }}</span>
            <span v-if="cmd.stderr" class="min-w-0 break-words text-red-500 sm:truncate">{{ cmd.stderr }}</span>
          </div>
          </div>
        </div>

        <!-- Screenshots -->
        <div v-if="uiCaseEvidence.length" class="mt-6">
          <div class="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <h4 class="text-xs font-bold text-gray-400 uppercase tracking-widest flex items-center gap-2">
              <Camera :size="14" /> 截图证据预览 ({{ uiScreenshotCount }})
            </h4>
            <button
              v-if="hasScreenshots"
              type="button"
              class="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-[11px] font-bold text-gray-600 transition-all hover:bg-gray-50 hover:text-gray-900"
              @click="activeTab = 'screenshots'"
            >
              查看全部证据
            </button>
          </div>
          <div class="space-y-4">
            <div
              v-for="caseEvidence in uiCaseEvidencePreview"
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
      <div v-if="uiCaseEvidence.length" class="bg-white border border-gray-200 rounded-lg shadow-sm p-6">
        <div class="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest flex items-center gap-2">
            <Camera :size="14" /> 截图证据
          </h3>
          <div class="flex flex-wrap items-center gap-2">
            <span class="rounded bg-gray-100 px-2 py-1 text-[10px] font-bold text-gray-500">{{ uiScreenshotCount }} 张</span>
            <InlinePager v-model:page="screenshotCasePage" :page-size="screenshotCasePageSize" :total="uiCaseEvidence.length" label="用例" />
          </div>
        </div>
        <div class="max-h-[760px] space-y-5 overflow-auto pr-1">
          <div
            v-for="{ item: caseEvidence } in pagedScreenshotCases"
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
            <div v-if="caseEvidence.screenshots.length" class="grid max-h-[560px] grid-cols-1 gap-4 overflow-auto pr-1 md:grid-cols-2 xl:grid-cols-3">
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
      <div v-if="hasRagSurface" class="bg-white border border-gray-200 rounded-lg shadow-sm p-6">
        <div class="mb-4 flex items-center justify-between gap-3">
          <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest flex items-center gap-2">
            <BookOpen :size="14" /> {{ ragPanelTitle }}
          </h3>
          <span class="rounded px-2 py-1 text-[10px] font-bold" :class="ragStatusClass(ragDisplayStatus)">
            {{ ragDisplayStatus }}
          </span>
        </div>

        <div class="grid gap-3 lg:grid-cols-[minmax(0,1fr)_300px]">
          <div class="rounded-lg border px-4 py-3" :class="ragEffectClass(ragDisplayMode)">
            <div class="text-xs font-bold">{{ ragDisplayEffect }}</div>
            <div v-if="ragRetrieval?.query" class="mt-2 truncate font-mono text-[11px]" :class="ragQueryClass(ragDisplayMode)">{{ ragRetrieval.query }}</div>
            <div v-if="ragRetrieval?.fallback_reason" class="mt-2 text-[11px] font-semibold">
              {{ ragRetrieval.fallback_reason }}
            </div>
          </div>
          <div class="grid grid-cols-3 gap-2">
            <div class="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
              <div class="text-[10px] font-bold text-gray-400">模式</div>
              <div class="mt-1 truncate text-xs font-bold text-gray-900">{{ ragModeLabel(ragDisplayMode) }}</div>
            </div>
            <div class="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
              <div class="text-[10px] font-bold text-gray-400">向量源</div>
              <div class="mt-1 text-sm font-bold text-gray-900">{{ ragRetrieval?.vector_source_count || 0 }}</div>
            </div>
            <div class="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
              <div class="text-[10px] font-bold text-gray-400">注入片段</div>
              <div class="mt-1 text-sm font-bold text-gray-900">{{ ragSources.length }}</div>
            </div>
          </div>
        </div>

        <div v-if="ragSources.length" class="mt-4 grid max-h-[420px] gap-3 overflow-auto pr-1 md:grid-cols-2">
          <div
            v-for="source in ragSources"
            :key="source.id"
            class="rounded-lg border border-gray-100 bg-gray-50 p-3"
          >
            <div class="mb-2 flex items-center justify-between gap-2">
              <span class="truncate font-mono text-[10px] font-bold text-gray-500">{{ source.source_script_id || source.id }}</span>
              <span class="shrink-0 rounded bg-white px-2 py-0.5 text-[10px] font-bold text-gray-500">{{ ragScoreLabel(source) }}</span>
            </div>
            <p class="line-clamp-3 text-xs leading-5 text-gray-600">{{ source.snippet }}</p>
          </div>
        </div>

        <pre v-if="run.rag_context" class="mt-4 max-h-44 overflow-auto rounded-lg border border-gray-100 bg-gray-950 p-3 text-[11px] leading-relaxed text-gray-100">{{ run.rag_context }}</pre>
      </div>

      <div v-if="agentEvaluations.length" class="bg-white border border-gray-200 rounded-lg shadow-sm p-6">
        <div class="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest flex items-center gap-2">
            <Activity :size="14" /> 证据评估与重规划
          </h3>
          <span class="rounded bg-gray-100 px-2 py-1 text-[10px] font-bold text-gray-500">{{ agentEvaluations.length }} 次评估</span>
        </div>
        <div class="space-y-3">
          <div
            v-for="(evaluation, index) in agentEvaluations.slice().reverse()"
            :key="`agent-evaluation-${index}-${evaluation.next_action}`"
            class="rounded-lg border p-4"
            :class="agentEvidenceClass(evaluation)"
          >
            <div class="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <span class="text-sm font-bold">{{ agentActionLabel(evaluation.next_action) }}</span>
                  <span class="rounded bg-white/70 px-2 py-0.5 text-[10px] font-bold">{{ evaluation.stage || 'agent' }}</span>
                  <span v-if="evaluation.source" class="rounded bg-white/70 px-2 py-0.5 text-[10px] font-bold">{{ evaluation.source }}</span>
                </div>
                <p class="mt-2 text-xs leading-5">{{ evaluation.reason }}</p>
              </div>
              <span class="shrink-0 rounded bg-white/70 px-2 py-0.5 text-[10px] font-bold">
                {{ evaluation.sufficient_evidence ? '证据充分' : '需要补证' }}
              </span>
            </div>
            <div v-if="ensureList(evaluation.missing_evidence).length" class="mt-3 flex flex-wrap gap-2">
              <span
                v-for="(item, missingIndex) in ensureList(evaluation.missing_evidence)"
                :key="`missing-${index}-${missingIndex}`"
                class="rounded border border-current/10 bg-white/60 px-2 py-1 text-[11px]"
              >
                {{ item }}
              </span>
            </div>
          </div>
        </div>
      </div>

      <div v-if="skillPlan.length" class="bg-white border border-gray-200 rounded-lg shadow-sm p-6">
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

      <div v-if="agentActionObservations.length" class="bg-white border border-gray-200 rounded-lg shadow-sm p-6">
        <div class="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest flex items-center gap-2">
            <Zap :size="14" /> Supervisor 动作观察
          </h3>
          <span class="rounded bg-gray-100 px-2 py-1 text-[10px] font-bold text-gray-500">{{ agentActionObservations.length }} 条</span>
        </div>
        <div class="grid gap-3 lg:grid-cols-2">
          <div
            v-for="observation in agentActionObservations.slice().reverse().slice(0, 8)"
            :key="`${observation.action_id}-${observation.stage}-${observation.timestamp}`"
            class="rounded-lg border border-gray-100 bg-gray-50 p-3"
          >
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0">
                <div class="truncate font-mono text-xs font-bold text-gray-800">{{ observation.tool_name }}</div>
                <div class="mt-1 flex flex-wrap items-center gap-2 text-[10px] text-gray-400">
                  <span>{{ observation.stage }}</span>
                  <span>{{ observation.layer }}</span>
                  <span>{{ observation.risk }}</span>
                </div>
              </div>
              <span class="shrink-0 rounded px-2 py-0.5 text-[10px] font-bold" :class="toolStatusClass(observation.status)">
                {{ observation.status }}
              </span>
            </div>
            <p class="mt-2 line-clamp-2 text-xs leading-5 text-gray-600">{{ observation.observation }}</p>
            <pre v-if="observation.output" class="mt-2 max-h-28 overflow-auto rounded bg-white p-2 text-[10px] font-mono text-gray-500 border border-gray-100 whitespace-pre-wrap break-words">{{ formatPreview(observation.output, 900) }}</pre>
          </div>
        </div>
      </div>

      <div v-if="toolCalls.length" class="bg-white border border-gray-200 rounded-lg shadow-sm p-6">
        <div class="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest flex items-center gap-2">
            <Terminal :size="14" /> 工具调用记录
          </h3>
          <div class="flex flex-wrap items-center gap-2">
            <span class="rounded bg-gray-100 px-2 py-1 text-[10px] font-bold text-gray-500">{{ toolCalls.length }} 次</span>
            <InlinePager v-model:page="toolCallPage" :page-size="toolCallPageSize" :total="recentToolCalls.length" label="调用" />
          </div>
        </div>
        <div class="max-h-[640px] space-y-2 overflow-auto pr-1">
          <div
            v-for="{ item: call, index: i } in pagedToolCalls"
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
              <pre v-if="call.input" class="max-h-28 overflow-auto rounded bg-white p-2 text-[10px] font-mono text-gray-500 border border-gray-100 whitespace-pre-wrap break-words">{{ formatPreview(call.input, 1000) }}</pre>
              <pre v-if="call.output" class="max-h-28 overflow-auto rounded bg-white p-2 text-[10px] font-mono text-gray-500 border border-gray-100 whitespace-pre-wrap break-words">{{ formatPreview(call.output, 1000) }}</pre>
            </div>
          </div>
        </div>
      </div>

      <div v-if="!skillPlan.length && !toolCalls.length && !agentEvaluations.length" class="text-center py-12 text-gray-400 text-sm">暂无工具调用记录</div>
    </div>

    <!-- Tab: Test Cases -->
    <div v-if="activeTab === 'cases'" class="space-y-4">
      <div v-if="hasGeneratedCaseAssets" class="bg-white border border-gray-200 rounded-lg shadow-sm p-6">
        <div class="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest">用例资产</h3>
            <div class="mt-1 text-sm font-bold text-gray-900">{{ acceptedCaseAssetCount }}/{{ caseAssetDrafts.length }} 已接受</div>
          </div>
          <div class="flex flex-col gap-2 sm:flex-row sm:items-center">
            <input
              v-model="caseAssetSuiteName"
              class="h-9 min-w-0 rounded-lg border border-gray-200 bg-white px-3 text-xs text-gray-700 outline-none transition-all focus:border-blue-400 focus:ring-2 focus:ring-blue-100 sm:w-72"
              placeholder="套件名称"
            />
            <button
              @click="saveAcceptedCaseAssets"
              :disabled="caseAssetSaving || !acceptedCaseAssetCount"
              class="inline-flex h-9 items-center justify-center gap-1.5 rounded-lg bg-gray-950 px-3 text-xs font-bold text-white transition-all hover:bg-gray-800 disabled:cursor-not-allowed disabled:bg-gray-300"
            >
              <Loader2 v-if="caseAssetSaving" :size="14" class="animate-spin" />
              <Save v-else :size="14" />
              保存到套件
            </button>
          </div>
        </div>
        <div v-if="caseAssetResult" class="mt-4 rounded-lg border border-emerald-100 bg-emerald-50 px-4 py-3 text-xs text-emerald-700">
          已保存到套件：{{ caseAssetResult.suite_name }}，共 {{ caseAssetResult.total }} 个用例。
        </div>
      </div>

      <!-- API Cases -->
      <div v-if="apiCaseAssetDrafts.length" class="bg-white border border-gray-200 rounded-lg shadow-sm p-6">
        <div class="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest">API 测试用例 ({{ apiCaseAssetDrafts.length }})</h3>
          <InlinePager v-model:page="apiCaseAssetPage" :page-size="caseAssetPageSize" :total="apiCaseAssetDrafts.length" label="用例" />
        </div>
        <div class="max-h-[720px] space-y-3 overflow-auto pr-1">
          <div
            v-for="{ item: draft } in pagedApiCaseAssetDrafts"
            :key="draft.key"
            class="rounded-lg border p-4 transition-all"
            :class="draft.status === 'accepted' ? 'border-emerald-200 bg-emerald-50/40' : 'border-gray-200 bg-gray-50 opacity-75'"
          >
            <div class="mb-3 flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
              <div class="min-w-0 flex-1">
                <input
                  v-model="draft.title"
                  class="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm font-bold text-gray-900 outline-none transition-all focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
                />
                <div v-if="caseEndpoint(draft.raw)" class="mt-2 truncate font-mono text-xs text-gray-500">
                  <span v-if="caseMethod(draft.raw)" class="font-bold text-blue-600">{{ caseMethod(draft.raw) }}</span>
                  {{ caseEndpoint(draft.raw) }}
                </div>
              </div>
              <div class="flex shrink-0 flex-wrap gap-2">
                <button
                  @click="setCaseAssetStatus(draft, 'accepted')"
                  class="inline-flex h-8 items-center gap-1.5 rounded-lg border px-2.5 text-[11px] font-bold transition-all"
                  :class="draft.status === 'accepted' ? 'border-emerald-200 bg-emerald-100 text-emerald-700' : 'border-gray-200 bg-white text-gray-500 hover:bg-gray-100'"
                >
                  <CheckCircle2 :size="13" /> 接受
                </button>
                <button
                  @click="setCaseAssetStatus(draft, 'rejected')"
                  class="inline-flex h-8 items-center gap-1.5 rounded-lg border px-2.5 text-[11px] font-bold transition-all"
                  :class="draft.status === 'rejected' ? 'border-red-200 bg-red-100 text-red-700' : 'border-gray-200 bg-white text-gray-500 hover:bg-gray-100'"
                >
                  <XCircle :size="13" /> 拒绝
                </button>
              </div>
            </div>

            <div class="grid gap-3 md:grid-cols-[120px_160px_minmax(0,1fr)]">
              <StyledSelect v-model="draft.priority" size="sm">
                <option value="P0">P0</option>
                <option value="P1">P1</option>
                <option value="P2">P2</option>
                <option value="P3">P3</option>
              </StyledSelect>
              <input v-model="draft.category" class="h-9 rounded-lg border border-gray-200 bg-white px-3 text-xs text-gray-700 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100" />
              <textarea v-model="draft.expectedText" rows="2" class="min-h-9 rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs leading-5 text-gray-600 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"></textarea>
            </div>
            <textarea v-model="draft.stepsText" rows="3" class="mt-3 w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs leading-5 text-gray-600 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"></textarea>
          </div>
        </div>
      </div>

      <!-- UI Cases -->
      <div v-if="uiCaseAssetDrafts.length" class="bg-white border border-gray-200 rounded-lg shadow-sm p-6">
        <div class="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest">UI 测试用例 ({{ uiCaseAssetDrafts.length }})</h3>
          <InlinePager v-model:page="uiCaseAssetPage" :page-size="caseAssetPageSize" :total="uiCaseAssetDrafts.length" label="用例" />
        </div>
        <div class="max-h-[720px] space-y-3 overflow-auto pr-1">
          <div
            v-for="{ item: draft } in pagedUiCaseAssetDrafts"
            :key="draft.key"
            class="rounded-lg border p-4 transition-all"
            :class="draft.status === 'accepted' ? 'border-emerald-200 bg-emerald-50/40' : 'border-gray-200 bg-gray-50 opacity-75'"
          >
            <div class="mb-3 flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
              <input
                v-model="draft.title"
                class="min-w-0 flex-1 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm font-bold text-gray-900 outline-none transition-all focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
              />
              <div class="flex shrink-0 flex-wrap gap-2">
                <button
                  @click="setCaseAssetStatus(draft, 'accepted')"
                  class="inline-flex h-8 items-center gap-1.5 rounded-lg border px-2.5 text-[11px] font-bold transition-all"
                  :class="draft.status === 'accepted' ? 'border-emerald-200 bg-emerald-100 text-emerald-700' : 'border-gray-200 bg-white text-gray-500 hover:bg-gray-100'"
                >
                  <CheckCircle2 :size="13" /> 接受
                </button>
                <button
                  @click="setCaseAssetStatus(draft, 'rejected')"
                  class="inline-flex h-8 items-center gap-1.5 rounded-lg border px-2.5 text-[11px] font-bold transition-all"
                  :class="draft.status === 'rejected' ? 'border-red-200 bg-red-100 text-red-700' : 'border-gray-200 bg-white text-gray-500 hover:bg-gray-100'"
                >
                  <XCircle :size="13" /> 拒绝
                </button>
              </div>
            </div>

            <div class="grid gap-3 md:grid-cols-[120px_160px_minmax(0,1fr)]">
              <StyledSelect v-model="draft.priority" size="sm">
                <option value="P0">P0</option>
                <option value="P1">P1</option>
                <option value="P2">P2</option>
                <option value="P3">P3</option>
              </StyledSelect>
              <input v-model="draft.category" class="h-9 rounded-lg border border-gray-200 bg-white px-3 text-xs text-gray-700 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100" />
              <textarea v-model="draft.expectedText" rows="2" class="min-h-9 rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs leading-5 text-gray-600 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"></textarea>
            </div>
            <textarea v-model="draft.stepsText" rows="3" class="mt-3 w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs leading-5 text-gray-600 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"></textarea>

            <div v-if="caseCommands(draft.raw).length" class="mt-3">
              <div class="text-[10px] font-bold text-gray-400 uppercase mb-1">Playwright 命令</div>
              <pre class="bg-gray-100 rounded p-2 text-[10px] font-mono text-gray-600 overflow-auto max-h-32 whitespace-pre-wrap break-words">{{ caseCommands(draft.raw).join('\n') }}</pre>
            </div>
          </div>
        </div>
      </div>

      <div v-if="!hasGeneratedCaseAssets" class="text-center py-12 text-gray-400 text-sm">无测试用例</div>
    </div>

    <!-- Tab: Script -->
    <div v-if="activeTab === 'script'" class="space-y-4">
      <div class="bg-white border border-gray-200 rounded-lg shadow-sm p-6">
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
              class="px-3 py-1.5 bg-gray-950 hover:bg-gray-800 text-white rounded-lg text-xs font-bold transition-all"
            >
              下载
            </button>
          </div>
        </div>
        <div v-if="run.ui_reproducible_script || run.artifacts?.ui_reproducible_script">
          <pre class="bg-gray-950 text-green-400 rounded-lg p-4 text-xs font-mono overflow-auto max-h-[600px] whitespace-pre-wrap break-words">{{ run.ui_reproducible_script || run.artifacts?.ui_reproducible_script }}</pre>
        </div>
        <div v-else class="text-center py-12 text-gray-400 text-sm">
          暂无脚本 — 运行 UI 测试后会自动生成可复现脚本
        </div>
      </div>

      <!-- Login Info -->
      <div v-if="run.setup_instructions || run.login_instructions" class="bg-white border border-gray-200 rounded-lg shadow-sm p-6">
        <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-3">测试前置说明</h3>
        <p class="text-sm text-gray-700 whitespace-pre-wrap">{{ run.setup_instructions || run.login_instructions }}</p>
      </div>

      <!-- Login Snapshot -->
      <div v-if="run.ui_login_snapshot" class="bg-white border border-gray-200 rounded-lg shadow-sm p-6">
        <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-3">登录后页面快照</h3>
        <pre class="bg-gray-50 rounded-lg p-3 text-[10px] font-mono text-gray-600 overflow-auto max-h-60 whitespace-pre-wrap">{{ run.ui_login_snapshot?.slice(0, 3000) }}</pre>
      </div>
    </div>

    <!-- Tab: Logs -->
    <div v-if="activeTab === 'logs'" class="space-y-4">
      <!-- Workflow Steps Log -->
      <div v-if="run.workflow_steps?.length" class="bg-white border border-gray-200 rounded-lg shadow-sm p-6">
        <div class="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest flex items-center gap-2">
            <Terminal :size="14" /> 工作流日志
          </h3>
          <InlinePager v-model:page="workflowStepPage" :page-size="workflowStepPageSize" :total="workflowSteps.length" label="步骤" />
        </div>
        <div class="max-h-[560px] space-y-2 overflow-auto pr-1">
          <div
            v-for="{ item: step, index: idx } in pagedWorkflowSteps"
            :key="idx"
            class="flex items-start gap-3 p-3 rounded-lg border font-mono text-xs"
            :class="step.status === 'done' ? 'bg-emerald-50 border-emerald-200' : step.status === 'failed' ? 'bg-red-50 border-red-200' : 'bg-gray-50 border-gray-200'"
          >
            <span class="text-gray-400 shrink-0 w-5 text-right">{{ idx + 1 }}</span>
            <span class="px-1.5 py-0.5 rounded text-[10px] font-bold shrink-0"
              :class="step.status === 'done' ? 'bg-emerald-200 text-emerald-800' : step.status === 'failed' ? 'bg-red-200 text-red-800' : 'bg-gray-200 text-gray-600'"
            >{{ step.node }}</span>
            <span class="min-w-0 flex-1 break-words text-gray-600">{{ step.detail }}</span>
            <span class="text-[10px] shrink-0"
              :class="step.status === 'done' ? 'text-emerald-600' : step.status === 'failed' ? 'text-red-600' : 'text-gray-400'"
            >{{ step.status }}</span>
          </div>
        </div>
      </div>

      <!-- Progress Events Log -->
      <div v-if="progressEvents.length" class="bg-white border border-gray-200 rounded-lg shadow-sm p-6">
        <div class="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest flex items-center gap-2">
            <Activity :size="14" /> 进度事件
          </h3>
          <InlinePager v-model:page="progressEventPage" :page-size="progressEventPageSize" :total="progressEvents.length" label="事件" />
        </div>
        <div class="max-h-[560px] space-y-2 overflow-auto pr-1">
          <div
            v-for="{ item: event, index: idx } in pagedProgressEvents"
            :key="`${event.timestamp || idx}-${event.node || event.stage || 'event'}`"
            class="rounded-lg border border-gray-200 bg-gray-50 p-3 text-xs"
          >
            <div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div class="min-w-0 break-all font-mono font-bold text-gray-800">{{ event.node || event.stage || event.event || `event ${idx + 1}` }}</div>
              <div class="flex shrink-0 flex-wrap items-center gap-2">
                <span class="rounded bg-white px-2 py-0.5 text-[10px] font-bold text-gray-500">{{ event.status || event.source || 'progress' }}</span>
                <span v-if="event.timestamp" class="font-mono text-[10px] text-gray-400">{{ new Date(event.timestamp).toLocaleString('zh-CN') }}</span>
              </div>
            </div>
            <p v-if="event.detail || event.message" class="mt-2 break-words leading-5 text-gray-600">{{ event.detail || event.message }}</p>
          </div>
        </div>
      </div>

      <!-- Execution Result Log -->
      <div v-if="run.execution_result" class="bg-white border border-gray-200 rounded-lg shadow-sm p-6">
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
          <pre class="bg-gray-950 text-gray-100 rounded-lg p-4 text-[11px] font-mono overflow-auto max-h-60 leading-relaxed whitespace-pre-wrap break-words">{{ run.execution_result.stdout }}</pre>
        </div>
        <div v-if="run.execution_result.stderr">
          <div class="text-[10px] font-bold text-red-400 uppercase mb-1">错误输出 (stderr)</div>
          <pre class="bg-gray-950 text-red-300 rounded-lg p-4 text-[11px] font-mono overflow-auto max-h-60 leading-relaxed whitespace-pre-wrap break-words">{{ run.execution_result.stderr }}</pre>
        </div>
        <div v-if="run.execution_result.trace_path" class="mt-3">
          <div class="text-[10px] font-bold text-gray-400 uppercase mb-1">Trace 路径</div>
          <div class="text-xs font-mono text-gray-600 bg-gray-50 rounded-lg p-3 border border-gray-100">{{ run.execution_result.trace_path }}</div>
        </div>
      </div>

      <!-- Generated Code -->
      <div v-if="run.generated_code" class="bg-white border border-gray-200 rounded-lg shadow-sm p-6">
        <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4 flex items-center gap-2">
          <FileText :size="14" /> 生成代码
        </h3>
        <pre class="bg-gray-950 text-gray-100 rounded-lg p-4 text-[11px] font-mono overflow-auto max-h-80 leading-relaxed whitespace-pre-wrap break-words">{{ run.generated_code }}</pre>
      </div>

      <!-- Raw Execution Log -->
      <div v-if="run.execution_log" class="bg-white border border-gray-200 rounded-lg shadow-sm p-6">
        <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4 flex items-center gap-2">
          <Terminal :size="14" /> 原始日志 (JSON)
        </h3>
        <pre class="bg-gray-950 text-gray-100 rounded-lg p-4 text-[11px] font-mono overflow-auto max-h-80 leading-relaxed whitespace-pre-wrap break-words">{{ formatJson(run.execution_log) }}</pre>
      </div>

      <div v-if="!run.workflow_steps?.length && !progressEvents.length && !run.execution_result && !run.generated_code && !run.execution_log" class="text-center py-12 text-gray-400 text-sm">暂无日志</div>
    </div>
  </div>

  <div v-else-if="!loading" class="flex flex-col items-center justify-center py-24 text-center">
    <div class="text-gray-400 text-sm">运行不存在或加载失败</div>
    <button @click="router.push('/history')" class="mt-4 px-4 py-2 bg-gray-950 text-white rounded-lg text-sm font-bold hover:bg-gray-800 transition-all">
      返回历史记录
    </button>
  </div>

  <!-- Lightbox Modal -->
  <Teleport to="body">
    <div v-if="lightboxUrl" class="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm" @click.self="closeLightbox">
      <div class="relative max-w-[90vw] max-h-[90vh]">
        <button
          type="button"
          aria-label="关闭截图预览"
          title="关闭截图预览"
          @click="closeLightbox"
          class="absolute -top-10 right-0 text-white hover:text-gray-300 transition-colors"
        >
          <XCircle :size="28" />
        </button>
        <img :src="lightboxUrl" class="max-w-full max-h-[85vh] rounded-lg shadow-2xl object-contain" @error="(e: any) => { e.target.alt = '截图加载失败' }" />
      </div>
    </div>
  </Teleport>
</template>
