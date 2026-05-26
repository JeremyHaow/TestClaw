<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  ChevronRight,
  ClipboardList,
  Globe2,
  KeyRound,
  ListChecks,
  Loader2,
  LockKeyhole,
  MessageSquare,
  Pencil,
  Plus,
  Trash2,
} from 'lucide-vue-next'
import AgentChatInput from '../components/agent/AgentChatInput.vue'
import AgentPlanDraft from '../components/agent/AgentPlanDraft.vue'
import AgentQuestionCard from '../components/agent/AgentQuestionCard.vue'
import api, { apiUrl } from '../lib/api'
import { redactSensitiveText } from '../lib/assetHandoff'
import { useToast } from '../composables/useToast'
import type {
  IntakeStep,
  IntakeStepId,
  PlanMessage,
  PlannerProcessEvent,
  PlannerQuestionChoice,
  PlannerQuestionOption,
  PlanningSession,
} from '../types/agentPlan'

const router = useRouter()
const route = useRoute()
const toast = useToast()
const sessions = ref<PlanningSession[]>([])
const activeSession = ref<PlanningSession | null>(null)
const loading = ref(false)
const sending = ref(false)
const creating = ref(false)
const rejecting = ref(false)
const executing = ref(false)
const deletingSessionId = ref('')
const deletingMessageId = ref('')
const editingMessageId = ref<string | null>(null)
const draft = ref('')
const executeError = ref('')
const chatEnd = ref<HTMLElement | null>(null)
const chatInput = ref<InstanceType<typeof AgentChatInput> | null>(null)
const processEvents = ref<PlannerProcessEvent[]>([])
const editingRollbackSnapshot = ref<PlanningSession | null>(null)
const selectedIntakeChoices = ref<Partial<Record<IntakeStepId, PlannerQuestionChoice>>>({})
const intakeSupplement = ref<Partial<Record<IntakeStepId, string>>>({})
const deferredIntakeSteps = ref<Partial<Record<IntakeStepId, boolean>>>({})
const skippedIntakeSteps = ref<Partial<Record<IntakeStepId, boolean>>>({})
let streamAbortController: AbortController | null = null

const IMPORTED_PLAN_CONTEXT_LIMIT = 1400

const intakeSteps: IntakeStep[] = [
  { id: 'target_kind', label: '测试目标', icon: Globe2 },
  { id: 'coverage_scope', label: '覆盖范围', icon: ClipboardList },
  { id: 'auth_boundary', label: '登录方式/凭证', icon: KeyRound },
  { id: 'safety_boundary', label: '安全边界', icon: LockKeyhole },
  { id: 'success_criteria', label: '成功标准', icon: ListChecks },
]

const messages = computed(() => activeSession.value?.messages || [])
const currentPlan = computed(() => activeSession.value?.current_plan || null)
const currentPayload = computed(() => activeSession.value?.current_run_payload || null)
const planReady = computed(() => Boolean(activeSession.value?.ready_to_execute && currentPlan.value))
const canModifyActiveSession = computed(() => Boolean(activeSession.value && activeSession.value.status !== 'executed'))
const scopeItems = computed(() => toStringList(currentPlan.value?.scope))
const stepItems = computed(() => toStringList(currentPlan.value?.steps))
const safetyItems = computed(() => toStringList(currentPlan.value?.safety))
const serverCurrentStepId = computed(() => normalizeStepId(activeSession.value?.current_step))
const latestQuestionGroups = computed(() => {
  const sessionGroups = activeSession.value?.question_options
  const groups = Array.isArray(sessionGroups) && sessionGroups.length
    ? sessionGroups
    : latestAssistantQuestionOptions()
  return groups
    .filter((group) => group?.question && Array.isArray(group.options) && group.options.length)
    .slice(0, 2)
})
const currentIntakeGroup = computed(() => latestQuestionGroups.value[0] || null)
const currentStepId = computed<IntakeStepId>(() => {
  if (serverCurrentStepId.value) return serverCurrentStepId.value
  const groupStep = currentIntakeGroup.value ? stepIdForGroup(currentIntakeGroup.value) : null
  if (groupStep) return groupStep
  if (planReady.value) return 'success_criteria'
  return firstOpenStepId()
})
const currentStep = computed<IntakeStep>(() => (
  intakeSteps.find((step) => step.id === currentStepId.value) || intakeSteps[0]!
))
const deterministicTargetGroup = computed<PlannerQuestionOption | null>(() => {
  if (currentStepId.value !== 'target_kind' || currentIntakeGroup.value) return null
  return targetKindGroupForSource(currentSupplementText.value || draft.value)
})
const displayedIntakeGroup = computed(() => currentIntakeGroup.value || deterministicTargetGroup.value)
const currentSupplementText = computed({
  get: () => intakeSupplement.value[currentStepId.value] || '',
  set: (value: string) => {
    intakeSupplement.value = { ...intakeSupplement.value, [currentStepId.value]: value }
  },
})
const currentSelectedChoice = computed(() => selectedIntakeChoices.value[currentStepId.value] || null)
const currentGroupRequired = computed(() => displayedIntakeGroup.value?.required !== false)
const canSkipCurrentStep = computed(() => {
  if (!displayedIntakeGroup.value) return false
  return (
    currentGroupRequired.value === false
    || displayedIntakeGroup.value.options.some((option) => option.allows_skip || option.optional)
  )
})
const canContinueIntake = computed(() => Boolean(currentSelectedChoice.value || currentSupplementText.value.trim()))
const currentStepIndex = computed(() => intakeSteps.findIndex((step) => step.id === currentStepId.value))
const planDraftItems = computed(() => intakeSteps.map((step) => draftItemForStep(step.id)))
const currentDraftStatus = computed(() => draftItemForStep(currentStepId.value).status)
const activeRejectionReason = computed(() => (
  activeSession.value?.status === 'collecting' ? activeSession.value?.rejection_reason || '' : ''
))
const intakeControlsDisabled = computed(() => !canModifyActiveSession.value || Boolean(editingMessageId.value))

function toStringList(value: unknown) {
  return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean).slice(0, 8) : []
}

