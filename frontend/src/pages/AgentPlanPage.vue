<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  FileJson,
  Loader2,
  MessageSquare,
  Play,
  Plus,
  Send,
  ShieldCheck,
  XCircle,
} from 'lucide-vue-next'
import api from '../lib/api'
import { useToast } from '../composables/useToast'

type PlanMessage = {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  plan?: Record<string, any> | null
  created_at?: string | null
}

type PlanningSession = {
  id: string
  title: string
  status: string
  ready_to_execute: boolean
  current_plan?: Record<string, any> | null
  current_run_payload?: Record<string, any> | null
  rejection_reason?: string | null
  executed_run_id?: string | null
  created_at?: string | null
  updated_at?: string | null
  messages?: PlanMessage[]
}

const router = useRouter()
const toast = useToast()
const sessions = ref<PlanningSession[]>([])
const activeSession = ref<PlanningSession | null>(null)
const loading = ref(false)
const sending = ref(false)
const creating = ref(false)
const rejecting = ref(false)
const executing = ref(false)
const draft = ref('')
const executeError = ref('')
const chatEnd = ref<HTMLElement | null>(null)

const messages = computed(() => activeSession.value?.messages || [])
const currentPlan = computed(() => activeSession.value?.current_plan || null)
const currentPayload = computed(() => activeSession.value?.current_run_payload || null)
const planReady = computed(() => Boolean(activeSession.value?.ready_to_execute && currentPlan.value))
const scopeItems = computed(() => toStringList(currentPlan.value?.scope))
const stepItems = computed(() => toStringList(currentPlan.value?.steps))
const safetyItems = computed(() => toStringList(currentPlan.value?.safety))

function toStringList(value: unknown) {
  return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean).slice(0, 8) : []
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

function messageClass(role: string) {
  if (role === 'user') return 'ml-auto bg-gray-950 text-white'
  if (role === 'system') return 'mx-auto bg-amber-50 text-amber-800 border border-amber-200'
  return 'mr-auto bg-white text-gray-900 border border-gray-200'
}

function errorMessage(error: any, fallback: string) {
  const detail = error.response?.data?.detail
  if (typeof detail !== 'string' || !detail.trim()) return fallback
  const normalized = detail.trim()
  const known: Record<string, string> = {
    'content is required': '请输入需求内容',
    'Planning session not found': '计划会话不存在',
    'No current plan to reject': '当前没有可拒绝的计划',
    'Executed plan cannot be rejected': '已执行的计划不能再拒绝',
    'No executable plan is ready': '当前没有可执行的计划',
    'source is required': '请补充测试目标或接口文档地址',
  }
  if (known[normalized]) return known[normalized]
  if (/New runs accept test_type values/i.test(normalized)) return '测试类型不支持，请选择 API、UI 或自动模式。'
  if (/[\u3400-\u9fff]/.test(normalized)) return normalized
  return fallback
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

async function scrollChat() {
  await nextTick()
  chatEnd.value?.scrollIntoView({ block: 'end' })
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

async function createSession() {
  creating.value = true
  executeError.value = ''
  try {
    const response = await api.post<PlanningSession>('/agent-plans', {})
    upsertSession(response.data)
    activeSession.value = response.data
    draft.value = ''
    await scrollChat()
  } finally {
    creating.value = false
  }
}

async function selectSession(id: string) {
  const response = await api.get<PlanningSession>(`/agent-plans/${id}`)
  upsertSession(response.data)
  activeSession.value = response.data
  executeError.value = ''
  await scrollChat()
}

async function sendMessage() {
  const content = draft.value.trim()
  if (!content || sending.value) return
  if (!activeSession.value) {
    await createSession()
  }
  if (!activeSession.value) return
  sending.value = true
  executeError.value = ''
  try {
    draft.value = ''
    const response = await api.post<PlanningSession>(
      `/agent-plans/${activeSession.value.id}/messages`,
      { content },
    )
    upsertSession(response.data)
    activeSession.value = response.data
    await scrollChat()
  } catch (error: any) {
    draft.value = content
    toast.error(errorMessage(error, '发送失败'))
  } finally {
    sending.value = false
  }
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
    upsertSession(response.data)
    activeSession.value = response.data
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
      upsertSession(response.data.session)
      activeSession.value = response.data.session
    }
    if (runId) {
      router.push(`/runs/${runId}`)
      return
    }
    executeError.value = '运行创建未返回任务编号。'
  } catch (error: any) {
    executeError.value = errorMessage(error, '运行预检阻止了执行，请根据提示补充信息。')
  } finally {
    executing.value = false
  }
}

