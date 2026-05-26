<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../lib/api'
import { useToast } from '../composables/useToast'
import StyledSelect from '../components/StyledSelect.vue'
import RunAuthPreflightCard from '../components/run/RunAuthPreflightCard.vue'
import RunHandoffPreview from '../components/run/RunHandoffPreview.vue'
import RunMissionCard from '../components/run/RunMissionCard.vue'
import RunModeSelector from '../components/run/RunModeSelector.vue'
import RunPolicySelector from '../components/run/RunPolicySelector.vue'
import RunPreflightStatusCard from '../components/run/RunPreflightStatusCard.vue'
import {
  AlertTriangle,
  Bot,
  FileJson,
  Globe,
  KeyRound,
  Loader2,
  Play,
  RefreshCw,
  Route,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Target,
  Terminal,
} from 'lucide-vue-next'

type PreflightCheck = {
  key: string
  label: string
  status: string
  detail: string
  action?: string | null
}

type AuthPreflightStep = {
  key: string
  label: string
  status: string
  detail: string
}

type AuthPreflightValidation = {
  method: string
  url: string
  status: string
  status_code?: number | null
  detail: string
}

type AuthPreflight = {
  auth_preflight_id?: string | null
  auth_mode: string
  captcha_mode: string
  status: string
  strategy: string
  plan: string
  captcha_handling: string
  steps: AuthPreflightStep[]
  missing_fields: string[]
  validation_results: AuthPreflightValidation[]
  auth_header_name?: string | null
  protected_validation_count: number
  can_start: boolean
  next_action?: string | null
}

type MissionCorrectionPrompt = {
  key: string
  label: string
  status: string
  detail: string
  action?: string | null
}

type MissionCounts = {
  endpoint_count?: number | null
  estimated_executable_count?: number | null
  estimated_skipped_count?: number | null
  auth_required_count?: number | null
  flow_step_count: number
  check_count: number
  ready_count: number
  review_count: number
  blocked_count: number
}

type MissionPreview = {
  handoff: string
  readiness: string
  target: string
  input_mode: string
  test_mode: string
  objective: string
  scope: string
  execution_policy: string
  safety_boundary: string
  auth_readiness: string
  counts: MissionCounts
  correction_prompts: MissionCorrectionPrompt[]
}

type TargetMemoryLastRun = {
  run_id: string
  status: string
  test_type?: string | null
  created_at?: string | null
}

type TargetMemoryTheme = {
  theme: string
  category: string
  count: number
  severity: string
  surfaces: string[]
  last_seen?: string | null
  recommended_action: string
}

type TargetMemoryBlocker = {
  category: string
  label: string
  count: number
  detail: string
  last_seen?: string | null
}

type TargetMemorySuite = {
  suite_id: string
  label: string
  case_count: number
}

type TargetMemory = {
  target: string
  previous_run_count: number
  target_run_count: number
  host_run_count: number
  last_run?: TargetMemoryLastRun | null
  recurring_failure_themes: TargetMemoryTheme[]
  known_blockers: TargetMemoryBlocker[]
  reusable_suite_count: number
  reusable_case_count: number
  reusable_suites: TargetMemorySuite[]
  suggested_strategy: string
  confidence: string
  confidence_reason: string
}

type PreflightResponse = {
  input_type: string
  test_type: string
  target_url: string
  expected_flow: string[]
  readiness: string
  checks: PreflightCheck[]
  mission_preview?: MissionPreview | null
  target_memory?: TargetMemory | null
  auth_preflight?: AuthPreflight | null
  warnings: string[]
  endpoint_count?: number | null
  auth_required_count?: number | null
  estimated_executable_count?: number | null
  estimated_skipped_count?: number | null
  api_execution_policy?: string
  api_path_prefix_rewrite?: { from: string; to: string } | null
  auth_resolved?: boolean
  auth_strategy?: string | null
  auth_header_name?: string | null
  auth_error?: string | null
  auth_missing_inputs?: string[]
  auth_next_action?: string | null
  auth_required_fields?: string[]
}

type ApiDocument = {
  id: string
  name?: string | null
  source_url?: string | null
  raw_content?: string | null
  format?: string | null
  parsed_endpoints?: Record<string, any>[] | null
}

const router = useRouter()
const route = useRoute()
const toast = useToast()
const submitting = ref(false)
const preflightLoading = ref(false)
const showAdvanced = ref(false)
const showTargetSettings = ref(false)
const showAuthChoices = ref(false)
const preflight = ref<PreflightResponse | null>(null)
const documents = ref<ApiDocument[]>([])
const documentsLoading = ref(false)
const selectedDocumentId = ref('')
const routeSourceForDocumentMatch = ref('')
const routeDocumentIdForSelection = ref('')
const credentialFieldsEdited = ref(false)

const form = reactive({
  source: '',
  test_type: 'api',
  objective: '',
  base_url: '',
  auth_mode: 'auto',
  captcha_mode: 'none',
  token: '',
  custom_headers: '',
  auth_refresh_enabled: false,
  auth_username: '',
  auth_password: '',
  auth_captcha: '',
  auth_login_url: '',
  auth_captcha_url: '',
  auth_method: 'POST',
  auth_content_type: 'json',
  auth_token_path: '',
  auth_header_name: 'Authorization',
  auth_token_prefix: 'Bearer',
  auth_login_headers: '',
  auth_login_body: '',
  api_execution_policy: 'safe_read_only',
  setup_instructions: '',
})

const modes = [
  { value: 'api', label: '接口测试', desc: '聚焦接口契约、鉴权和断言', icon: FileJson },
  { value: 'ui', label: 'UI 测试', desc: '聚焦页面路径、登录和截图证据', icon: Globe },
]

const safetyPresets = [
  '不要删除或覆盖真实数据，只验证只读路径、校验提示和取消流程。',
  '可以创建临时测试数据，但必须使用明显的 TestClaw 前缀并在完成后回滚。',
  '优先覆盖登录、核心导航、搜索/筛选、表单必填校验和错误提示。',
]

const apiPolicies = [
  { value: 'safe_read_only', label: '安全只读', desc: '默认跳过 POST/PUT/PATCH/DELETE，适合未知或真实环境。' },
  { value: 'safe_with_auth', label: '带鉴权只读', desc: '使用 Token/Header 执行只读接口，写入接口仍跳过。' },
  { value: 'write_allowed', label: '允许写入', desc: '会执行创建、修改、删除等请求，仅用于测试环境。' },
]

const authModes = [
  { value: 'auto', label: '自动获取 Token', desc: '账号密码登录，预检验证受保护接口。', icon: RefreshCw },
  { value: 'manual', label: '手动 Token/Header', desc: '直接提供当前可用鉴权信息。', icon: KeyRound },
  { value: 'none_confirmed', label: '无需鉴权', desc: '验证公开接口可访问后运行。', icon: ShieldCheck },
]

const captchaModes = [
  { value: 'none', label: '无验证码', desc: '登录链路不提交验证码。' },
  { value: 'static', label: '固定验证码', desc: '使用下方填写的验证码。' },
  { value: 'dynamic', label: '动态验证码', desc: '接口只取上下文；UI 使用 Vision 识别。' },
]

const advancedAuthInputs = new Set(['login_url', 'captcha_url', 'login_body', 'login_headers', 'token_path', 'method', 'content_type'])
const authInputLabels: Record<string, string> = {
  username: '用户名',
  password: '密码',
  captcha: '验证码',
  base_url: 'Base URL',
  login_url: '登录 URL',
  captcha_url: '验证码 URL',
  login_body: '登录请求体 JSON',
  login_headers: '登录请求头 JSON',
  token_path: 'Token 路径',
  method: '请求方法',
  content_type: 'Body 类型',
}