function queryText(value: unknown) {
  if (Array.isArray(value)) return typeof value[0] === 'string' ? value[0] : ''
  return typeof value === 'string' ? value : ''
}

function redactImportedPlanContext(value: unknown) {
  return redactSensitiveText(value, IMPORTED_PLAN_CONTEXT_LIMIT)
}

function importedQualityMemoryPlanContent() {
  if (queryText(route.query.from) !== 'quality-memory') return ''
  const context = redactImportedPlanContext(queryText(route.query.context))
  if (context) return context
  const target = redactImportedPlanContext(queryText(route.query.target))
  if (!target) return ''
  return [
    '从 TestClaw 质量记忆创建新测试计划。',
    `目标：${target}`,
    '安全边界：默认只读；不要复用历史凭证、Token、Cookie、会话或验证码值。',
  ].join('\n')
}

function importedAssetPlanContent() {
  if (queryText(route.query.from) !== 'asset') return ''
  const assetType = queryText(route.query.asset_type)
  const assetLabels: Record<string, string> = {
    document: '接口文档',
    environment: '测试环境',
    'test-case': '用例资产',
  }
  const label = assetLabels[assetType] || '可复用资产'
  const context = redactImportedPlanContext(queryText(route.query.context))
  const title = redactImportedPlanContext(queryText(route.query.title))
  if (!context && !title) return ''
  return [
    `从 TestClaw ${label}创建新测试计划。`,
    title ? `资产：${title}` : '',
    context,
    '安全边界：默认只读；不要复用凭证、Token、Cookie、会话或验证码值。',
  ].filter(Boolean).join('\n')
}

async function clearImportedQualityMemoryQuery() {
  const nextQuery = { ...route.query }
  delete nextQuery.from
  delete nextQuery.target
  delete nextQuery.context
  await router.replace({ path: route.path, query: nextQuery }).catch(() => undefined)
}

async function clearImportedAssetQuery() {
  const nextQuery = { ...route.query }
  delete nextQuery.from
  delete nextQuery.asset_type
  delete nextQuery.asset_id
  delete nextQuery.title
  delete nextQuery.context
  await router.replace({ path: route.path, query: nextQuery }).catch(() => undefined)
}

function latestAssistantQuestionOptions() {
  if (activeSession.value?.status !== 'collecting') return []
  for (const message of [...messages.value].reverse()) {
    const groups = messageQuestionOptions(message)
    if (groups.length) return groups
  }
  return []
}

function normalizeStepId(value?: string | null): IntakeStepId | null {
  const normalized = String(value || '').trim().toLowerCase()
  const aliases: Record<string, IntakeStepId> = {
    target: 'target_kind',
    target_kind: 'target_kind',
    target_type: 'target_kind',
    source: 'target_kind',
    scope: 'coverage_scope',
    coverage: 'coverage_scope',
    coverage_scope: 'coverage_scope',
    auth: 'auth_boundary',
    login: 'auth_boundary',
    credentials: 'auth_boundary',
    auth_boundary: 'auth_boundary',
    safety: 'safety_boundary',
    policy: 'safety_boundary',
    safety_boundary: 'safety_boundary',
    success: 'success_criteria',
    criteria: 'success_criteria',
    success_criteria: 'success_criteria',
  }
  return aliases[normalized] || null
}

function inferStepFromQuestion(question: string): IntakeStepId | null {
  if (/目标|网址|接口文档/.test(question)) return 'target_kind'
  if (/范围|覆盖/.test(question)) return 'coverage_scope'
  if (/登录|账号|鉴权|凭证|令牌|请求头/.test(question)) return 'auth_boundary'
  if (/安全|只读|写入/.test(question)) return 'safety_boundary'
  if (/成功|结果|断言|通过/.test(question)) return 'success_criteria'
  return null
}

function plannerChoice(
  label: string,
  title: string,
  description: string,
  value: string,
  message: string,
): PlannerQuestionChoice {
  return {
    label,
    title,
    description,
    field: 'target_kind',
    value,
    step: 'target_kind',
    message,
  }
}