onMounted(loadSessions)
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
        <button
          v-for="session in sessions"
          :key="session.id"
          type="button"
          class="w-full rounded-lg px-3 py-2 text-left transition"
          :class="activeSession?.id === session.id ? 'bg-gray-950 text-white' : 'text-gray-700 hover:bg-gray-100'"
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

      <div class="min-h-0 flex-1 overflow-y-auto bg-[#f8faf9] px-4 py-4">
        <div v-if="!messages.length" class="flex h-full items-center justify-center">
          <div class="max-w-md rounded-lg border border-dashed border-gray-300 bg-white px-4 py-4 text-center">
            <Bot :size="24" class="mx-auto mb-2 text-gray-500" />
            <div class="text-sm font-semibold text-gray-900">还没有需求</div>
            <div class="mt-1 text-xs leading-5 text-gray-500">先描述目标、范围、鉴权约束和安全边界。</div>
          </div>
        </div>

        <div v-else class="space-y-3">
          <div
            v-for="message in messages"
            :key="message.id"
            class="max-w-[86%] rounded-lg px-3 py-2 text-sm leading-6 shadow-sm"
            :class="messageClass(message.role)"
          >
            <div class="whitespace-pre-wrap break-words">{{ message.content }}</div>
          </div>
          <div ref="chatEnd" />
        </div>
      </div>

      <div class="border-t border-gray-100 bg-white p-3">
        <div v-if="activeSession?.rejection_reason && activeSession.status === 'collecting'" class="mb-2 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          <AlertTriangle :size="15" class="mt-0.5 shrink-0" />
          <span class="min-w-0 break-words">{{ activeSession.rejection_reason }}</span>
        </div>
        <div class="flex gap-2">
          <textarea
            v-model="draft"
            rows="2"
            class="min-h-[52px] flex-1 resize-none rounded-lg border border-gray-200 px-3 py-2 text-sm leading-5 outline-none transition focus:border-gray-500 focus:ring-2 focus:ring-gray-200"
            placeholder="描述测试目标、范围、凭据和约束"
            @keydown.enter.exact.prevent="sendMessage"
          />
          <button
            type="button"
            title="发送"
            aria-label="发送"
            class="flex h-[52px] w-[52px] items-center justify-center rounded-lg bg-gray-950 text-white hover:bg-gray-800 disabled:cursor-not-allowed disabled:bg-gray-300"
            :disabled="sending || !draft.trim()"
            @click="sendMessage"
          >
            <Loader2 v-if="sending" :size="18" class="animate-spin" />
            <Send v-else :size="18" />
          </button>
        </div>
      </div>
    </section>

    <aside class="tc-card flex min-h-0 flex-col overflow-hidden">
      <div class="border-b border-gray-100 px-4 py-3">
        <div class="flex items-center gap-2">
          <FileJson :size="18" class="text-gray-700" />
          <h2 class="text-base font-semibold text-gray-950">当前计划</h2>
        </div>
        <p class="mt-1 text-xs text-gray-500">{{ planReady ? '等待确认' : '暂无可执行计划' }}</p>
      </div>

      <div class="min-h-0 flex-1 overflow-y-auto p-4">
        <div v-if="!currentPlan" class="rounded-lg border border-dashed border-gray-300 bg-gray-50 p-4 text-sm text-gray-500">
          规划器会把下一份可执行任务放在这里。
        </div>

        <div v-else class="space-y-4">
          <div>
            <div class="text-sm font-semibold text-gray-950">{{ currentPlan.title || '测试智能体任务计划' }}</div>
            <div class="mt-1 text-xs leading-5 text-gray-600">{{ currentPlan.summary }}</div>
          </div>

          <div class="grid grid-cols-2 gap-2">
            <div class="rounded-lg border border-gray-200 bg-gray-50 p-2">
              <div class="text-[10px] font-bold uppercase text-gray-400">目标</div>
              <div class="mt-1 truncate text-xs font-semibold text-gray-900">{{ currentPlan.target || currentPayload?.source }}</div>
            </div>
            <div class="rounded-lg border border-gray-200 bg-gray-50 p-2">
              <div class="text-[10px] font-bold uppercase text-gray-400">模式</div>
              <div class="mt-1 text-xs font-semibold uppercase text-gray-900">{{ currentPlan.test_type || currentPayload?.test_type }}</div>
            </div>
          </div>

          <div v-if="currentPlan.objective" class="rounded-lg border border-gray-200 bg-white p-3">
            <div class="text-[10px] font-bold uppercase text-gray-400">任务目标</div>
            <div class="mt-1 text-xs leading-5 text-gray-700">{{ currentPlan.objective }}</div>
          </div>

          <div v-if="scopeItems.length" class="space-y-2">
            <div class="text-xs font-bold uppercase text-gray-400">范围</div>
            <div v-for="item in scopeItems" :key="item" class="flex gap-2 text-xs leading-5 text-gray-700">
              <CheckCircle2 :size="14" class="mt-0.5 shrink-0 text-emerald-600" />
              <span>{{ item }}</span>
            </div>
          </div>

          <div v-if="stepItems.length" class="space-y-2">
            <div class="text-xs font-bold uppercase text-gray-400">执行步骤</div>
            <div v-for="(item, index) in stepItems" :key="item" class="flex gap-2 text-xs leading-5 text-gray-700">
              <span class="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-gray-900 text-[10px] font-bold text-white">{{ index + 1 }}</span>
              <span>{{ item }}</span>
            </div>
          </div>

          <div class="rounded-lg border border-gray-200 bg-gray-50 p-3">
            <div class="mb-2 flex items-center gap-2 text-xs font-bold uppercase text-gray-400">
              <ShieldCheck :size="14" />
              安全边界
            </div>
            <div class="space-y-1.5">
              <div v-for="item in safetyItems" :key="item" class="text-xs leading-5 text-gray-700">{{ item }}</div>
              <div class="text-xs leading-5 text-gray-700">{{ currentPlan.auth }}</div>
            </div>
          </div>

          <div v-if="executeError" class="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-xs leading-5 text-red-700">
            <AlertTriangle :size="15" class="mt-0.5 shrink-0" />
            <span>{{ executeError }}</span>
          </div>
        </div>
      </div>

      <div v-if="currentPlan && activeSession?.status !== 'executed'" class="border-t border-gray-100 p-3">
        <div class="grid grid-cols-2 gap-2">
          <button
            type="button"
            class="inline-flex items-center justify-center gap-2 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-semibold text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60"
            :disabled="rejecting || executing"
            @click="rejectPlan"
          >
            <Loader2 v-if="rejecting" :size="16" class="animate-spin" />
            <XCircle v-else :size="16" />
            拒绝
          </button>
          <button
            type="button"
            class="inline-flex items-center justify-center gap-2 rounded-lg bg-gray-950 px-3 py-2 text-sm font-semibold text-white hover:bg-gray-800 disabled:cursor-not-allowed disabled:bg-gray-300"
            :disabled="!planReady || rejecting || executing"
            @click="executePlan"
          >
            <Loader2 v-if="executing" :size="16" class="animate-spin" />
            <Play v-else :size="16" />
            立即执行
          </button>
        </div>
      </div>
    </aside>
  </div>
</template>