const defaultFlow = ['识别目标', '制定测试计划', '生成用例', '执行并采集证据', '输出报告']

const flow = computed(() => preflight.value?.expected_flow?.length ? preflight.value.expected_flow : defaultFlow)
const isApiMode = computed(() => form.test_type !== 'ui')
const selectedDocument = computed(() => documents.value.find((doc) => doc.id === selectedDocumentId.value) || null)
const localInputType = computed(() => {
  if (isApiMode.value) return selectedDocument.value ? '已保存 API 文档' : '等待选择文档'
  return detectInputType(form.source)
})
const sourceReady = computed(() => {
  if (isApiMode.value) return Boolean(selectedDocument.value && form.source.trim())
  return Boolean(form.source.trim())
})
const manualAuthSupplied = computed(() => Boolean(form.token.trim() || form.custom_headers.trim()))
const isAutoAuthMode = computed(() => form.auth_mode === 'auto')
const isManualAuthMode = computed(() => form.auth_mode === 'manual')
const manualRefreshEnabled = computed(() => isManualAuthMode.value && form.auth_refresh_enabled && manualAuthSupplied.value)
const shouldSendAuthConfig = computed(() => isApiMode.value && (isAutoAuthMode.value || manualRefreshEnabled.value))
const showLoginCredentialPanel = computed(() => isAutoAuthMode.value || (isManualAuthMode.value && form.auth_refresh_enabled))
const currentAuthMode = computed(() => authModes.find((mode) => mode.value === form.auth_mode) || authModes[0])
const alternateAuthModes = computed(() => authModes.filter((mode) => mode.value !== form.auth_mode))
const authMissingInputs = computed(() => preflight.value?.auth_missing_inputs || [])
const authRequiredFields = computed(() => preflight.value?.auth_required_fields || [])
const authMissingLabels = computed(() => authMissingInputs.value.map((key) => authInputLabels[key] || key))
const requestedAdvancedInputs = computed(() => new Set(authMissingInputs.value.filter((key) => advancedAuthInputs.has(key))))
const authNeedsAdvanced = computed(() => requestedAdvancedInputs.value.size > 0)
const manualAdvancedAuthFlow = computed(() => isManualAuthMode.value && form.auth_refresh_enabled)
const hasAnyAdvancedAuthValue = computed(() => [
  'login_url',
  'captcha_url',
  'login_body',
  'login_headers',
  'token_path',
  'method',
  'content_type',
  'header_name',
  'token_prefix',
].some((key) => hasAdvancedAuthValue(key)))
const canShowAdvancedAuthToggle = computed(() => (
  showLoginCredentialPanel.value
  && (authNeedsAdvanced.value || manualAdvancedAuthFlow.value || hasAnyAdvancedAuthValue.value || showAdvanced.value)
))
const showAdvancedAuthPanel = computed(() => showAdvanced.value && canShowAdvancedAuthToggle.value)
const showLoginRequestSettings = computed(() => ['login_url', 'method', 'content_type'].some((key) => shouldShowAdvancedField(key)))
const showTokenSettings = computed(() => ['token_path', 'header_name', 'token_prefix'].some((key) => shouldShowAdvancedField(key)))
const showLoginPayloadSettings = computed(() => ['login_body', 'login_headers'].some((key) => shouldShowAdvancedField(key)))
const showAuthPrompt = computed(() => Boolean(preflight.value?.auth_error) && authMissingInputs.value.length > 0)
const readiness = computed(() => preflight.value?.readiness || (sourceReady.value ? 'needs_review' : 'blocked'))
const readinessLabel = computed(() => {
  if (readiness.value === 'ready') return '就绪'
  if (readiness.value === 'blocked') return '阻塞'
  return '需确认'
})
const sourceMissingMessage = computed(() => (isApiMode.value ? '请选择已保存接口文档' : '请输入目标页面 URL'))
const hasBlockingPreflight = computed(() => preflight.value?.readiness === 'blocked')
const canRun = computed(() => sourceReady.value && !submitting.value && !hasBlockingPreflight.value)
const baseUrlRootWarning = computed(() => shouldOmitRootBaseUrlOverride(form.source, form.base_url))
const baseUrlForPayload = computed(() => normalizedBaseUrlOverrideForPayload(form.source, form.base_url))
const inferredTarget = computed(() => {
  if (preflight.value?.target_url) return preflight.value.target_url
  if (baseUrlForPayload.value) return baseUrlForPayload.value
  if (isApiMode.value) {
    return selectedDocument.value ? `${documentDisplayName(selectedDocument.value)}（已保存接口文档）` : '请选择接口文档'
  }
  return form.source.trim() || '等待输入目标'
})
const endpointCountLabel = computed(() => {
  const count = preflight.value?.endpoint_count
  if (count === null || count === undefined) return '运行时解析'
  return `${count} 个端点`
})
const missionPreview = computed(() => preflight.value?.mission_preview || null)
const missionCountItems = computed(() => {
  const counts = missionPreview.value?.counts
  if (!counts) return []
  const items = [
    { label: '流程步骤', value: `${counts.flow_step_count}` },
    { label: '待修正', value: `${counts.blocked_count}` },
    { label: '需确认', value: `${counts.review_count}` },
  ]
  if (counts.endpoint_count !== null && counts.endpoint_count !== undefined) {
    items.unshift({ label: '端点', value: `${counts.endpoint_count}` })
  } else if (isApiMode.value) {
    items.unshift({ label: '端点', value: '运行时解析' })
  }
  if (counts.estimated_executable_count !== null && counts.estimated_executable_count !== undefined) {
    items.push({ label: '预计执行', value: `${counts.estimated_executable_count}` })
  }
  if (counts.estimated_skipped_count) {
    items.push({ label: '策略跳过', value: `${counts.estimated_skipped_count}` })
  }
  if (counts.auth_required_count !== null && counts.auth_required_count !== undefined) {
    items.push({ label: '需鉴权', value: `${counts.auth_required_count}` })
  }
  return items
})
const missionAuthTone = computed(() => {
  const authRequiredCount = missionPreview.value?.counts.auth_required_count || 0
  if (preflight.value?.auth_error || (authRequiredCount > 0 && !preflight.value?.auth_resolved && !manualAuthSupplied.value)) {
    return 'text-amber-700'
  }
  if (authRequiredCount > 0 || preflight.value?.auth_resolved || manualAuthSupplied.value) return 'text-emerald-700'
  return 'text-gray-700'
})
const targetMemory = computed(() => preflight.value?.target_memory || null)
const targetMemoryCountItems = computed(() => {
  const memory = targetMemory.value
  if (!memory) return []
  return [
    { label: '历史运行', value: `${memory.previous_run_count}` },
    { label: '同主机', value: `${memory.host_run_count}` },
    { label: '套件', value: `${memory.reusable_suite_count}` },
    { label: '用例', value: `${memory.reusable_case_count}` },
  ]
})
const authProvidedLabel = computed(() => {
  if (isAutoAuthMode.value) return preflight.value?.auth_resolved ? '自动获取成功' : '自动获取'
  if (form.auth_mode === 'none_confirmed') return preflight.value?.auth_preflight?.can_start ? '无需鉴权已验证' : '确认无需鉴权'
  if (manualAuthSupplied.value) return form.auth_refresh_enabled ? '手动提供 + 可刷新' : '手动提供'
  return '未提供'
})
const authProvidedTone = computed(() => {
  if (isAutoAuthMode.value) return preflight.value?.auth_resolved ? 'text-emerald-700' : 'text-amber-700'
  if (form.auth_mode === 'none_confirmed') return preflight.value?.auth_preflight?.can_start ? 'text-emerald-700' : 'text-amber-700'
  if (manualAuthSupplied.value) return 'text-emerald-700'
  if (form.auth_refresh_enabled) return 'text-amber-700'
  return 'text-gray-500'
})