function sourceSignal(value: string) {
  const text = value.trim()
  const lower = text.toLowerCase()
  const hasOpenApiText = /^\s*[{[]/.test(text) && /"paths"\s*:|"openapi"\s*:|"swagger"\s*:/.test(text)
  const hasOpenApiYaml = /^\s*(openapi|swagger)\s*:/.test(lower) || /\n\s*paths\s*:/.test(lower)
  const hasUrl = /https?:\/\/\S+/i.test(text)
  const hasOpenApiMarker = /\b(openapi|swagger|api-docs|v3\/api-docs)\b/i.test(text) || /接口文档/.test(text)
  const hasOpenApiUrl = /https?:\/\/\S*(swagger|openapi|api-docs|v3\/api-docs)(\/|\.|\?|#|$)/i.test(text)
    || /\/(swagger|openapi|api-docs)(\/|\.|\?|#|$)/i.test(text)
  const hasApiResponseSemantics = /\b(json|status\s*code|status|headers?|body|endpoint|assertions?|response|request|fields?)\b/i.test(text)
    || /(响应|状态码|字段|请求头|响应头|响应体|请求体|接口|端点|断言|返回)/.test(text)
  const hasUiMarker = /\b(web|ui|page|browser)\b/i.test(text) || /(网页|页面|浏览器|后台|管理台)/.test(text)
  const openApi = hasOpenApiText || hasOpenApiYaml || hasOpenApiMarker || hasOpenApiUrl
  const api = openApi || (hasUrl && hasApiResponseSemantics)
  const ui = hasUiMarker
  if (openApi) return 'api'
  if (api && !ui) return 'api'
  if (ui && !api) return 'ui'
  if (api && ui) return 'ambiguous'
  return 'ambiguous'
}

function targetKindGroupForSource(value: string): PlannerQuestionOption {
  const apiChoice = plannerChoice(
    'API / 接口',
    'API / OpenAPI',
    '用于接口文档、接口契约、只读接口覆盖或指定接口回归。',
    'api_openapi',
    '测试目标类型：API / OpenAPI/Swagger 接口来源。',
  )
  const uiChoice = plannerChoice(
    'Web UI / 网页',
    'Web UI 页面',
    '用于浏览器页面、登录后业务流程、表单和页面可用性检查。',
    'web_page',
    '测试目标类型：浏览器 Web UI 页面。',
  )
  const customChoice = plannerChoice(
    '自定义',
    '自定义目标',
    '用补充说明描述具体目标，但仍限定在 API 或浏览器 Web UI 范围内。',
    'custom',
    '测试目标类型：自定义 API/Web UI 目标，由补充说明限定。',
  )
  const signal = sourceSignal(value)
  const options = signal === 'api'
    ? [apiChoice, customChoice]
    : signal === 'ui'
      ? [uiChoice, customChoice]
      : [apiChoice, uiChoice, customChoice]
  return {
    question: signal === 'ambiguous' ? '这个目标应按 API 还是 Web UI 规划？' : '已识别目标类型，请确认。',
    step: 'target_kind',
    required: true,
    options,
  }
}

function stepIdForGroup(group: PlannerQuestionOption): IntakeStepId | null {
  return (
    normalizeStepId(group.step)
    || normalizeStepId(group.options[0]?.step)
    || normalizeStepId(group.options[0]?.field)
    || inferStepFromQuestion(group.question)
  )
}

function firstOpenStepId(): IntakeStepId {
  for (const step of intakeSteps) {
    if (!serverConfirmedStepForNavigation(step.id)) return step.id
  }
  return 'success_criteria'
}

function choiceTitle(option: PlannerQuestionChoice) {
  return option.title || option.label
}

function intakeDisplayText(value: unknown, limit = 240) {
  return redactSensitiveText(value, limit)
}

function selectIntakeChoice(group: PlannerQuestionOption, option: PlannerQuestionChoice) {
  const stepId = stepIdForGroup(group)
  if (!stepId || sending.value || intakeControlsDisabled.value) return
  selectedIntakeChoices.value = { ...selectedIntakeChoices.value, [stepId]: option }
  deferredIntakeSteps.value = { ...deferredIntakeSteps.value, [stepId]: false }
  skippedIntakeSteps.value = { ...skippedIntakeSteps.value, [stepId]: false }
}

function stepIndex(stepId: IntakeStepId) {
  return intakeSteps.findIndex((step) => step.id === stepId)
}

function stepTone(stepId: IntakeStepId) {
  const index = stepIndex(stepId)
  if (stepId === currentStepId.value) return 'active'
  if (index < currentStepIndex.value || serverConfirmedStepForNavigation(stepId)) return 'done'
  return 'pending'
}

function stepCircleClass(stepId: IntakeStepId) {
  const tone = stepTone(stepId)
  if (tone === 'done') return 'border-emerald-500 bg-emerald-500 text-white'
  if (tone === 'active') return 'border-gray-950 bg-gray-950 text-white'
  return 'border-gray-200 bg-white text-gray-400'
}

function stepTextClass(stepId: IntakeStepId) {
  const tone = stepTone(stepId)
  if (tone === 'done') return 'text-emerald-700'
  if (tone === 'active') return 'text-gray-950'
  return 'text-gray-400'
}

function draftItemForStep(stepId: IntakeStepId) {
  return localDraftItemForStep(stepId) || serverDraftItemForStep(stepId) || pendingDraftItemForStep(stepId)
}

function localDraftItemForStep(stepId: IntakeStepId) {
  const step = intakeSteps.find((item) => item.id === stepId) || intakeSteps[0]
  const selected = selectedIntakeChoices.value[stepId]
  const supplement = intakeMessageForStep(stepId)
  if (skippedIntakeSteps.value[stepId]) {
    return { id: stepId, label: step.label, status: '草稿', value: '准备跳过这个非必填项' }
  }
  if (deferredIntakeSteps.value[stepId]) {
    return { id: stepId, label: step.label, status: '草稿', value: '准备标记为稍后补充' }
  }
  if (selected || supplement) {
    const value = [
      selected ? choiceTitle(selected) : '',
      supplement ? intakeDisplayText(supplement) : '',
    ].filter(Boolean).join('；')
    return { id: stepId, label: step.label, status: '草稿', value }
  }
  return null
}

function serverDraftItemForStep(stepId: IntakeStepId) {
  const step = intakeSteps.find((item) => item.id === stepId) || intakeSteps[0]
  const structured = activeSession.value?.structured_intake?.[stepId]
  const structuredStatus = String(structured?.status || '').toLowerCase()
  const structuredValue = structured?.summary || structured?.supplement || structured?.message || structured?.label || structured?.value
  if (structuredStatus === 'skipped') {
    return { id: stepId, label: step.label, status: '已跳过', value: structuredValue || '按默认策略继续规划' }
  }
  if (structuredStatus === 'deferred') {
    return { id: stepId, label: step.label, status: '待补充', value: structuredValue || '已标记为稍后补充' }
  }
  if (structuredStatus === 'confirmed' && structuredValue) {
    return { id: stepId, label: step.label, status: '已收集', value: structuredValue }
  }
  if (stepId === 'target_kind' && (currentPlan.value?.target || currentPayload.value?.source)) {
    return { id: stepId, label: step.label, status: '已收集', value: currentPlan.value?.target || currentPayload.value?.source }
  }
  if (stepId === 'coverage_scope' && scopeItems.value.length) {
    return { id: stepId, label: step.label, status: '已生成', value: scopeItems.value[0] }
  }
  if (stepId === 'auth_boundary' && (currentPlan.value?.auth_summary || currentPayload.value?.auth_mode)) {
    return { id: stepId, label: step.label, status: '已收集', value: currentPlan.value?.auth_summary || currentPayload.value?.auth_mode }
  }
  if (stepId === 'safety_boundary' && (safetyItems.value.length || currentPayload.value?.api_execution_policy)) {
    return { id: stepId, label: step.label, status: '已收集', value: safetyItems.value[0] || currentPayload.value?.api_execution_policy }
  }
  if (stepId === 'success_criteria' && currentPlan.value?.summary) {
    return { id: stepId, label: step.label, status: '已生成', value: currentPlan.value.summary }
  }
  return null
}

function pendingDraftItemForStep(stepId: IntakeStepId) {
  const step = intakeSteps.find((item) => item.id === stepId) || intakeSteps[0]
  return { id: stepId, label: step.label, status: '待确认', value: '等待确认' }
}

function serverConfirmedStepForNavigation(stepId: IntakeStepId) {
  const serverItem = serverDraftItemForStep(stepId)
  return Boolean(serverItem && serverItem.status !== '待确认')
}

function withoutIntakeStep<T>(value: Partial<Record<IntakeStepId, T>>, stepId: IntakeStepId) {
  const next = { ...value }
  delete next[stepId]
  return next
}

function clearLocalIntakeDraft(stepId: IntakeStepId) {
  selectedIntakeChoices.value = withoutIntakeStep(selectedIntakeChoices.value, stepId)
  intakeSupplement.value = withoutIntakeStep(intakeSupplement.value, stepId)
  deferredIntakeSteps.value = withoutIntakeStep(deferredIntakeSteps.value, stepId)
  skippedIntakeSteps.value = withoutIntakeStep(skippedIntakeSteps.value, stepId)
}

function buildIntakeContent(action: 'continue' | 'defer' | 'skip') {
  const step = currentStep.value
  if (action === 'skip') {
    return `${step.label}：跳过这个非必填项，按默认 TestClaw 策略继续规划。`
  }
  if (action === 'defer') {
    if (step.id === 'target_kind') {
      return `${step.label}：目标来源待补充；这不是可执行目标，请在计划草案中标记为待补充。`
    }
    return `${step.label}：稍后补充；先记录为待补充项，请继续确认其他计划信息。`
  }
  const parts: string[] = []
  if (currentSelectedChoice.value) {
    parts.push(`${step.label}：${choiceTitle(currentSelectedChoice.value)}。${currentSelectedChoice.value.message}`)
  }
  const supplement = currentSupplementText.value.trim()
  if (supplement) {
    parts.push(`${step.label}补充说明：${supplement}`)
  }
  return parts.join('\n')
}

function intakeMessageForStep(stepId: IntakeStepId) {
  const supplement = (intakeSupplement.value[stepId] || '').trim()
  if (supplement) return supplement
  if (stepId === 'target_kind' && currentStepId.value === 'target_kind') return draft.value.trim()
  return ''
}

function shouldConsumeDraftForIntake(stepId: IntakeStepId, message: string) {
  return stepId === 'target_kind' && Boolean(message) && !intakeSupplement.value[stepId]?.trim()
}

function statusLabel(status?: string) {
  if (status === 'ready') return '可执行'
  if (status === 'executed') return '已执行'
  return '收集中'
}

function statusClass(status?: string) {
  if (status === 'ready') return 'border-emerald-200 bg-emerald-50 text-emerald-700'
  if (status === 'executed') return 'border-blue-200 bg-blue-50 text-blue-700'
  return 'border-gray-200 bg-gray-50 text-gray-600'
}

function messageQuestionOptions(message: PlanMessage) {
  const questionOptions = message.plan?.question_options
  return Array.isArray(questionOptions)
    ? questionOptions
        .filter((group) => group?.question && Array.isArray(group.options) && group.options.length)
        .slice(0, 2)
    : []
}

function messageClass(role: string) {
  if (role === 'user') return 'ml-auto bg-gray-950 text-white'
  if (role === 'system') return 'mx-auto bg-amber-50 text-amber-800 border border-amber-200'
  return 'mr-auto bg-white text-gray-900 border border-gray-200'
}

function errorMessage(error: any, fallback: string) {
  const detail = error.response?.data?.detail
  const directMessage = typeof error?.message === 'string' ? error.message.trim() : ''
  const known: Record<string, string> = {
    'content is required': '请输入需求内容',
    'Planning session not found': '计划会话不存在',
    'Planning message not found': '消息不存在',
    'No current plan to reject': '当前没有可拒绝的计划',
    'Executed plan cannot be rejected': '已执行的计划不能再拒绝',
    'Executed plan cannot be changed': '已执行的计划不能再修改',
    'No executable plan is ready': '当前没有可执行的计划',
    'Only user messages can be edited': '只能编辑用户消息',
    'source is required': '请补充测试目标或接口文档地址',
  }
  if (typeof detail !== 'string' || !detail.trim()) {
    if (known[directMessage]) return known[directMessage]
    if (directMessage && /[\u3400-\u9fff]/.test(directMessage)) return directMessage
    return fallback
  }
  const normalized = detail.trim()
  if (known[normalized]) return known[normalized]
  if (/New runs accept test_type values/i.test(normalized)) return '测试类型不支持，请选择 API、UI 或自动模式。'
  if (/[\u3400-\u9fff]/.test(normalized)) return normalized
  return fallback
}

function setActiveSession(session: PlanningSession) {
  upsertSession(session)
  activeSession.value = session
  editingRollbackSnapshot.value = null
}

function upsertSession(session: PlanningSession) {
  const index = sessions.value.findIndex((item) => item.id === session.id)
  if (index >= 0) {
    sessions.value[index] = { ...sessions.value[index], ...session }
  } else {
    sessions.value.unshift(session)
  }
  sessions.value.sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')))
}

function resetIntakeState() {
  selectedIntakeChoices.value = {}
  intakeSupplement.value = {}
  deferredIntakeSteps.value = {}
  skippedIntakeSteps.value = {}
}

function resetConversationUi() {
  processEvents.value = []
  editingMessageId.value = null
  editingRollbackSnapshot.value = null
  resetIntakeState()
}

function clonePlanningSession(session: PlanningSession): PlanningSession {
  return JSON.parse(JSON.stringify(session)) as PlanningSession
}

function mergeProcessEvent(event: PlannerProcessEvent) {
  const index = processEvents.value.findIndex((item) => item.code === event.code)
  if (index >= 0) {
    processEvents.value[index] = { ...processEvents.value[index], ...event }
  } else {
    processEvents.value.push(event)
  }
}

function mutableMessageList() {
  if (!activeSession.value) return []
  if (!activeSession.value.messages) activeSession.value.messages = []
  return activeSession.value.messages
}

function clearStalePlanState() {
  if (!activeSession.value) return
  activeSession.value = {
    ...activeSession.value,
    status: 'collecting',
    ready_to_execute: false,
    current_plan: null,
    current_run_payload: null,
  }
}

function applyEditRollback(messageId: string, content?: string) {
  if (!activeSession.value) return false
  const sourceMessages = editingRollbackSnapshot.value?.messages || activeSession.value.messages || []
  const index = sourceMessages.findIndex((message) => message.id === messageId)
  if (index < 0) return false
  const retained = sourceMessages.slice(0, index + 1).map((message) => ({ ...message }))
  if (content !== undefined) {
    retained[index] = { ...retained[index], content }
  }
  activeSession.value = {
    ...activeSession.value,
    status: 'collecting',
    ready_to_execute: false,
    current_plan: null,
    current_run_payload: null,
    messages: retained,
  }
  return true
}

function appendAssistantDelta(messageId: string, delta: string) {
  const list = mutableMessageList()
  const index = list.findIndex((message) => message.id === messageId)
  if (index >= 0) {
    list[index] = { ...list[index], content: `${list[index].content}${delta}` }
  }
}

function addOptimisticTurn(content: string) {
  const list = mutableMessageList()
  const now = new Date().toISOString()
  const assistantId = `stream-assistant-${Date.now()}`
  list.push({
    id: `stream-user-${Date.now()}`,
    role: 'user',
    content,
    created_at: now,
  })
  list.push({
    id: assistantId,
    role: 'assistant',
    content: '',
    created_at: now,
  })
  clearStalePlanState()
  return assistantId
}

function applyOptimisticEdit(messageId: string, content: string) {
  const list = mutableMessageList()
  const index = list.findIndex((message) => message.id === messageId)
  const assistantId = `stream-assistant-${Date.now()}`
  if (index >= 0) {
    const retained = list.slice(0, index + 1)
    retained[index] = { ...retained[index], content }
    retained.push({
      id: assistantId,
      role: 'assistant',
      content: '',
      created_at: new Date().toISOString(),
    })
    activeSession.value = {
      ...activeSession.value!,
      messages: retained,
    }
    clearStalePlanState()
  }
  return assistantId
}

async function scrollChat() {
  await nextTick()
  chatEnd.value?.scrollIntoView({ block: 'end' })
}

async function focusDraftInput() {
  await nextTick()
  await chatInput.value?.focus()
}

function parseSseBlock(block: string) {
  let event = 'message'
  const dataLines: string[] = []
  block.split(/\r?\n/).forEach((line) => {
    if (line.startsWith('event:')) {
      event = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart())
    }
  })
  const rawData = dataLines.join('\n')
  if (!rawData) return null
  return {
    event,
    data: JSON.parse(rawData),
  }
}

async function handlePlannerStreamEvent(
  eventName: string,
  data: any,
  assistantMessageId: string,
) {
  if (eventName === 'process') {
    mergeProcessEvent(data)
    return
  }
  if (eventName === 'token') {
    appendAssistantDelta(assistantMessageId, String(data?.delta || ''))
    await scrollChat()
    return
  }
  if (eventName === 'final') {
    if (Array.isArray(data?.process_events)) {
      processEvents.value = data.process_events
    }
    if (data?.session) {
      setActiveSession(data.session)
      resetIntakeState()
    }
    await scrollChat()
    return
  }
  if (eventName === 'error') {
    throw new Error(String(data?.detail || '规划失败'))
  }
}

async function consumePlannerStream(response: Response, assistantMessageId: string) {
  const reader = response.body?.getReader()
  if (!reader) throw new Error('无法读取规划流')
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    buffer = buffer.replace(/\r\n/g, '\n')
    let boundary = buffer.indexOf('\n\n')
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary).trim()
      buffer = buffer.slice(boundary + 2)
      if (block) {
        const parsed = parseSseBlock(block)
        if (parsed) {
          await handlePlannerStreamEvent(parsed.event, parsed.data, assistantMessageId)
        }
      }
      boundary = buffer.indexOf('\n\n')
    }
  }

  buffer += decoder.decode()
  const block = buffer.trim()
  if (block) {
    const parsed = parseSseBlock(block)
    if (parsed) {
      await handlePlannerStreamEvent(parsed.event, parsed.data, assistantMessageId)
    }
  }
}

