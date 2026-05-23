<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../lib/api'
import { useToast } from '../composables/useToast'
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
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
  Zap,
} from 'lucide-vue-next'

type PreflightCheck = {
  key: string
  label: string
  status: string
  detail: string
  action?: string | null
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

const router = useRouter()
const toast = useToast()
const submitting = ref(false)
const preflightLoading = ref(false)
const showAdvanced = ref(false)
const preflight = ref<PreflightResponse | null>(null)

const form = reactive({
  source: '',
  test_type: 'auto',
  objective: '',
  base_url: '',
  auth_mode: 'auto',
  token: '',
  custom_headers: '',
  auth_refresh_enabled: false,
  auth_username: '',
  auth_password: '',
  auth_captcha: '',
  auth_login_url: '',
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
  { value: 'auto', label: '自动编排', desc: '根据输入决定 API/UI 路径', icon: Zap },
  { value: 'api', label: 'API 检查', desc: '聚焦接口契约和断言', icon: FileJson },
  { value: 'ui', label: 'UI 巡检', desc: '聚焦页面路径和证据截图', icon: Globe },
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
  { value: 'auto', label: '自动获取 Token', desc: '填写登录凭据，运行前自动换取鉴权 Header。', icon: RefreshCw },
  { value: 'manual', label: '手动提供 Token/Header', desc: '直接粘贴当前 Token 或自定义 Header。', icon: KeyRound },
]

const advancedAuthInputs = new Set(['base_url', 'login_url', 'login_body', 'login_headers', 'token_path', 'method', 'content_type'])
const authInputLabels: Record<string, string> = {
  username: '用户名',
  password: '密码',
  captcha: '验证码',
  base_url: 'Base URL',
  login_url: '登录 URL',
  login_body: '登录请求体 JSON',
  login_headers: '登录请求头 JSON',
  token_path: 'Token 路径',
  method: '请求方法',
  content_type: 'Body 类型',
}

const defaultFlow = ['识别目标', '制定测试计划', '生成用例', '执行并采集证据', '输出报告']

const localInputType = computed(() => detectInputType(form.source))
const flow = computed(() => preflight.value?.expected_flow?.length ? preflight.value.expected_flow : defaultFlow)
const isApiMode = computed(() => form.test_type !== 'ui')
const manualAuthSupplied = computed(() => Boolean(form.token.trim() || form.custom_headers.trim()))
const isAutoAuthMode = computed(() => form.auth_mode === 'auto')
const isManualAuthMode = computed(() => form.auth_mode === 'manual')
const manualRefreshEnabled = computed(() => isManualAuthMode.value && form.auth_refresh_enabled && manualAuthSupplied.value)
const shouldSendAuthConfig = computed(() => isAutoAuthMode.value || manualRefreshEnabled.value)
const showLoginCredentialPanel = computed(() => isAutoAuthMode.value || (isManualAuthMode.value && form.auth_refresh_enabled))
const authMissingInputs = computed(() => preflight.value?.auth_missing_inputs || [])
const authRequiredFields = computed(() => preflight.value?.auth_required_fields || [])
const authMissingLabels = computed(() => authMissingInputs.value.map((key) => authInputLabels[key] || key))
const authNeedsAdvanced = computed(() => authMissingInputs.value.some((key) => advancedAuthInputs.has(key)))
const showAuthPrompt = computed(() => isApiMode.value && Boolean(preflight.value?.auth_error) && authMissingInputs.value.length > 0)
const readiness = computed(() => preflight.value?.readiness || (form.source.trim() ? 'needs_review' : 'blocked'))
const readinessLabel = computed(() => {
  if (readiness.value === 'ready') return 'Ready'
  if (readiness.value === 'blocked') return 'Blocked'
  return 'Needs review'
})
const hasBlockingPreflight = computed(() => preflight.value?.readiness === 'blocked')
const canRun = computed(() => Boolean(form.source.trim()) && !submitting.value && !hasBlockingPreflight.value)
const inferredTarget = computed(() => preflight.value?.target_url || form.base_url || form.source.trim() || '等待输入目标')
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
  if (manualAuthSupplied.value) return form.auth_refresh_enabled ? '手动提供 + 可刷新' : '手动提供'
  return '未提供'
})
const authProvidedTone = computed(() => {
  if (isAutoAuthMode.value) return preflight.value?.auth_resolved ? 'text-emerald-700' : 'text-amber-700'
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

function resetPreflight() {
  preflight.value = null
}

function setExample(source: string, objective: string, mode = 'auto') {
  form.source = source
  form.objective = objective
  form.test_type = mode
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
  resetPreflight()
}

function authInputNeeds(key: string) {
  return authMissingInputs.value.includes(key)
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

function buildRunPayload() {
  const headers = isManualAuthMode.value ? buildHeaders() : undefined
  const payload: any = {
    source: form.source.trim(),
    test_type: form.test_type,
  }
  if (form.objective.trim()) payload.objective = form.objective.trim()
  if (form.base_url.trim()) payload.base_url = form.base_url.trim()
  if (isApiMode.value) {
    payload.api_execution_policy = form.api_execution_policy
    if (isManualAuthMode.value && form.token.trim()) payload.token = form.token.trim()
    if (headers && Object.keys(headers).length) payload.headers = headers
    const authConfig = buildAuthConfig()
    if (authConfig) payload.auth_config = authConfig
  }
  if (form.setup_instructions.trim()) payload.setup_instructions = form.setup_instructions.trim()
  return payload
}

async function runPreflight(showToast = true) {
  if (!form.source.trim()) {
    if (showToast) toast.warning('请输入目标入口或 Swagger 文档')
    return null
  }
  preflightLoading.value = true
  try {
    const { data } = await api.post('/runs/preflight', buildRunPayload())
    const result = data as PreflightResponse
    preflight.value = result
    if (result.auth_error && (result.auth_missing_inputs || []).some((key) => advancedAuthInputs.has(key))) {
      showAdvanced.value = true
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
  if (!form.source.trim()) {
    toast.warning('请输入目标入口或 Swagger 文档')
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
</script>

<template>
  <div class="mx-auto max-w-7xl space-y-6 pb-12">
    <section class="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
      <div class="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div class="flex min-w-0 items-start gap-4">
          <div class="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-gray-900 text-white">
            <Bot :size="22" />
          </div>
          <div class="min-w-0">
            <h2 class="text-2xl font-bold tracking-tight text-gray-900">Testing Agent Workspace</h2>
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
            class="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-bold text-gray-600 transition-all hover:border-blue-200 hover:text-blue-700"
          >
            查看历史
          </button>
        </div>
      </div>
    </section>

    <div class="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
      <section class="space-y-5">
        <div class="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <div class="mb-5 flex items-center justify-between gap-3">
            <div>
              <h3 class="text-sm font-bold text-gray-900">任务委派</h3>
              <p class="mt-1 text-xs text-gray-500">告诉智能体要测试什么，以及哪些行为被允许。</p>
            </div>
            <span class="rounded bg-gray-100 px-2 py-1 text-[10px] font-bold text-gray-500">{{ localInputType }}</span>
          </div>

          <div class="space-y-5">
            <div>
              <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-gray-400">测试任务</label>
              <textarea
                v-model="form.objective"
                rows="3"
                placeholder="例如：验证登录、核心导航、搜索筛选和异常输入，不要删除真实数据。"
                class="w-full resize-none rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-sm outline-none transition-all focus:border-blue-500 focus:bg-white"
                @input="resetPreflight"
              />
            </div>

            <div>
              <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-gray-400">目标入口 / API 文档</label>
              <textarea
                v-model="form.source"
                rows="5"
                placeholder="粘贴网页 URL、Swagger/OpenAPI URL，或直接粘贴 Swagger JSON/YAML..."
                class="w-full resize-none rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 font-mono text-sm outline-none transition-all focus:border-blue-500 focus:bg-white"
                @input="resetPreflight"
              />
              <div class="mt-3 flex flex-wrap gap-2">
                <button
                  @click="setExample('https://petstore.swagger.io/v2/swagger.json', '对 Petstore API 做契约、参数边界和错误分支检查。', 'api')"
                  class="flex items-center gap-1.5 rounded-lg bg-gray-100 px-3 py-1.5 text-xs font-bold text-gray-600 transition-all hover:bg-gray-200"
                >
                  <FileJson :size="13" /> Petstore API
                </button>
                <button
                  @click="setExample('https://httpbin.org', '对公开页面做基础可达性和页面结构巡检。', 'ui')"
                  class="flex items-center gap-1.5 rounded-lg bg-gray-100 px-3 py-1.5 text-xs font-bold text-gray-600 transition-all hover:bg-gray-200"
                >
                  <Globe :size="13" /> UI 巡检示例
                </button>
              </div>
            </div>

            <div>
              <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-gray-400">测试模式</label>
              <div class="grid gap-3 md:grid-cols-3">
                <button
                  v-for="mode in modes"
                  :key="mode.value"
                  @click="form.test_type = mode.value; resetPreflight()"
                  class="min-w-0 rounded-lg border p-4 text-left transition-all"
                  :class="form.test_type === mode.value ? 'border-blue-500 bg-blue-50 text-blue-800' : 'border-gray-200 bg-white text-gray-700 hover:border-gray-300'"
                >
                  <div class="mb-1 flex items-center gap-2">
                    <component :is="mode.icon" :size="16" />
                    <span class="text-sm font-bold">{{ mode.label }}</span>
                  </div>
                  <p class="text-xs leading-5 text-gray-500">{{ mode.desc }}</p>
                </button>
              </div>
            </div>

            <div v-if="isApiMode">
              <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-gray-400">API 执行策略</label>
              <div class="grid gap-3 md:grid-cols-3">
                <button
                  v-for="policy in apiPolicies"
                  :key="policy.value"
                  @click="form.api_execution_policy = policy.value; resetPreflight()"
                  class="min-w-0 rounded-lg border p-3 text-left transition-all"
                  :class="form.api_execution_policy === policy.value ? 'border-emerald-500 bg-emerald-50 text-emerald-800' : 'border-gray-200 bg-white text-gray-700 hover:border-gray-300'"
                >
                  <div class="text-sm font-bold">{{ policy.label }}</div>
                  <p class="mt-1 text-xs leading-5 text-gray-500">{{ policy.desc }}</p>
                </button>
              </div>
            </div>
            <div v-else class="rounded-lg border border-violet-100 bg-violet-50 px-4 py-3 text-sm text-violet-800">
              UI 巡检会使用浏览器执行路径、截图证据和登录前置说明；API 写入策略不会应用到本次运行。
            </div>
          </div>
        </div>

        <div class="grid gap-5 lg:grid-cols-2">
          <div class="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <div class="mb-4 flex items-center gap-2">
              <Target :size="17" class="text-gray-500" />
              <h3 class="text-sm font-bold text-gray-900">目标上下文</h3>
            </div>
            <div class="space-y-4">
              <div>
                <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-gray-400">Base URL 覆盖</label>
                <input
                  v-model="form.base_url"
                  placeholder="例如：https://api.example.com"
                  class="w-full rounded-lg border px-4 py-2.5 font-mono text-sm outline-none transition-all focus:bg-white"
                  :class="authInputNeeds('base_url') ? 'border-amber-400 bg-amber-50 focus:border-amber-500' : 'border-gray-200 bg-gray-50 focus:border-blue-500'"
                  @input="resetPreflight"
                />
              </div>
              <div class="rounded-lg border border-gray-200 bg-gray-50 p-3">
                <div class="mb-1 flex items-center gap-2 text-xs font-bold text-gray-700">
                  <Route :size="14" /> 推断目标
                </div>
                <p class="break-words font-mono text-xs leading-5 text-gray-500">{{ inferredTarget }}</p>
              </div>
            </div>
          </div>

          <div class="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
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

        <div v-if="isApiMode" class="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <div class="mb-4 flex items-start gap-3">
            <div class="flex items-center gap-2">
              <KeyRound :size="17" class="text-gray-500" />
              <div>
                <h3 class="text-sm font-bold text-gray-900">API 凭据</h3>
                <p class="mt-1 text-xs leading-5 text-gray-500">选择自动获取 Token，或手动提供 Token/Header。</p>
              </div>
            </div>
          </div>

          <div class="grid gap-3 md:grid-cols-2">
            <button
              v-for="mode in authModes"
              :key="mode.value"
              @click="selectAuthMode(mode.value)"
              class="min-w-0 rounded-lg border p-4 text-left transition-all"
              :class="form.auth_mode === mode.value ? 'border-blue-500 bg-blue-50 text-blue-800' : 'border-gray-200 bg-white text-gray-700 hover:border-gray-300'"
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
                  placeholder="Bearer Token 或 API Key"
                  class="w-full rounded-lg border border-gray-200 bg-gray-50 px-4 py-2.5 font-mono text-sm outline-none transition-all focus:border-blue-500 focus:bg-white"
                  @input="resetPreflight"
                />
              </div>
              <div>
                <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-gray-400">自定义请求头</label>
                <textarea
                  v-model="form.custom_headers"
                  rows="3"
                  placeholder="每行一个，格式：Header-Name: value"
                  class="w-full resize-none rounded-lg border border-gray-200 bg-gray-50 px-4 py-2.5 font-mono text-sm outline-none transition-all focus:border-blue-500 focus:bg-white"
                  @input="resetPreflight"
                />
              </div>
            </div>

            <label class="flex cursor-pointer items-start gap-3 rounded-lg border border-gray-200 bg-gray-50 px-3 py-3 text-xs font-bold text-gray-700">
              <input
                v-model="form.auth_refresh_enabled"
                type="checkbox"
                class="mt-0.5 h-4 w-4 rounded border-gray-300 text-blue-600"
                @change="resetPreflight"
              />
              <span>
                <span class="block text-gray-900">过期时自动重新获取</span>
                <span class="mt-1 block font-normal leading-5 text-gray-500">
                  先使用你提供的 Token/Header 执行；如果返回 401/403，再用下面的登录凭据刷新鉴权。
                </span>
              </span>
            </label>

            <div v-if="form.auth_refresh_enabled && !manualAuthSupplied" class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-bold text-amber-800">
              手动模式需要先提供当前 Token/Header，自动刷新只负责 Token 过期后的重取。
            </div>
          </div>

          <div v-if="isAutoAuthMode" class="mt-4 rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-xs leading-5 text-blue-800">
            默认会从 OpenAPI 文档推断 login/token 接口和请求体；通常只需要先填账号、密码或验证码。
          </div>

          <div v-if="showLoginCredentialPanel" class="mt-4 space-y-4 rounded-lg border border-emerald-100 bg-emerald-50/60 p-4">
            <div class="flex items-center justify-between gap-3">
              <div>
                <div class="text-sm font-bold text-emerald-900">
                  {{ isAutoAuthMode ? '自动获取 Token' : '自动刷新凭据' }}
                </div>
                <p class="mt-1 text-xs leading-5 text-emerald-700">
                  {{ isAutoAuthMode ? '运行前先尝试登录并注入鉴权头。' : '手动 Token 失效后才会使用这些信息重新登录。' }}
                </p>
              </div>
            </div>

            <div class="grid gap-4 md:grid-cols-3">
              <div>
                <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-emerald-700">用户名</label>
                <input
                  v-model="form.auth_username"
                  placeholder="admin"
                  class="w-full rounded-lg border px-3 py-2.5 text-sm outline-none transition-all"
                  :class="authInputNeeds('username') ? 'border-amber-400 bg-amber-50 focus:border-amber-500' : 'border-emerald-200 bg-white focus:border-emerald-500'"
                  @input="resetPreflight"
                />
              </div>
              <div>
                <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-emerald-700">密码</label>
                <input
                  v-model="form.auth_password"
                  type="password"
                  placeholder="password"
                  class="w-full rounded-lg border px-3 py-2.5 text-sm outline-none transition-all"
                  :class="authInputNeeds('password') ? 'border-amber-400 bg-amber-50 focus:border-amber-500' : 'border-emerald-200 bg-white focus:border-emerald-500'"
                  @input="resetPreflight"
                />
              </div>
              <div>
                <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-emerald-700">验证码</label>
                <input
                  v-model="form.auth_captcha"
                  placeholder="可选"
                  class="w-full rounded-lg border px-3 py-2.5 text-sm outline-none transition-all"
                  :class="authInputNeeds('captcha') ? 'border-amber-400 bg-amber-50 focus:border-amber-500' : 'border-emerald-200 bg-white focus:border-emerald-500'"
                  @input="resetPreflight"
                />
              </div>
            </div>

            <button
              @click="showAdvanced = !showAdvanced"
              class="flex w-full items-center justify-between rounded-lg border border-emerald-200 bg-white px-3 py-2 text-left text-xs font-bold text-emerald-800 transition-all hover:border-emerald-300"
            >
              <span class="flex items-center gap-2"><SlidersHorizontal :size="15" /> 高级登录选项</span>
              <span class="text-emerald-600">{{ showAdvanced ? '收起' : '展开' }}</span>
            </button>

            <div v-if="showAdvanced" class="space-y-4">
              <div class="grid gap-4 lg:grid-cols-[minmax(0,1fr)_130px_130px]">
                <div>
                  <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-gray-400">登录 URL</label>
                  <input
                    v-model="form.auth_login_url"
                    placeholder="留空时从 OpenAPI login/token 接口推断"
                    class="w-full rounded-lg border px-4 py-2.5 font-mono text-sm outline-none transition-all"
                    :class="authInputNeeds('login_url') ? 'border-amber-400 bg-amber-50 focus:border-amber-500' : 'border-gray-200 bg-white focus:border-blue-500'"
                    @input="resetPreflight"
                  />
                </div>
                <div>
                  <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-gray-400">方法</label>
                  <select
                    v-model="form.auth_method"
                    class="w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm font-bold outline-none transition-all focus:border-blue-500"
                    @change="resetPreflight"
                  >
                    <option value="POST">POST</option>
                    <option value="PUT">PUT</option>
                    <option value="PATCH">PATCH</option>
                  </select>
                </div>
                <div>
                  <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-gray-400">Body 类型</label>
                  <select
                    v-model="form.auth_content_type"
                    class="w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm font-bold outline-none transition-all focus:border-blue-500"
                    @change="resetPreflight"
                  >
                    <option value="json">JSON</option>
                    <option value="form">Form</option>
                  </select>
                </div>
              </div>

              <div class="grid gap-4 lg:grid-cols-3">
                <div>
                  <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-gray-400">Token 路径</label>
                  <input
                    v-model="form.auth_token_path"
                    placeholder="留空时自动识别 access_token/data.token"
                    class="w-full rounded-lg border px-4 py-2.5 font-mono text-sm outline-none transition-all"
                    :class="authInputNeeds('token_path') ? 'border-amber-400 bg-amber-50 focus:border-amber-500' : 'border-gray-200 bg-white focus:border-blue-500'"
                    @input="resetPreflight"
                  />
                </div>
                <div>
                  <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-gray-400">注入 Header</label>
                  <input
                    v-model="form.auth_header_name"
                    placeholder="Authorization"
                    class="w-full rounded-lg border border-gray-200 bg-white px-4 py-2.5 font-mono text-sm outline-none transition-all focus:border-blue-500"
                    @input="resetPreflight"
                  />
                </div>
                <div>
                  <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-gray-400">Token 前缀</label>
                  <input
                    v-model="form.auth_token_prefix"
                    placeholder="Bearer"
                    class="w-full rounded-lg border border-gray-200 bg-white px-4 py-2.5 font-mono text-sm outline-none transition-all focus:border-blue-500"
                    @input="resetPreflight"
                  />
                </div>
              </div>

              <div class="grid gap-4 lg:grid-cols-2">
                <div>
                  <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-gray-400">登录请求体 JSON</label>
                  <textarea
                    v-model="form.auth_login_body"
                    rows="6"
                    placeholder="{ &quot;username&quot;: &quot;admin&quot;, &quot;password&quot;: &quot;123456&quot; }"
                    class="w-full resize-none rounded-lg border px-4 py-3 font-mono text-sm outline-none transition-all"
                    :class="authInputNeeds('login_body') ? 'border-amber-400 bg-amber-50 focus:border-amber-500' : 'border-gray-200 bg-white focus:border-blue-500'"
                    @input="resetPreflight"
                  />
                </div>
                <div>
                  <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-gray-400">登录请求头 JSON</label>
                  <textarea
                    v-model="form.auth_login_headers"
                    rows="6"
                    placeholder="{ &quot;X-App&quot;: &quot;test&quot; }"
                    class="w-full resize-none rounded-lg border px-4 py-3 font-mono text-sm outline-none transition-all"
                    :class="authInputNeeds('login_headers') ? 'border-amber-400 bg-amber-50 focus:border-amber-500' : 'border-gray-200 bg-white focus:border-blue-500'"
                    @input="resetPreflight"
                  />
                </div>
              </div>
            </div>
          </div>

          <div
            v-if="preflight?.auth_resolved || preflight?.auth_error"
            class="mt-4 rounded-lg border px-3 py-2 text-xs font-bold"
            :class="preflight.auth_resolved ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-red-200 bg-red-50 text-red-700'"
          >
            {{
              preflight.auth_resolved
                ? `${isAutoAuthMode ? '自动获取成功' : '自动更新可用'}：${preflight.auth_header_name || 'Authorization'}`
                : `${isAutoAuthMode ? '自动获取失败' : '自动更新失败'}：${preflight.auth_error}`
            }}
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
                打开高级登录选项
              </button>
            </div>
          </div>
        </div>

        <div class="sticky bottom-0 z-20 flex flex-col gap-3 rounded-xl border border-gray-200 bg-white/95 p-4 shadow-sm backdrop-blur sm:flex-row sm:items-center sm:justify-between lg:static lg:bg-white">
          <div class="min-w-0 text-sm text-gray-600">
            <span class="font-bold text-gray-900">启动后</span>
            智能体会进入 Agent Cockpit，持续展示计划、当前动作、日志和证据。
          </div>
          <div class="flex shrink-0 gap-2">
            <button
              @click="runPreflight()"
              :disabled="preflightLoading || !form.source.trim()"
              class="flex items-center justify-center gap-2 rounded-lg border border-gray-200 bg-white px-4 py-2.5 text-sm font-bold text-gray-700 transition-all hover:border-blue-200 hover:text-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Loader2 v-if="preflightLoading" :size="16" class="animate-spin" />
              <RefreshCw v-else :size="16" />
              运行前预检
            </button>
            <button
              @click="submit"
              :disabled="!canRun"
              class="flex items-center justify-center gap-2 rounded-lg bg-gray-900 px-5 py-2.5 text-sm font-bold text-white transition-all hover:bg-black disabled:cursor-not-allowed disabled:bg-gray-300"
            >
              <Loader2 v-if="submitting" :size="16" class="animate-spin" />
              <Play v-else :size="16" />
              {{ submitting ? '正在启动...' : '启动测试智能体' }}
            </button>
          </div>
        </div>
      </section>

      <aside class="space-y-5">
        <div class="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <div class="mb-4 flex items-center justify-between gap-3">
            <div>
              <h3 class="text-sm font-bold text-gray-900">预检状态</h3>
              <p class="mt-1 text-xs text-gray-500">运行前确认输入、模型、Worker、浏览器执行器和环境。</p>
            </div>
            <span class="rounded-lg border px-2.5 py-1 text-[10px] font-bold" :class="readinessTone(readiness)">
              {{ readinessLabel }}
            </span>
          </div>

          <div v-if="preflightLoading" class="flex items-center justify-center rounded-lg border border-dashed border-gray-200 bg-gray-50 px-4 py-10 text-sm text-gray-500">
            <Loader2 :size="18" class="mr-2 animate-spin" /> 正在检查工作区...
          </div>

          <div v-else class="space-y-3">
            <div
              v-for="check in preflight?.checks || []"
              :key="check.key"
              class="rounded-lg border px-3 py-3"
              :class="checkTone(check.status)"
            >
              <div class="flex items-start gap-2">
                <CheckCircle2 v-if="check.status === 'ready'" :size="15" class="mt-0.5 shrink-0" />
                <AlertTriangle v-else :size="15" class="mt-0.5 shrink-0" />
                <div class="min-w-0 flex-1">
                  <div class="text-xs font-bold">{{ check.label }}</div>
                  <div class="mt-0.5 text-xs leading-5 opacity-90">{{ check.detail }}</div>
                  <div v-if="check.action" class="mt-1 text-[11px] font-bold opacity-80">{{ check.action }}</div>
                </div>
              </div>
            </div>

            <div v-if="!preflight" class="rounded-lg border border-dashed border-gray-200 bg-gray-50 px-4 py-8 text-center text-sm text-gray-400">
              输入目标后先运行预检，智能体会展示计划路径和缺失配置。
            </div>
          </div>

          <div v-if="preflight?.warnings?.length" class="mt-4 space-y-2">
            <div
              v-for="warning in preflight.warnings"
              :key="warning"
              class="flex gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-800"
            >
              <AlertTriangle :size="14" class="mt-0.5 shrink-0" /> {{ warning }}
            </div>
          </div>
        </div>

        <div class="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <div class="mb-4 flex items-center justify-between gap-3">
            <div class="flex items-center gap-2">
              <Bot :size="16" class="text-gray-500" />
              <h3 class="text-sm font-bold text-gray-900">目标记忆 / Agent Memory</h3>
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

        <div class="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
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

        <div class="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <div class="mb-4 flex items-center justify-between gap-3">
            <div class="flex items-center gap-2">
              <Settings2 :size="16" class="text-gray-500" />
              <h3 class="text-sm font-bold text-gray-900">任务交接预览</h3>
            </div>
            <span
              v-if="missionPreview"
              class="rounded-lg border px-2.5 py-1 text-[10px] font-bold"
              :class="readinessTone(missionPreview.readiness)"
            >
              {{ readinessLabel }}
            </span>
          </div>

          <div v-if="missionPreview" class="space-y-4">
            <div class="rounded-lg border px-3 py-3 text-xs font-bold" :class="readinessTone(missionPreview.readiness)">
              {{ missionPreview.handoff }}
            </div>

            <div class="space-y-3 text-xs text-gray-600">
              <div class="space-y-1">
                <span class="block text-gray-400">目标</span>
                <span class="block break-words font-mono font-bold text-gray-800">{{ missionPreview.target }}</span>
              </div>
              <div class="flex items-start justify-between gap-3">
                <span class="shrink-0 text-gray-400">推断模式</span>
                <span class="min-w-0 text-right font-bold text-gray-800">{{ missionPreview.input_mode }} / {{ missionPreview.test_mode }}</span>
              </div>
              <div class="space-y-1">
                <span class="block text-gray-400">任务目标</span>
                <span class="block font-bold leading-5 text-gray-800">{{ missionPreview.objective }}</span>
              </div>
              <div class="space-y-1">
                <span class="block text-gray-400">测试范围</span>
                <span class="block leading-5 text-gray-700">{{ missionPreview.scope }}</span>
              </div>
              <div class="space-y-1">
                <span class="block text-gray-400">执行策略</span>
                <span class="block leading-5 text-gray-700">{{ missionPreview.execution_policy }}</span>
              </div>
              <div class="space-y-1">
                <span class="block text-gray-400">安全边界</span>
                <span class="block leading-5 text-gray-700">{{ missionPreview.safety_boundary }}</span>
              </div>
              <div class="space-y-1">
                <span class="block text-gray-400">鉴权准备</span>
                <span class="block leading-5" :class="missionAuthTone">{{ missionPreview.auth_readiness }}</span>
              </div>
            </div>

            <div class="grid grid-cols-2 gap-2">
              <div
                v-for="item in missionCountItems"
                :key="item.label"
                class="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2"
              >
                <div class="text-[10px] font-bold text-gray-400">{{ item.label }}</div>
                <div class="mt-0.5 text-sm font-bold text-gray-900">{{ item.value }}</div>
              </div>
            </div>

            <div v-if="missionPreview.correction_prompts.length" class="space-y-2">
              <div class="text-xs font-bold text-gray-900">启动前可修正</div>
              <div
                v-for="prompt in missionPreview.correction_prompts"
                :key="prompt.key"
                class="rounded-lg border px-3 py-2"
                :class="checkTone(prompt.status)"
              >
                <div class="flex items-start gap-2">
                  <AlertTriangle :size="14" class="mt-0.5 shrink-0" />
                  <div class="min-w-0">
                    <div class="text-xs font-bold">{{ prompt.label }}</div>
                    <div class="mt-0.5 text-xs leading-5 opacity-90">{{ prompt.detail }}</div>
                    <div v-if="prompt.action" class="mt-1 text-[11px] font-bold opacity-80">{{ prompt.action }}</div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div v-else class="space-y-3 text-xs text-gray-600">
            <div class="rounded-lg border border-dashed border-gray-200 bg-gray-50 px-4 py-6 text-center text-sm text-gray-400">
              运行预检后，这里会展示智能体准备接收的目标、范围、策略和待修正项。
            </div>
            <div class="flex items-start justify-between gap-3">
              <span class="shrink-0 text-gray-400">输入类型</span>
              <span class="min-w-0 text-right font-bold text-gray-800">{{ localInputType }}</span>
            </div>
            <div class="flex items-start justify-between gap-3">
              <span class="shrink-0 text-gray-400">测试模式</span>
              <span class="font-bold uppercase text-gray-800">{{ form.test_type }}</span>
            </div>
            <div v-if="isApiMode" class="flex items-start justify-between gap-3">
              <span class="shrink-0 text-gray-400">API 端点</span>
              <span class="font-bold text-gray-800">{{ endpointCountLabel }}</span>
            </div>
            <div v-if="isApiMode" class="flex items-start justify-between gap-3">
              <span class="shrink-0 text-gray-400">凭据</span>
              <span class="flex items-center gap-1 font-bold" :class="authProvidedTone">
                <KeyRound :size="13" /> {{ authProvidedLabel }}
              </span>
            </div>
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>