function detectInputType(source: string): string {
  const s = source.trim()
  if (!s) return '等待输入'
  if (s.startsWith('{') || s.startsWith('[')) return 'Swagger JSON'
  if (s.startsWith('openapi:') || s.startsWith('swagger:')) return 'Swagger YAML'
  if (/https?:\/\//.test(s)) {
    if (/swagger|openapi|api-docs/i.test(s)) return 'Swagger URL'
    return '网页 URL'
  }
  return '文本输入'
}

function queryString(value: unknown) {
  return Array.isArray(value) ? String(value[0] || '') : String(value || '')
}

function applyRoutePrefill() {
  const source = queryString(route.query.source)
  const documentId = queryString(route.query.document_id)
  const objective = queryString(route.query.objective)
  const testType = queryString(route.query.test_type)
  const baseUrl = queryString(route.query.base_url)
  const setupInstructions = queryString(route.query.setup_instructions)
  const apiPolicy = queryString(route.query.api_execution_policy)

  if (['api', 'ui'].includes(testType)) form.test_type = testType
  if (source) {
    if (form.test_type === 'ui') {
      form.source = source
    } else {
      form.source = ''
      selectedDocumentId.value = ''
      routeSourceForDocumentMatch.value = source
    }
  }
  if (documentId && form.test_type !== 'ui') {
    selectedDocumentId.value = documentId
    routeDocumentIdForSelection.value = documentId
  }
  if (objective) form.objective = objective
  if (baseUrl) {
    form.base_url = baseUrl
    showTargetSettings.value = true
  }
  if (setupInstructions) form.setup_instructions = setupInstructions
  if (apiPolicies.some((policy) => policy.value === apiPolicy)) form.api_execution_policy = apiPolicy
  if (source || documentId || objective || testType || baseUrl || setupInstructions || apiPolicy) resetPreflight()
}

function resetPreflight() {
  preflight.value = null
}

function documentSource(doc: ApiDocument) {
  return String(doc.source_url || doc.raw_content || '').trim()
}

function documentEndpointCount(doc: ApiDocument) {
  return Array.isArray(doc.parsed_endpoints) ? doc.parsed_endpoints.length : 0
}

function documentDisplayName(doc: ApiDocument) {
  return doc.name || `Document-${doc.format || 'openapi'}`
}

function normalizeSourceMatch(value: string | null | undefined) {
  return String(value || '').trim()
}

function findMatchingDocument(source: string) {
  const normalized = normalizeSourceMatch(source)
  if (!normalized) return null
  return documents.value.find((doc) => (
    normalizeSourceMatch(doc.source_url) === normalized
    || normalizeSourceMatch(doc.raw_content) === normalized
  )) || null
}

function applySavedDocument(doc: ApiDocument) {
  const source = documentSource(doc)
  if (!source) {
    selectedDocumentId.value = ''
    form.source = ''
    toast.warning('文档没有可用于运行的 source')
    return
  }
  selectedDocumentId.value = doc.id
  form.source = source
  form.base_url = ''
  resetPreflight()
}

function handleDocumentSelection() {
  routeSourceForDocumentMatch.value = ''
  routeDocumentIdForSelection.value = ''
  if (!selectedDocumentId.value) {
    form.source = ''
    resetPreflight()
    return
  }
  const doc = selectedDocument.value
  if (doc) applySavedDocument(doc)
  else {
    form.source = ''
    resetPreflight()
  }
}

function handleSourceInput() {
  routeSourceForDocumentMatch.value = ''
  routeDocumentIdForSelection.value = ''
  resetPreflight()
}

async function fetchDocuments() {
  documentsLoading.value = true
  try {
    const { data } = await api.get('/documents')
    documents.value = Array.isArray(data) ? data : []
    const routeDocumentId = routeDocumentIdForSelection.value
    if (routeDocumentId && isApiMode.value) {
      const match = documents.value.find((doc) => doc.id === routeDocumentId)
      if (match) applySavedDocument(match)
      else {
        selectedDocumentId.value = ''
        form.source = ''
        toast.warning('未找到已保存接口文档，请先在接口文档页面导入')
      }
      routeDocumentIdForSelection.value = ''
      routeSourceForDocumentMatch.value = ''
      return
    }
    const routeSource = routeSourceForDocumentMatch.value
    if (routeSource && isApiMode.value) {
      const match = findMatchingDocument(routeSource)
      if (match) applySavedDocument(match)
      else {
        selectedDocumentId.value = ''
        form.source = ''
        toast.warning('当前目标没有匹配的已保存接口文档，请先在接口文档页面导入')
      }
      routeSourceForDocumentMatch.value = ''
    }
  } catch {
    toast.error('加载已保存接口文档失败')
  } finally {
    documentsLoading.value = false
  }
}

function setExample(source: string, objective: string, mode = 'api') {
  routeSourceForDocumentMatch.value = ''
  routeDocumentIdForSelection.value = ''
  if (mode === 'api') {
    selectedDocumentId.value = ''
    form.source = ''
  } else {
    form.source = source
  }
  form.objective = objective
  form.test_type = mode
  resetPreflight()
}

function selectTestType(mode: string) {
  if (form.test_type === mode) return
  const previousDocument = selectedDocument.value
  const previousDocumentSource = previousDocument ? documentSource(previousDocument) : ''
  const switchingFromApiDocument = isApiMode.value && previousDocumentSource && form.source.trim() === previousDocumentSource

  form.test_type = mode
  routeSourceForDocumentMatch.value = ''
  routeDocumentIdForSelection.value = ''

  if (mode === 'api') {
    form.source = previousDocument ? previousDocumentSource : ''
  } else if (switchingFromApiDocument) {
    form.source = ''
  }
  resetPreflight()
}

function appendSafetyPreset(text: string) {
  form.setup_instructions = form.setup_instructions.trim()
    ? `${form.setup_instructions.trim()}\n${text}`
    : text
  resetPreflight()
}

function selectAuthMode(mode: string) {
  form.auth_mode = mode
  showAuthChoices.value = mode !== 'auto'
  showAdvanced.value = false
  resetPreflight()
  scheduleCredentialAutofillClear()
}

function handleManualRefreshToggle() {
  resetPreflight()
  scheduleCredentialAutofillClear()
}

function authInputNeeds(key: string) {
  return authMissingInputs.value.includes(key)
}

function hasAdvancedAuthValue(key: string) {
  if (key === 'login_url') return Boolean(form.auth_login_url.trim())
  if (key === 'captcha_url') return Boolean(form.auth_captcha_url.trim())
  if (key === 'login_body') return Boolean(form.auth_login_body.trim())
  if (key === 'login_headers') return Boolean(form.auth_login_headers.trim())
  if (key === 'token_path') return Boolean(form.auth_token_path.trim())
  if (key === 'method') return form.auth_method !== 'POST'
  if (key === 'content_type') return form.auth_content_type !== 'json'
  if (key === 'header_name') return form.auth_header_name.trim() !== 'Authorization'
  if (key === 'token_prefix') return form.auth_token_prefix.trim() !== 'Bearer'
  return false
}

function shouldShowAdvancedField(key: string) {
  if (manualAdvancedAuthFlow.value) return true
  if (requestedAdvancedInputs.value.has(key)) return true
  return showAdvanced.value && hasAdvancedAuthValue(key)
}

function openAdvancedAuth() {
  showAdvanced.value = true
}

function checkTone(status: string) {
  if (status === 'ready') return 'border-emerald-200 bg-emerald-50 text-emerald-700'
  if (status === 'missing') return 'border-red-200 bg-red-50 text-red-700'
  if (status === 'skipped') return 'border-gray-200 bg-gray-50 text-gray-500'
  return 'border-amber-200 bg-amber-50 text-amber-700'
}