async function plannerFetchError(response: Response) {
  try {
    const body = await response.json()
    if (typeof body?.detail === 'string' && body.detail.trim()) return body.detail.trim()
  } catch {
    const text = await response.text().catch(() => '')
    if (text.trim()) return text.trim()
  }
  return '规划请求失败'
}

async function streamPlannerTurn(
  path: string,
  method: 'POST' | 'PUT',
  content: string,
  assistantMessageId: string,
) {
  streamAbortController?.abort()
  streamAbortController = new AbortController()
  const token = localStorage.getItem('testclaw_token')
  const response = await fetch(apiUrl(path), {
    method,
    signal: streamAbortController.signal,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ content }),
  })

  if (response.status === 401) {
    localStorage.removeItem('testclaw_token')
    window.location.href = '/login'
    throw new Error('登录已过期')
  }
  if (!response.ok) {
    throw new Error(await plannerFetchError(response))
  }
  await consumePlannerStream(response, assistantMessageId)
}

async function loadSessions() {
  loading.value = true
  try {
    const response = await api.get<PlanningSession[]>('/agent-plans')
    sessions.value = response.data
    if (sessions.value.length) {
      await selectSession(sessions.value[0].id)
    } else {
      await createSession()
    }
  } finally {
    loading.value = false
  }
}