function readinessTone(status: string) {
  if (status === 'ready') return 'border-emerald-200 bg-emerald-50 text-emerald-700'
  if (status === 'blocked') return 'border-red-200 bg-red-50 text-red-700'
  return 'border-amber-200 bg-amber-50 text-amber-700'
}

function memoryConfidenceLabel(confidence?: string) {
  if (confidence === 'high') return 'High memory'
  if (confidence === 'medium') return 'Medium memory'
  return 'Low memory'
}

function memoryConfidenceTone(confidence?: string) {
  if (confidence === 'high') return 'border-emerald-200 bg-emerald-50 text-emerald-700'
  if (confidence === 'medium') return 'border-blue-200 bg-blue-50 text-blue-700'
  return 'border-gray-200 bg-gray-50 text-gray-600'
}

function runStatusLabel(status?: string | null) {
  if (status === 'succeeded') return '通过'
  if (status === 'failed') return '失败'
  if (status === 'bug_found') return '发现缺陷'
  if (status === 'cancelled') return '已取消'
  if (status === 'running') return '运行中'
  if (status === 'queued') return '排队中'
  return status || '未知'
}

function parseHttpUrl(value: string) {
  try {
    const parsed = new URL(value)
    return ['http:', 'https:'].includes(parsed.protocol) ? parsed : null
  } catch {
    return null
  }
}

function looksLikeOpenApiDocumentUrl(source: string) {
  const parsed = parseHttpUrl(source.trim())
  if (!parsed) return false
  return /swagger|openapi|api-docs/i.test(`${parsed.pathname}${parsed.search}`)
}

function isRootPathUrl(url: URL) {
  return url.pathname.replace(/\/+$/, '') === ''
}

function shouldOmitRootBaseUrlOverride(source: string, baseUrl: string) {
  const sourceUrl = parseHttpUrl(source.trim())
  const overrideUrl = parseHttpUrl(baseUrl.trim())
  if (!sourceUrl || !overrideUrl || !looksLikeOpenApiDocumentUrl(source)) return false
  return sourceUrl.origin === overrideUrl.origin && isRootPathUrl(overrideUrl)
}

function normalizedBaseUrlOverrideForPayload(source: string, baseUrl: string) {
  const trimmed = baseUrl.trim()
  if (!trimmed) return ''
  if (shouldOmitRootBaseUrlOverride(source, trimmed)) return ''
  return trimmed
}

function markCredentialUserEdit() {
  credentialFieldsEdited.value = true
}

function handleCredentialInput() {
  resetPreflight()
}

function clearCredentialFieldsIfAutofilled() {
  if (credentialFieldsEdited.value) return
  const hadCredentials = Boolean(
    form.auth_username
    || form.auth_password
    || form.auth_captcha
    || form.token,
  )
  form.auth_username = ''
  form.auth_password = ''
  form.auth_captcha = ''
  form.token = ''
  if (hadCredentials) resetPreflight()
}

function scheduleCredentialAutofillClear() {
  clearCredentialFieldsIfAutofilled()
  const clearDelays = [50, 250, 1000]
  clearDelays.forEach((delay) => {
    window.setTimeout(clearCredentialFieldsIfAutofilled, delay)
  })
}

function buildHeaders() {
  let headers: Record<string, string> | undefined
  if (form.custom_headers.trim()) {
    headers = {}
    for (const line of form.custom_headers.trim().split('\n')) {
      const idx = line.indexOf(':')
      if (idx > 0) {
        const key = line.slice(0, idx).trim()
        const value = line.slice(idx + 1).trim()
        if (key && value) headers[key] = value
      }
    }
  }
  return headers
}

function parseJsonObject(raw: string, label: string) {
  const text = raw.trim()
  if (!text) return undefined
  let parsed: any
  try {
    parsed = JSON.parse(text)
  } catch {
    throw new Error(`${label} 必须是合法 JSON`)
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error(`${label} 必须是 JSON 对象`)
  }
  return parsed
}

function buildAuthConfig() {
  if (!shouldSendAuthConfig.value) return undefined
  const config: any = {
    enabled: true,
  }
  if (form.auth_username.trim()) config.username = form.auth_username.trim()
  if (form.auth_password.trim()) config.password = form.auth_password.trim()
  if (form.auth_captcha.trim()) config.captcha = form.auth_captcha.trim()
  if (form.auth_login_url.trim()) config.login_url = form.auth_login_url.trim()
  if (form.auth_captcha_url.trim()) config.captcha_url = form.auth_captcha_url.trim()
  if (showAdvanced.value || form.auth_method !== 'POST') config.method = form.auth_method
  if (showAdvanced.value || form.auth_content_type !== 'json') config.content_type = form.auth_content_type
  if (form.auth_token_path.trim()) config.token_path = form.auth_token_path.trim()
  if (showAdvanced.value || form.auth_header_name.trim() !== 'Authorization') {
    config.header_name = form.auth_header_name.trim() || 'Authorization'
  }
  if (showAdvanced.value || form.auth_token_prefix.trim() !== 'Bearer') {
    config.token_prefix = form.auth_token_prefix.trim()
  }
  const headers = parseJsonObject(form.auth_login_headers, '登录请求头')
  const body = parseJsonObject(form.auth_login_body, '登录请求体')
  if (headers) config.headers = headers
  if (body) config.body = body
  return config
}

function buildAuthCredentials() {
  const credentials: Record<string, string> = {}
  if (form.auth_username.trim()) credentials.username = form.auth_username.trim()
  if (form.auth_password.trim()) credentials.password = form.auth_password.trim()
  if (form.auth_captcha.trim()) credentials.captcha = form.auth_captcha.trim()
  return Object.keys(credentials).length ? credentials : undefined
}

function buildRunPayload() {
  const headers = isManualAuthMode.value ? buildHeaders() : undefined
  const payload: any = {
    source: form.source.trim(),
    test_type: form.test_type,
    auth_mode: form.auth_mode,
    captcha_mode: form.captcha_mode,
  }
  if (form.objective.trim()) payload.objective = form.objective.trim()
  if (baseUrlForPayload.value) payload.base_url = baseUrlForPayload.value
  const authCredentials = buildAuthCredentials()
  if (authCredentials) payload.auth_credentials = authCredentials
  if (isManualAuthMode.value && form.token.trim()) payload.token = form.token.trim()
  if (headers && Object.keys(headers).length) payload.headers = headers
  if (isApiMode.value) {
    payload.api_execution_policy = form.api_execution_policy
    const authConfig = buildAuthConfig()
    if (authConfig) payload.auth_config = authConfig
  }
  if (preflight.value?.auth_preflight?.auth_preflight_id) {
    payload.auth_preflight_id = preflight.value.auth_preflight.auth_preflight_id
  }
  if (form.setup_instructions.trim()) payload.setup_instructions = form.setup_instructions.trim()
  return payload
}

async function runPreflight(showToast = true) {
  if (!sourceReady.value) {
    if (showToast) toast.warning(sourceMissingMessage.value)
    return null
  }
  preflightLoading.value = true
  try {
    const { data } = await api.post('/runs/preflight', buildRunPayload())
    const result = data as PreflightResponse
    preflight.value = result
    if (result.auth_error && (result.auth_missing_inputs || []).some((key) => advancedAuthInputs.has(key))) {
      showAdvanced.value = true
    } else if (isAutoAuthMode.value && !hasAnyAdvancedAuthValue.value) {
      showAdvanced.value = false
    }
    if (showToast) toast.success('预检完成')
    return result
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || err?.message || '预检失败')
    return null
  } finally {
    preflightLoading.value = false
  }
}

async function submit() {
  if (!sourceReady.value) {
    toast.warning(sourceMissingMessage.value)
    return
  }

  if (!preflight.value) {
    const checked = await runPreflight(false)
    if (!checked) return
  }

  if (preflight.value?.readiness === 'blocked') {
    toast.error('预检未通过，请补齐阻断项后再启动任务')
    return
  }

  submitting.value = true
  try {
    const { data } = await api.post('/runs', buildRunPayload())
    toast.success('测试智能体已接收任务')
    router.push(`/runs/${data.id}`)
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || err?.message || '创建运行失败')
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  applyRoutePrefill()
  scheduleCredentialAutofillClear()
  void fetchDocuments()
})
</script>

<template>
  <div class="mx-auto max-w-7xl space-y-5 pb-10">
    <section class="border-b border-gray-200/80 pb-5">
      <div class="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div class="flex min-w-0 items-start gap-4">
          <div class="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-gray-950 text-white shadow-sm">
            <Bot :size="22" />
          </div>
          <div class="min-w-0">
            <div class="tc-page-kicker">任务控制台</div>
            <h2 class="mt-1 text-xl font-semibold tracking-tight text-gray-950">测试智能体工作台</h2>
            <p class="mt-1 max-w-3xl text-sm leading-6 text-gray-500">
              像分配测试任务一样描述目标、上下文和安全边界。TestClaw 会先预检，再自动规划 API/UI 路径、执行并沉淀证据。
            </p>
          </div>
        </div>
        <div class="flex shrink-0 flex-wrap items-center gap-2">
          <span class="rounded-lg border px-3 py-1.5 text-xs font-bold" :class="readinessTone(readiness)">
            {{ readinessLabel }}
          </span>
          <button
            @click="router.push('/history')"
            class="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-bold text-gray-600 transition-all hover:border-gray-300 hover:bg-gray-50 hover:text-gray-950"
          >
            查看历史
          </button>
        </div>
      </div>
      <div class="mt-4 flex flex-wrap gap-2 text-[11px] font-bold text-gray-500">
        <span
          v-for="(step, index) in flow.slice(0, 5)"
          :key="`${step}-${index}`"
          class="rounded-lg border border-gray-200 bg-white/80 px-2.5 py-1"
        >
          {{ index + 1 }}. {{ step }}
        </span>
      </div>
    </section>

    <div class="grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
      <section class="space-y-4">
        <RunMissionCard
          v-model:selected-document-id="selectedDocumentId"
          :form="form"
          :is-api-mode="isApiMode"
          :local-input-type="localInputType"
          :documents="documents"
          :documents-loading="documentsLoading"
          :selected-document="selectedDocument"
          :document-display-name="documentDisplayName"
          :document-endpoint-count="documentEndpointCount"
          @reset-preflight="resetPreflight"
          @document-selection="handleDocumentSelection"
          @source-input="handleSourceInput"
          @navigate-documents="router.push('/documents')"
          @set-example="setExample"
        >
          <RunModeSelector
            :model-value="form.test_type"
            :modes="modes"
            @update:model-value="selectTestType"
          />
          <RunPolicySelector
            :model-value="form.api_execution_policy"
            :is-api-mode="isApiMode"
            :api-policies="apiPolicies"
            @update:model-value="form.api_execution_policy = $event; resetPreflight()"
          />
        </RunMissionCard>

        <div class="grid gap-4 lg:grid-cols-2">
          <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
            <div class="mb-4 flex items-center gap-2">
              <Target :size="17" class="text-gray-500" />
              <h3 class="text-sm font-bold text-gray-900">目标上下文</h3>
            </div>
            <div class="space-y-4">
              <div class="rounded-lg border border-gray-200 bg-gray-50 p-3">
                <div class="mb-1 flex items-center gap-2 text-xs font-bold text-gray-700">
                  <Route :size="14" /> 推断目标
                </div>
                <p class="break-words font-mono text-xs leading-5 text-gray-500">{{ inferredTarget }}</p>
              </div>
              <button
                type="button"
                class="flex w-full items-center justify-between rounded-lg border border-gray-200 bg-white px-3 py-2 text-left text-xs font-bold text-gray-700 transition-all hover:border-blue-200 hover:text-blue-700"
                @click="showTargetSettings = !showTargetSettings"
              >
                <span class="flex items-center gap-2"><SlidersHorizontal :size="15" /> 高级/可选目标设置</span>
                <span class="text-gray-500">{{ showTargetSettings ? '收起' : '展开' }}</span>
              </button>
              <div v-if="showTargetSettings || form.base_url || authInputNeeds('base_url')" class="space-y-2">
                <label class="block text-xs font-bold uppercase tracking-widest text-gray-400">Base URL 覆盖</label>
                <input
                  v-model="form.base_url"
                  name="tc-run-target-base"
                  autocomplete="off"
                  placeholder="例如：https://api.example.com/api"
                  class="w-full rounded-lg border px-4 py-2.5 font-mono text-sm outline-none transition-all focus:bg-white"
                  :class="authInputNeeds('base_url') ? 'border-amber-400 bg-amber-50 focus:border-amber-500' : 'border-gray-200 bg-gray-50 focus:border-blue-500'"
                  @input="resetPreflight"
                />
                <p class="text-xs leading-5 text-gray-500">
                  OpenAPI/Swagger 文档通常留空，系统会使用文档 servers 推断。仅在刻意切换环境时填写，并包含完整 API 基础路径，例如 /api。
                </p>
                <div v-if="baseUrlRootWarning" class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-800">
                  已忽略这个 Base URL 覆盖：它只是文档同源根地址，可能丢失 servers 中的 /api 路径。留空使用文档 servers；切换环境时请填写完整基础路径。
                </div>
              </div>
            </div>
          </div>

          <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
            <div class="mb-4 flex items-center gap-2">
              <ShieldCheck :size="17" class="text-gray-500" />
              <h3 class="text-sm font-bold text-gray-900">安全边界</h3>
            </div>
            <textarea
              v-model="form.setup_instructions"
              rows="5"
              placeholder="账号、验证码、测试范围、禁止操作、允许写入的数据类型..."
              class="w-full resize-none rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-sm outline-none transition-all focus:border-blue-500 focus:bg-white"
              @input="resetPreflight"
            />
            <div class="mt-3 flex flex-wrap gap-2">
              <button
                v-for="preset in safetyPresets"
                :key="preset"
                @click="appendSafetyPreset(preset)"
                class="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-left text-[11px] font-bold leading-4 text-gray-600 transition-all hover:border-emerald-200 hover:text-emerald-700"
              >
                {{ preset }}
              </button>
            </div>
          </div>
        </div>

        <RunAuthPreflightCard>
          <div class="rounded-lg border border-blue-100 bg-blue-50/70 px-4 py-3">
            <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div class="flex min-w-0 items-start gap-3">
                <div class="mt-0.5 rounded-lg bg-white p-2 text-blue-700">
                  <component :is="currentAuthMode.icon" :size="17" />
                </div>
                <div class="min-w-0">
                  <div class="text-xs font-bold uppercase tracking-widest text-blue-500">当前鉴权方式</div>
                  <div class="mt-1 text-sm font-bold text-blue-950">{{ currentAuthMode.label }}</div>
                  <p class="mt-1 text-xs leading-5 text-blue-700">{{ currentAuthMode.desc }}</p>
                </div>
              </div>
              <button
                @click="showAuthChoices = !showAuthChoices"
                class="inline-flex shrink-0 items-center justify-center gap-2 rounded-lg border border-blue-200 bg-white px-3 py-2 text-xs font-bold text-blue-800 transition-all hover:border-blue-300"
              >
                <Settings2 :size="15" />
                其他方式
              </button>
            </div>
          </div>

          <div v-if="showAuthChoices" class="mt-3 grid gap-3 md:grid-cols-2">
            <button
              v-for="mode in alternateAuthModes"
              :key="mode.value"
              @click="selectAuthMode(mode.value)"
              class="min-w-0 rounded-lg border border-gray-200 bg-white p-3 text-left text-gray-700 transition-all hover:border-blue-300 hover:text-blue-800"
            >
              <div class="mb-1 flex items-center gap-2">
                <component :is="mode.icon" :size="16" />
                <span class="text-sm font-bold">{{ mode.label }}</span>
              </div>
              <p class="text-xs leading-5 text-gray-500">{{ mode.desc }}</p>
            </button>
          </div>

          <div v-if="isManualAuthMode" class="mt-4 space-y-4">
            <div class="grid gap-4 lg:grid-cols-2">
              <div>
                <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-gray-400">认证 Token</label>
                  <input
                    v-model="form.token"
                    type="password"
                    name="tc-run-manual-token"
                    autocomplete="new-password"
                    data-lpignore="true"
                    data-1p-ignore="true"
                    spellcheck="false"
                    placeholder="粘贴 Token 或 API Key"
                    class="w-full rounded-lg border border-gray-200 bg-gray-50 px-4 py-2.5 font-mono text-sm outline-none transition-all focus:border-blue-500 focus:bg-white"
                    @beforeinput="markCredentialUserEdit"
                    @keydown="markCredentialUserEdit"
                    @paste="markCredentialUserEdit"
                    @input="handleCredentialInput"
                  />
              </div>
              <div>
                <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-gray-400">自定义请求头</label>
                  <textarea
                    v-model="form.custom_headers"
                    rows="3"
                    autocomplete="off"
                    spellcheck="false"
                    placeholder="每行一个，格式：Header-Name: value"
                    class="w-full resize-none rounded-lg border border-gray-200 bg-gray-50 px-4 py-2.5 font-mono text-sm outline-none transition-all focus:border-blue-500 focus:bg-white"
                    @input="resetPreflight"
                />
              </div>
            </div>

            <label v-if="isApiMode" class="flex cursor-pointer items-start gap-3 rounded-lg border border-gray-200 bg-gray-50 px-3 py-3 text-xs font-bold text-gray-700">
              <input
                v-model="form.auth_refresh_enabled"
                type="checkbox"
                class="mt-0.5 h-4 w-4 rounded border-gray-300 text-blue-600"
                @change="handleManualRefreshToggle"
              />
              <span>
                <span class="block text-gray-900">过期时自动重新获取</span>
                <span class="mt-1 block font-normal leading-5 text-gray-500">
                  先使用你提供的 Token/Header 执行；如果返回 401/403，再用下面的登录凭据刷新鉴权。
                </span>
              </span>
            </label>

            <div v-if="isApiMode && form.auth_refresh_enabled && !manualAuthSupplied" class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-bold text-amber-800">
              手动模式需要先提供当前 Token/Header，自动刷新只负责 Token 过期后的重取。
            </div>
          </div>

          <div v-if="isAutoAuthMode" class="mt-4 rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-xs leading-5 text-blue-800">
            {{ isApiMode ? '预检会从 OpenAPI 文档推断登录接口、换取 Token，并验证受保护只读接口。' : 'UI 测试会打开登录页，根据页面结构完成登录并验证登录后页面。' }}
          </div>

          <div v-if="showLoginCredentialPanel" class="mt-4 space-y-4 rounded-lg border border-emerald-100 bg-emerald-50/60 p-4">
            <div class="flex items-center justify-between gap-3">
              <div>
                <div class="text-sm font-bold text-emerald-900">
                  {{ isAutoAuthMode ? '自动获取 Token' : 'Token 自动刷新凭据' }}
                </div>
                <p class="mt-1 text-xs leading-5 text-emerald-700">
                  {{ isAutoAuthMode ? '只需要账号、密码和验证码策略；接口测试不会做图片验证码识别。' : '手动 Token 失效后才会使用这些信息重新登录。' }}
                </p>
              </div>
            </div>

            <div class="grid gap-4 md:grid-cols-3">
              <div>
                <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-emerald-700">用户名</label>
                  <input
                    v-model="form.auth_username"
                    name="tc-run-login-identity"
                    autocomplete="off"
                    autocapitalize="none"
                    spellcheck="false"
                    placeholder="请输入账号"
                    class="w-full rounded-lg border px-3 py-2.5 text-sm outline-none transition-all"
                    :class="authInputNeeds('username') ? 'border-amber-400 bg-amber-50 focus:border-amber-500' : 'border-emerald-200 bg-white focus:border-emerald-500'"
                    @beforeinput="markCredentialUserEdit"
                    @keydown="markCredentialUserEdit"
                    @paste="markCredentialUserEdit"
                    @input="handleCredentialInput"
                  />
              </div>
              <div>
                <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-emerald-700">密码</label>
                  <input
                    v-model="form.auth_password"
                    type="password"
                    name="tc-run-login-secret"
                    autocomplete="new-password"
                    data-lpignore="true"
                    data-1p-ignore="true"
                    spellcheck="false"
                    placeholder="请输入密码"
                    class="w-full rounded-lg border px-3 py-2.5 text-sm outline-none transition-all"
                    :class="authInputNeeds('password') ? 'border-amber-400 bg-amber-50 focus:border-amber-500' : 'border-emerald-200 bg-white focus:border-emerald-500'"
                    @beforeinput="markCredentialUserEdit"
                    @keydown="markCredentialUserEdit"
                    @paste="markCredentialUserEdit"
                    @input="handleCredentialInput"
                  />
              </div>
              <div v-if="form.captcha_mode === 'static'">
                <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-emerald-700">验证码</label>
                  <input
                    v-model="form.auth_captcha"
                    name="tc-run-login-code"
                    autocomplete="off"
                    spellcheck="false"
                    placeholder="请输入验证码"
                    class="w-full rounded-lg border px-3 py-2.5 text-sm outline-none transition-all"
                    :class="authInputNeeds('captcha') ? 'border-amber-400 bg-amber-50 focus:border-amber-500' : 'border-emerald-200 bg-white focus:border-emerald-500'"
                    @beforeinput="markCredentialUserEdit"
                    @keydown="markCredentialUserEdit"
                    @paste="markCredentialUserEdit"
                    @input="handleCredentialInput"
                  />
              </div>
            </div>

            <div>
              <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-emerald-700">验证码策略</label>
              <div class="grid gap-3 md:grid-cols-3">
                <button
                  v-for="mode in captchaModes"
                  :key="mode.value"
                  @click="form.captcha_mode = mode.value; resetPreflight()"
                  class="min-w-0 rounded-lg border p-3 text-left transition-all"
                  :class="form.captcha_mode === mode.value ? 'border-emerald-500 bg-white text-emerald-800' : 'border-emerald-100 bg-emerald-50 text-emerald-700 hover:border-emerald-300'"
                >
                  <div class="text-xs font-bold">{{ mode.label }}</div>
                  <p class="mt-1 text-[11px] leading-4 opacity-80">{{ mode.desc }}</p>
                </button>
              </div>
            </div>

            <button
              v-if="canShowAdvancedAuthToggle"
              @click="showAdvanced = !showAdvanced"
              class="flex w-full items-center justify-between rounded-lg border border-emerald-200 bg-white px-3 py-2 text-left text-xs font-bold text-emerald-800 transition-all hover:border-emerald-300"
            >
              <span class="flex items-center gap-2"><SlidersHorizontal :size="15" /> 补充登录字段</span>
              <span class="text-emerald-600">{{ showAdvanced ? '收起' : '展开' }}</span>
            </button>

            <div v-if="showAdvancedAuthPanel" class="space-y-4">
              <div v-if="showLoginRequestSettings" class="grid gap-4 lg:grid-cols-[minmax(0,1fr)_130px_130px]">
                <div v-if="shouldShowAdvancedField('login_url')">
                  <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-gray-400">登录 URL</label>
                    <input
                      v-model="form.auth_login_url"
                      name="tc-run-login-endpoint"
                      autocomplete="off"
                      spellcheck="false"
                      placeholder="留空时从 OpenAPI login/token 接口推断"
                      class="w-full rounded-lg border px-4 py-2.5 font-mono text-sm outline-none transition-all"
                    :class="authInputNeeds('login_url') ? 'border-amber-400 bg-amber-50 focus:border-amber-500' : 'border-gray-200 bg-white focus:border-blue-500'"
                    @input="resetPreflight"
                  />
                </div>
                <div v-if="shouldShowAdvancedField('method')">
                  <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-gray-400">方法</label>
                  <StyledSelect
                    v-model="form.auth_method"
                    @change="resetPreflight"
                  >
                    <option value="POST">POST</option>
                    <option value="PUT">PUT</option>
                    <option value="PATCH">PATCH</option>
                  </StyledSelect>
                </div>
                <div v-if="shouldShowAdvancedField('content_type')">
                  <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-gray-400">Body 类型</label>
                  <StyledSelect
                    v-model="form.auth_content_type"
                    @change="resetPreflight"
                  >
                    <option value="json">JSON</option>
                    <option value="form">Form</option>
                  </StyledSelect>
                </div>
              </div>

              <div v-if="isApiMode && shouldShowAdvancedField('captcha_url')">
                <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-gray-400">验证码 URL</label>
                  <input
                    v-model="form.auth_captcha_url"
                    name="tc-run-captcha-endpoint"
                    autocomplete="off"
                    spellcheck="false"
                    placeholder="留空时从 OpenAPI captcha/verifyCode 接口推断"
                    class="w-full rounded-lg border px-4 py-2.5 font-mono text-sm outline-none transition-all"
                  :class="authInputNeeds('captcha_url') ? 'border-amber-400 bg-amber-50 focus:border-amber-500' : 'border-gray-200 bg-white focus:border-blue-500'"
                  @input="resetPreflight"
                />
              </div>

              <div v-if="showTokenSettings" class="grid gap-4 lg:grid-cols-3">
                <div v-if="shouldShowAdvancedField('token_path')">
                  <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-gray-400">Token 路径</label>
                    <input
                      v-model="form.auth_token_path"
                      name="tc-run-token-json-path"
                      autocomplete="off"
                      spellcheck="false"
                      placeholder="留空时自动识别 access_token/data.token"
                      class="w-full rounded-lg border px-4 py-2.5 font-mono text-sm outline-none transition-all"
                    :class="authInputNeeds('token_path') ? 'border-amber-400 bg-amber-50 focus:border-amber-500' : 'border-gray-200 bg-white focus:border-blue-500'"
                    @input="resetPreflight"
                  />
                </div>
                <div v-if="shouldShowAdvancedField('header_name')">
                  <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-gray-400">注入 Header</label>
                  <input
                    v-model="form.auth_header_name"
                    placeholder="Authorization"
                    class="w-full rounded-lg border border-gray-200 bg-white px-4 py-2.5 font-mono text-sm outline-none transition-all focus:border-blue-500"
                    @input="resetPreflight"
                  />
                </div>
                <div v-if="shouldShowAdvancedField('token_prefix')">
                  <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-gray-400">Token 前缀</label>
                  <input
                    v-model="form.auth_token_prefix"
                    placeholder="Bearer"
                    class="w-full rounded-lg border border-gray-200 bg-white px-4 py-2.5 font-mono text-sm outline-none transition-all focus:border-blue-500"
                    @input="resetPreflight"
                  />
                </div>
              </div>

              <div v-if="showLoginPayloadSettings" class="grid gap-4 lg:grid-cols-2">
                <div v-if="shouldShowAdvancedField('login_body')">
                  <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-gray-400">登录请求体 JSON</label>
                    <textarea
                      v-model="form.auth_login_body"
                      rows="6"
                      autocomplete="off"
                      spellcheck="false"
                      placeholder="{ &quot;username&quot;: &quot;请输入账号&quot;, &quot;password&quot;: &quot;请输入密码&quot; }"
                      class="w-full resize-none rounded-lg border px-4 py-3 font-mono text-sm outline-none transition-all"
                    :class="authInputNeeds('login_body') ? 'border-amber-400 bg-amber-50 focus:border-amber-500' : 'border-gray-200 bg-white focus:border-blue-500'"
                    @input="resetPreflight"
                  />
                </div>
                <div v-if="shouldShowAdvancedField('login_headers')">
                  <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-gray-400">登录请求头 JSON</label>
                    <textarea
                      v-model="form.auth_login_headers"
                      rows="6"
                      autocomplete="off"
                      spellcheck="false"
                      placeholder="{ &quot;X-Example&quot;: &quot;value&quot; }"
                      class="w-full resize-none rounded-lg border px-4 py-3 font-mono text-sm outline-none transition-all"
                    :class="authInputNeeds('login_headers') ? 'border-amber-400 bg-amber-50 focus:border-amber-500' : 'border-gray-200 bg-white focus:border-blue-500'"
                    @input="resetPreflight"
                  />
                </div>
              </div>
            </div>
          </div>

          <div
            v-if="preflight?.auth_preflight"
            class="mt-4 rounded-lg border px-3 py-2 text-xs font-bold"
            :class="preflight.auth_preflight.can_start ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-red-200 bg-red-50 text-red-700'"
          >
            {{
              preflight.auth_preflight.can_start
                ? `鉴权预检通过：${preflight.auth_preflight.strategy}`
                : `鉴权预检阻断：${preflight.auth_preflight.next_action || preflight.auth_error}`
            }}
          </div>

          <div v-if="preflight?.auth_preflight" class="mt-4 space-y-2">
            <div class="rounded-lg border border-gray-200 bg-gray-50 px-3 py-3 text-xs leading-5 text-gray-700">
              <div class="font-bold text-gray-900">{{ preflight.auth_preflight.plan }}</div>
              <div class="mt-1">{{ preflight.auth_preflight.captcha_handling }}</div>
            </div>
            <div
              v-for="step in preflight.auth_preflight.steps"
              :key="step.key"
              class="rounded-lg border px-3 py-2 text-xs"
              :class="checkTone(step.status === 'passed' ? 'ready' : step.status === 'blocked' ? 'missing' : 'warning')"
            >
              <div class="font-bold">{{ step.label }}</div>
              <div class="mt-0.5 leading-5 opacity-90">{{ step.detail }}</div>
            </div>
          </div>

          <div v-if="showAuthPrompt" class="mt-4 space-y-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            <div class="flex items-center gap-2 font-bold">
              <AlertTriangle :size="16" /> 需要补充鉴权信息
            </div>
            <p class="text-xs leading-5">{{ preflight?.auth_next_action || '根据提示补齐信息后重新运行预检。' }}</p>
            <div v-if="authRequiredFields.length" class="flex flex-wrap items-center gap-2 text-xs">
              <span class="font-bold">接口要求字段</span>
              <span
                v-for="field in authRequiredFields"
                :key="field"
                class="rounded border border-amber-300 bg-white px-2 py-1 font-mono text-[11px] text-amber-800"
              >
                {{ field }}
              </span>
            </div>
            <div class="flex flex-wrap items-center gap-2 text-xs">
              <span class="font-bold">建议填写</span>
              <span
                v-for="label in authMissingLabels"
                :key="label"
                class="rounded border border-amber-300 bg-white px-2 py-1 text-[11px] font-bold text-amber-800"
              >
                {{ label }}
              </span>
              <button
                v-if="authNeedsAdvanced"
                @click="openAdvancedAuth"
                class="rounded border border-amber-300 bg-white px-2 py-1 text-[11px] font-bold text-amber-900 transition-all hover:border-amber-500"
              >
                {{ authInputNeeds('token_path') ? '填写 Token 路径' : '补充登录字段' }}
              </button>
            </div>
          </div>
        </RunAuthPreflightCard>

        <div class="sticky bottom-0 z-20 flex flex-col gap-3 rounded-lg border border-gray-200 bg-white/95 p-4 shadow-sm backdrop-blur sm:flex-row sm:items-center sm:justify-between lg:static lg:bg-white">
          <div class="min-w-0 text-sm text-gray-600">
            <span class="font-bold text-gray-900">启动后</span>
            智能体会进入 Agent Cockpit，持续展示计划、当前动作、日志和证据。
          </div>
          <div class="flex shrink-0 gap-2">
            <button
              @click="runPreflight()"
              :disabled="preflightLoading || !sourceReady"
              class="flex items-center justify-center gap-2 rounded-lg border border-gray-200 bg-white px-4 py-2.5 text-sm font-bold text-gray-700 transition-all hover:border-blue-200 hover:text-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Loader2 v-if="preflightLoading" :size="16" class="animate-spin" />
              <RefreshCw v-else :size="16" />
              运行前预检
            </button>
            <button
              @click="submit"
              :disabled="!canRun"
              class="flex items-center justify-center gap-2 rounded-lg bg-gray-950 px-5 py-2.5 text-sm font-bold text-white transition-all hover:bg-gray-800 disabled:cursor-not-allowed disabled:bg-gray-300"
            >
              <Loader2 v-if="submitting" :size="16" class="animate-spin" />
              <Play v-else :size="16" />
              {{ submitting ? '正在启动...' : '启动测试智能体' }}
            </button>
          </div>
        </div>
      </section>

      <aside class="space-y-4 xl:sticky xl:top-4 xl:max-h-[calc(100vh-7rem)] xl:overflow-y-auto xl:pr-1">
        <RunPreflightStatusCard
          :preflight="preflight"
          :preflight-loading="preflightLoading"
          :readiness="readiness"
          :readiness-label="readinessLabel"
          :readiness-tone="readinessTone"
          :check-tone="checkTone"
        />

        <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <div class="mb-4 flex items-center justify-between gap-3">
            <div class="flex items-center gap-2">
              <Bot :size="16" class="text-gray-500" />
              <h3 class="text-sm font-bold text-gray-900">目标记忆</h3>
            </div>
            <span
              v-if="targetMemory"
              class="rounded-lg border px-2.5 py-1 text-[10px] font-bold"
              :class="memoryConfidenceTone(targetMemory.confidence)"
            >
              {{ memoryConfidenceLabel(targetMemory.confidence) }}
            </span>
          </div>

          <div v-if="targetMemory" class="space-y-4">
            <div class="space-y-1 text-xs">
              <span class="block text-gray-400">记忆目标</span>
              <span class="block break-words font-mono font-bold text-gray-800">{{ targetMemory.target }}</span>
            </div>

            <div class="grid grid-cols-2 gap-2">
              <div
                v-for="item in targetMemoryCountItems"
                :key="item.label"
                class="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2"
              >
                <div class="text-[10px] font-bold text-gray-400">{{ item.label }}</div>
                <div class="mt-0.5 text-sm font-bold text-gray-900">{{ item.value }}</div>
              </div>
            </div>

            <div v-if="targetMemory.last_run" class="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs">
              <div class="flex items-center justify-between gap-3">
                <span class="font-bold text-gray-700">上次运行</span>
                <span class="font-bold text-gray-900">{{ runStatusLabel(targetMemory.last_run.status) }}</span>
              </div>
              <div v-if="targetMemory.last_run.created_at" class="mt-1 text-[11px] text-gray-500">{{ targetMemory.last_run.created_at }}</div>
            </div>

            <div class="rounded-lg border px-3 py-3 text-xs leading-5" :class="memoryConfidenceTone(targetMemory.confidence)">
              <div class="font-bold">{{ targetMemory.suggested_strategy }}</div>
              <div class="mt-1 opacity-80">{{ targetMemory.confidence_reason }}</div>
            </div>

            <div v-if="targetMemory.known_blockers.length" class="space-y-2">
              <div class="text-xs font-bold text-gray-900">已知阻塞</div>
              <div
                v-for="blocker in targetMemory.known_blockers.slice(0, 2)"
                :key="blocker.category"
                class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800"
              >
                <div class="flex items-center justify-between gap-2 font-bold">
                  <span>{{ blocker.label }}</span>
                  <span>{{ blocker.count }} 次</span>
                </div>
                <div class="mt-1 leading-5 opacity-90">{{ blocker.detail }}</div>
              </div>
            </div>

            <div v-if="targetMemory.recurring_failure_themes.length" class="space-y-2">
              <div class="text-xs font-bold text-gray-900">反复失败主题</div>
              <div
                v-for="theme in targetMemory.recurring_failure_themes.slice(0, 2)"
                :key="`${theme.category}-${theme.theme}`"
                class="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800"
              >
                <div class="flex items-center justify-between gap-2 font-bold">
                  <span class="min-w-0 truncate">{{ theme.theme }}</span>
                  <span class="shrink-0">{{ theme.count }} 次</span>
                </div>
                <div v-if="theme.surfaces.length" class="mt-1 truncate text-[11px] opacity-80">{{ theme.surfaces.join(' / ') }}</div>
              </div>
            </div>

            <div v-if="targetMemory.reusable_suites.length" class="space-y-2">
              <div class="text-xs font-bold text-gray-900">可复用套件</div>
              <div
                v-for="suite in targetMemory.reusable_suites.slice(0, 2)"
                :key="suite.suite_id"
                class="flex items-center justify-between gap-3 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs"
              >
                <span class="min-w-0 truncate font-bold text-gray-800">{{ suite.label }}</span>
                <span class="shrink-0 text-gray-500">{{ suite.case_count }} cases</span>
              </div>
            </div>
          </div>

          <div v-else class="rounded-lg border border-dashed border-gray-200 bg-gray-50 px-4 py-8 text-center text-sm text-gray-400">
            运行预检后，智能体会展示该目标的历史运行、阻塞点和可复用资产。
          </div>
        </div>

        <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <div class="mb-4 flex items-center gap-2">
            <Terminal :size="16" class="text-gray-500" />
            <h3 class="text-sm font-bold text-gray-900">智能体执行流</h3>
          </div>
          <div class="space-y-3">
            <div
              v-for="(step, index) in flow"
              :key="`${step}-${index}`"
              class="flex gap-3"
            >
              <div class="flex flex-col items-center">
                <div class="flex h-7 w-7 items-center justify-center rounded-full border border-gray-200 bg-gray-50 text-xs font-bold text-gray-600">
                  {{ index + 1 }}
                </div>
                <div v-if="index < flow.length - 1" class="h-7 w-px bg-gray-200"></div>
              </div>
              <div class="min-w-0 pb-2 text-sm font-medium text-gray-700">{{ step }}</div>
            </div>
          </div>
        </div>

        <RunHandoffPreview
          :mission-preview="missionPreview"
          :mission-count-items="missionCountItems"
          :readiness-label="readinessLabel"
          :readiness-tone="readinessTone"
          :check-tone="checkTone"
          :mission-auth-tone="missionAuthTone"
          :local-input-type="localInputType"
          :form="form"
          :is-api-mode="isApiMode"
          :endpoint-count-label="endpointCountLabel"
          :auth-provided-tone="authProvidedTone"
          :auth-provided-label="authProvidedLabel"
        />
      </aside>
    </div>
  </div>
</template>