async function initializePage() {
  const qualityMemoryContent = importedQualityMemoryPlanContent()
  const assetContent = qualityMemoryContent ? '' : importedAssetPlanContent()
  const importedContent = qualityMemoryContent || assetContent
  if (!importedContent) {
    await loadSessions()
    return
  }

  loading.value = true
  try {
    await createSession()
    if (qualityMemoryContent) {
      await clearImportedQualityMemoryQuery()
    } else {
      await clearImportedAssetQuery()
    }
    const sent = await submitPlannerContent(importedContent, importedContent)
    if (!sent) {
      draft.value = importedContent
    }
  } finally {
    loading.value = false
  }
}

async function createSession() {
  creating.value = true
  executeError.value = ''
  try {
    const response = await api.post<PlanningSession>('/agent-plans', {})
    resetConversationUi()
    setActiveSession(response.data)
    draft.value = ''
    await scrollChat()
  } finally {
    creating.value = false
  }
}

async function selectSession(id: string) {
  const response = await api.get<PlanningSession>(`/agent-plans/${id}`)
  resetConversationUi()
  setActiveSession(response.data)
  executeError.value = ''
  await scrollChat()
}

async function submitPlannerContent(content: string, restoreDraftContent = '') {
  if (!content || sending.value) return false
  if (!activeSession.value) {
    await createSession()
  }
  if (!activeSession.value) return false
  if (!canModifyActiveSession.value) {
    toast.error(errorMessage(new Error('Executed plan cannot be changed'), '已执行的计划不能再修改'))
    return false
  }
  sending.value = true
  executeError.value = ''
  processEvents.value = []
  const sessionId = activeSession.value.id
  const assistantId = addOptimisticTurn(content)
  try {
    draft.value = ''
    await scrollChat()
    await streamPlannerTurn(`/agent-plans/${sessionId}/messages/stream`, 'POST', content, assistantId)
    await scrollChat()
    return true
  } catch (error: any) {
    draft.value = restoreDraftContent
    await selectSession(sessionId).catch(() => undefined)
    toast.error(errorMessage(error, '发送失败'))
    return false
  } finally {
    sending.value = false
  }
}

async function submitStructuredIntake(action: 'continue' | 'defer' | 'skip') {
  if (!activeSession.value || sending.value || intakeControlsDisabled.value) return false
  const stepId = currentStepId.value
  const selectedOption = selectedIntakeChoices.value[stepId] || null
  const message = intakeMessageForStep(stepId)
  if (action === 'continue' && !selectedOption && !message) return false
  const consumeDraft = shouldConsumeDraftForIntake(stepId, message)
  const sessionId = activeSession.value.id
  sending.value = true
  executeError.value = ''
  processEvents.value = []
  try {
    const response = await api.post(`/agent-plans/sessions/${sessionId}/intake`, {
      action,
      current_step: stepId,
      selected_option: selectedOption,
      message: message || null,
    })
    if (response.data?.session) {
      setActiveSession(response.data.session)
    }
    if (consumeDraft) {
      draft.value = ''
    }
    clearLocalIntakeDraft(stepId)
    await scrollChat()
    return true
  } catch (error: any) {
    toast.error(errorMessage(error, '继续规划失败'))
    return false
  } finally {
    sending.value = false
  }
}

async function sendMessage() {
  const content = draft.value.trim()
  if (!content || sending.value) return
  if (editingMessageId.value) {
    await resendEditedMessage(content)
    return
  }
  await submitPlannerContent(content, content)
}

async function continueIntake() {
  if (!canContinueIntake.value || sending.value || intakeControlsDisabled.value) return
  await submitStructuredIntake('continue')
}

async function deferCurrentStep() {
  if (sending.value || intakeControlsDisabled.value) return
  const stepId = currentStepId.value
  deferredIntakeSteps.value = { ...deferredIntakeSteps.value, [stepId]: true }
  skippedIntakeSteps.value = { ...skippedIntakeSteps.value, [stepId]: false }
  const sent = await submitStructuredIntake('defer')
  if (!sent) {
    deferredIntakeSteps.value = { ...deferredIntakeSteps.value, [stepId]: false }
  }
}

async function skipCurrentStep() {
  if (!canSkipCurrentStep.value || sending.value || intakeControlsDisabled.value) return
  const stepId = currentStepId.value
  skippedIntakeSteps.value = { ...skippedIntakeSteps.value, [stepId]: true }
  deferredIntakeSteps.value = { ...deferredIntakeSteps.value, [stepId]: false }
  const sent = await submitStructuredIntake('skip')
  if (!sent) {
    skippedIntakeSteps.value = { ...skippedIntakeSteps.value, [stepId]: false }
  }
}

async function resendEditedMessage(content: string) {
  if (!activeSession.value || !editingMessageId.value) return
  const sessionId = activeSession.value.id
  const messageId = editingMessageId.value
  sending.value = true
  executeError.value = ''
  processEvents.value = []
  const assistantId = applyOptimisticEdit(messageId, content)
  try {
    draft.value = ''
    editingMessageId.value = null
    await scrollChat()
    await streamPlannerTurn(
      `/agent-plans/${sessionId}/messages/${messageId}/stream`,
      'PUT',
      content,
      assistantId,
    )
  } catch (error: any) {
    draft.value = content
    editingMessageId.value = messageId
    applyEditRollback(messageId, content)
    await scrollChat()
    toast.error(errorMessage(error, '重新生成失败'))
  } finally {
    sending.value = false
  }
}

function startEditMessage(message: PlanMessage) {
  if (!activeSession.value || message.role !== 'user' || sending.value || !canModifyActiveSession.value) return
  editingRollbackSnapshot.value = clonePlanningSession(activeSession.value)
  editingMessageId.value = message.id
  draft.value = message.content
  executeError.value = ''
  processEvents.value = []
  applyEditRollback(message.id)
  scrollChat()
  focusDraftInput()
}

function cancelEdit() {
  if (editingRollbackSnapshot.value && activeSession.value?.id === editingRollbackSnapshot.value.id) {
    const restored = clonePlanningSession(editingRollbackSnapshot.value)
    upsertSession(restored)
    activeSession.value = restored
  }
  editingRollbackSnapshot.value = null
  editingMessageId.value = null
  draft.value = ''
}

async function rejectPlan() {
  if (!activeSession.value || rejecting.value) return
  const reason = draft.value.trim() || '从计划卡片拒绝'
  rejecting.value = true
  executeError.value = ''
  try {
    const response = await api.post<PlanningSession>(
      `/agent-plans/${activeSession.value.id}/reject`,
      { reason },
    )
    processEvents.value = []
    setActiveSession(response.data)
    resetIntakeState()
    draft.value = draft.value.trim()
    await scrollChat()
  } catch (error: any) {
    toast.error(errorMessage(error, '拒绝计划失败'))
  } finally {
    rejecting.value = false
  }
}

async function executePlan() {
  if (!activeSession.value || executing.value) return
  executing.value = true
  executeError.value = ''
  try {
    const response = await api.post(`/agent-plans/${activeSession.value.id}/execute`)
    const runId = response.data?.run?.id
    if (response.data?.session) {
      setActiveSession(response.data.session)
    }
    if (runId) {
      router.push(`/runs/${runId}`)
      return
    }
    executeError.value = '运行创建未返回任务编号。'
  } catch (error: any) {
    const message = errorMessage(error, '运行预检阻止了执行，请根据提示补充信息。')
    executeError.value = message
    toast.error(message)
    await nextTick()
  } finally {
    executing.value = false
  }
}

async function deleteSession(session: PlanningSession) {
  if (deletingSessionId.value) return
  if (!window.confirm('删除这个规划会话及其消息？')) return
  deletingSessionId.value = session.id
  try {
    await api.delete(`/agent-plans/${session.id}`)
    sessions.value = sessions.value.filter((item) => item.id !== session.id)
    if (activeSession.value?.id === session.id) {
      resetConversationUi()
      activeSession.value = null
      if (sessions.value.length) {
        await selectSession(sessions.value[0].id)
      } else {
        await createSession()
      }
    }
  } catch (error: any) {
    toast.error(errorMessage(error, '删除会话失败'))
  } finally {
    deletingSessionId.value = ''
  }
}

async function deleteMessage(message: PlanMessage) {
  if (!activeSession.value || deletingMessageId.value || !canModifyActiveSession.value) return
  if (!window.confirm('删除这条消息，并回滚其后的对话？')) return
  const sessionId = activeSession.value.id
  deletingMessageId.value = message.id
  try {
    const response = await api.delete<PlanningSession>(
      `/agent-plans/${sessionId}/messages/${message.id}`,
    )
    if (editingMessageId.value === message.id) {
      cancelEdit()
    }
    if (
      editingMessageId.value
      && !response.data.messages?.some((item) => item.id === editingMessageId.value)
    ) {
      cancelEdit()
    }
    processEvents.value = []
    setActiveSession(response.data)
    resetIntakeState()
    await scrollChat()
  } catch (error: any) {
    toast.error(errorMessage(error, '删除消息失败'))
  } finally {
    deletingMessageId.value = ''
  }
}

onMounted(initializePage)
onBeforeUnmount(() => {
  streamAbortController?.abort()
})
</script>

<template>
  <div class="grid h-[calc(100vh-7.25rem)] min-h-[620px] grid-cols-1 gap-4 lg:grid-cols-[260px_minmax(0,1fr)_340px]">
    <aside class="tc-card flex min-h-0 flex-col overflow-hidden">
      <div class="flex items-center justify-between border-b border-gray-100 px-3 py-3">
        <div class="min-w-0">
          <div class="text-xs font-bold uppercase text-gray-400">计划模式</div>
          <div class="truncate text-sm font-semibold text-gray-950">会话</div>
        </div>
        <button
          type="button"
          title="新建会话"
          aria-label="新建会话"
          class="rounded-lg border border-gray-200 p-2 text-gray-600 hover:bg-gray-50"
          :disabled="creating"
          @click="createSession"
        >
          <Loader2 v-if="creating" :size="16" class="animate-spin" />
          <Plus v-else :size="16" />
        </button>
      </div>

      <div class="min-h-0 flex-1 space-y-1 overflow-y-auto p-2">
        <div v-if="loading" class="flex items-center gap-2 px-2 py-3 text-xs font-semibold text-gray-500">
          <Loader2 :size="15" class="animate-spin" />
          正在加载会话
        </div>
        <div
          v-for="session in sessions"
          :key="session.id"
          class="group flex items-stretch gap-1 rounded-lg transition"
          :class="activeSession?.id === session.id ? 'bg-gray-950 text-white' : 'text-gray-700 hover:bg-gray-100'"
        >
          <button
            type="button"
            class="min-w-0 flex-1 px-3 py-2 text-left"
            @click="selectSession(session.id)"
          >
            <div class="flex items-center gap-2">
              <MessageSquare :size="15" class="shrink-0" />
              <span class="min-w-0 flex-1 truncate text-sm font-semibold">{{ session.title }}</span>
            </div>
            <div class="mt-1 flex items-center justify-between gap-2 text-[11px]">
              <span
                class="rounded-full border px-2 py-0.5 font-bold"
                :class="activeSession?.id === session.id ? 'border-white/20 bg-white/10 text-white' : statusClass(session.status)"
              >
                {{ statusLabel(session.status) }}
              </span>
              <span class="truncate opacity-70">{{ session.executed_run_id ? '已启动' : '进行中' }}</span>
            </div>
          </button>
          <button
            type="button"
            title="删除会话"
            aria-label="删除会话"
            class="my-2 mr-2 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-current opacity-60 hover:bg-white/10 hover:opacity-100 disabled:cursor-not-allowed disabled:opacity-40"
            :disabled="deletingSessionId === session.id"
            @click.stop="deleteSession(session)"
          >
            <Loader2 v-if="deletingSessionId === session.id" :size="15" class="animate-spin" />
            <Trash2 v-else :size="15" />
          </button>
        </div>
      </div>
    </aside>

    <section class="tc-card flex min-h-0 flex-col overflow-hidden">
      <div class="flex items-center justify-between border-b border-gray-100 px-4 py-3">
        <div class="min-w-0">
          <div class="flex items-center gap-2">
            <Bot :size="18" class="text-gray-700" />
            <h1 class="truncate text-base font-semibold text-gray-950">测试智能体计划</h1>
          </div>
          <p class="truncate text-xs text-gray-500">{{ activeSession?.title || '新计划' }}</p>
        </div>
        <span
          class="rounded-full border px-2.5 py-1 text-xs font-bold"
          :class="statusClass(activeSession?.status)"
        >
          {{ statusLabel(activeSession?.status) }}
        </span>
      </div>

      <div class="border-b border-gray-100 bg-white px-4 py-3">
        <div class="grid grid-cols-5 gap-2">
          <div
            v-for="(step, index) in intakeSteps"
            :key="step.id"
            class="min-w-0"
          >
            <div class="flex items-center gap-1.5">
              <div
                class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border text-xs transition"
                :class="stepCircleClass(step.id)"
              >
                <CheckCircle2 v-if="stepTone(step.id) === 'done'" :size="15" />
                <component v-else :is="step.icon" :size="15" />
              </div>
              <ChevronRight
                v-if="index < intakeSteps.length - 1"
                :size="14"
                class="hidden shrink-0 text-gray-300 sm:block"
              />
            </div>
            <div
              class="mt-1 truncate text-[11px] font-bold"
              :class="stepTextClass(step.id)"
            >
              {{ step.label }}
            </div>
          </div>
        </div>
      </div>

      <div v-if="processEvents.length" class="border-b border-gray-100 bg-white px-4 py-2">
        <div class="flex flex-wrap gap-2">
          <div
            v-for="event in processEvents"
            :key="event.code"
            class="inline-flex items-center gap-1.5 rounded-full border border-gray-200 bg-gray-50 px-2.5 py-1 text-xs font-semibold text-gray-600"
          >
            <Loader2 v-if="sending && event.status === 'active'" :size="13" class="animate-spin" />
            <CheckCircle2 v-else :size="13" class="text-emerald-600" />
            <span>{{ event.label }}</span>
          </div>
        </div>
      </div>

      <div class="min-h-0 flex-1 overflow-y-auto bg-[#f8faf9] px-4 py-4">
        <div class="space-y-4">
          <div
            v-if="executeError"
            class="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm leading-6 text-red-700"
          >
            <AlertTriangle :size="16" class="mt-0.5 shrink-0" />
            <div>
              <div class="font-semibold text-red-800">执行未启动</div>
              <div>{{ executeError }}</div>
            </div>
          </div>

          <AgentQuestionCard
            :current-step="currentStep"
            :question-group="displayedIntakeGroup"
            :status="currentDraftStatus"
            :selected-choice="currentSelectedChoice"
            v-model:supplement="currentSupplementText"
            :can-skip="canSkipCurrentStep"
            :can-continue="canContinueIntake"
            :sending="sending"
            :disabled="intakeControlsDisabled"
            @select="selectIntakeChoice"
            @skip="skipCurrentStep"
            @defer="deferCurrentStep"
            @continue="continueIntake"
          />

          <div v-if="!messages.length" class="rounded-lg border border-dashed border-gray-300 bg-white px-4 py-4 text-center">
            <Bot :size="24" class="mx-auto mb-2 text-gray-500" />
            <div class="text-sm font-semibold text-gray-900">还没有需求</div>
            <div class="mt-1 text-xs leading-5 text-gray-500">先描述目标、范围、鉴权约束和安全边界。</div>
          </div>

          <div v-else class="space-y-3">
          <div
            v-for="message in messages"
            :key="message.id"
            class="group flex items-start gap-2"
            :class="message.role === 'user' ? 'justify-end' : message.role === 'system' ? 'justify-center' : 'justify-start'"
          >
            <div
              v-if="message.role === 'user' && canModifyActiveSession"
              class="mt-1 flex shrink-0 gap-1 opacity-0 transition group-hover:opacity-100"
            >
              <button
                type="button"
                title="编辑消息"
                aria-label="编辑消息"
                class="flex h-7 w-7 items-center justify-center rounded-lg border border-gray-200 bg-white text-gray-500 hover:text-gray-900 disabled:cursor-not-allowed disabled:opacity-40"
                :disabled="sending"
                @click="startEditMessage(message)"
              >
                <Pencil :size="14" />
              </button>
              <button
                type="button"
                title="删除消息"
                aria-label="删除消息"
                class="flex h-7 w-7 items-center justify-center rounded-lg border border-gray-200 bg-white text-gray-500 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-40"
                :disabled="sending || deletingMessageId === message.id"
                @click="deleteMessage(message)"
              >
                <Loader2 v-if="deletingMessageId === message.id" :size="14" class="animate-spin" />
                <Trash2 v-else :size="14" />
              </button>
            </div>
            <div
              class="max-w-[86%] rounded-lg px-3 py-2 text-sm leading-6 shadow-sm"
              :class="messageClass(message.role)"
            >
              <div v-if="message.content" class="whitespace-pre-wrap break-words">{{ message.content }}</div>
              <div v-else class="flex items-center gap-2 text-gray-500">
                <Loader2 :size="15" class="animate-spin" />
                <span>正在生成回复</span>
              </div>
            </div>
            <div
              v-if="message.role !== 'user' && canModifyActiveSession"
              class="mt-1 flex shrink-0 gap-1 opacity-0 transition group-hover:opacity-100"
            >
              <button
                type="button"
                title="删除消息"
                aria-label="删除消息"
                class="flex h-7 w-7 items-center justify-center rounded-lg border border-gray-200 bg-white text-gray-500 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-40"
                :disabled="sending || deletingMessageId === message.id"
                @click="deleteMessage(message)"
              >
                <Loader2 v-if="deletingMessageId === message.id" :size="14" class="animate-spin" />
                <Trash2 v-else :size="14" />
              </button>
            </div>
          </div>
          <div ref="chatEnd" />
        </div>
      </div>
      </div>

      <AgentChatInput
        ref="chatInput"
        v-model="draft"
        :sending="sending"
        :disabled="!canModifyActiveSession"
        :editing-message-id="editingMessageId"
        :rejection-reason="activeRejectionReason"
        @send="sendMessage"
        @cancel-edit="cancelEdit"
      />
    </section>

    <AgentPlanDraft
      :draft-items="planDraftItems"
      :current-plan="currentPlan"
      :current-payload="currentPayload"
      :scope-items="scopeItems"
      :step-items="stepItems"
      :safety-items="safetyItems"
      :plan-ready="planReady"
      :execute-error="executeError"
      :rejecting="rejecting"
      :executing="executing"
      :show-actions="activeSession?.status !== 'executed'"
      @reject="rejectPlan"
      @execute="executePlan"
    />
  </div>
</template>
