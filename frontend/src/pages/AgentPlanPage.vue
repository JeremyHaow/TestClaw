<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  FileJson,
  Loader2,
  MessageSquare,
  Pencil,
  Play,
  Plus,
  Send,
  ShieldCheck,
  Trash2,
  X,
  XCircle,
} from 'lucide-vue-next'
import api, { apiUrl } from '../lib/api'
import { useToast } from '../composables/useToast'

type PlanMessage = {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  plan?: PlannerMessagePlan | null
  created_at?: string | null
}

type PlannerQuestionChoice = {
  label: string
  message: string
}

type PlannerQuestionOption = {
  question: string
  options: PlannerQuestionChoice[]
}

type PlannerMessagePlan = {
  status?: string
  questions?: string[]
  question_options?: PlannerQuestionOption[]
  ready_to_execute?: boolean
  plan?: Record<string, any> | null
  run_payload?: Record<string, any> | null
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
  question_options?: PlannerQuestionOption[]
}

type PlannerProcessEvent = {
  code: string
  label: string
  status?: string
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
const deletingSessionId = ref('')
const deletingMessageId = ref('')
const editingMessageId = ref<string | null>(null)
const draft = ref('')
const executeError = ref('')
const chatEnd = ref<HTMLElement | null>(null)
const processEvents = ref<PlannerProcessEvent[]>([])
let streamAbortController: AbortController | null = null

const messages = computed(() => activeSession.value?.messages || [])
const currentPlan = computed(() => activeSession.value?.current_plan || null)
const currentPayload = computed(() => activeSession.value?.current_run_payload || null)
const planReady = computed(() => Boolean(activeSession.value?.ready_to_execute && currentPlan.value))
const canModifyActiveSession = computed(() => Boolean(activeSession.value && activeSession.value.status !== 'executed'))
const latestOptionMessageId = computed(() => {
  if (activeSession.value?.status !== 'collecting') return ''
  for (const message of [...messages.value].reverse()) {
    if (message.role === 'assistant' && messageQuestionOptions(message).length) return message.id
  }
  return ''
})
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

function messageQuestionOptions(message: PlanMessage) {
  const questionOptions = message.plan?.question_options
  return Array.isArray(questionOptions)
    ? questionOptions
        .filter((group) => group?.question && Array.isArray(group.options) && group.options.length)
        .slice(0, 6)
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

function resetConversationUi() {
  processEvents.value = []
  editingMessageId.value = null
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

async function sendMessage() {
  const content = draft.value.trim()
  if (!content || sending.value) return
  if (!activeSession.value) {
    await createSession()
  }
  if (!activeSession.value) return
  if (!canModifyActiveSession.value) {
    toast.error(errorMessage(new Error('Executed plan cannot be changed'), '已执行的计划不能再修改'))
    return
  }
  if (editingMessageId.value) {
    await resendEditedMessage(content)
    return
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
  } catch (error: any) {
    draft.value = content
    await selectSession(sessionId).catch(() => undefined)
    toast.error(errorMessage(error, '发送失败'))
  } finally {
    sending.value = false
  }
}

async function sendChoice(option: PlannerQuestionChoice) {
  if (!option.message.trim() || sending.value) return
  editingMessageId.value = null
  draft.value = option.message
  await sendMessage()
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
    await selectSession(sessionId).catch(() => undefined)
    editingMessageId.value = messageId
    toast.error(errorMessage(error, '重新生成失败'))
  } finally {
    sending.value = false
  }
}

function startEditMessage(message: PlanMessage) {
  if (message.role !== 'user' || sending.value || !canModifyActiveSession.value) return
  editingMessageId.value = message.id
  draft.value = message.content
  executeError.value = ''
}

function cancelEdit() {
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
    executeError.value = errorMessage(error, '运行预检阻止了执行，请根据提示补充信息。')
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
    await scrollChat()
  } catch (error: any) {
    toast.error(errorMessage(error, '删除消息失败'))
  } finally {
    deletingMessageId.value = ''
  }
}

onMounted(loadSessions)
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
              <div
                v-if="message.id === latestOptionMessageId"
                class="mt-3 space-y-2"
              >
                <div
                  v-for="group in messageQuestionOptions(message)"
                  :key="group.question"
                  class="space-y-1.5"
                >
                  <div class="text-xs font-semibold text-gray-500">{{ group.question }}</div>
                  <div class="flex flex-wrap gap-2">
                    <button
                      v-for="option in group.options"
                      :key="`${group.question}-${option.label}-${option.message}`"
                      type="button"
                      class="rounded-full border border-gray-200 bg-gray-50 px-3 py-1.5 text-xs font-semibold text-gray-700 hover:border-gray-400 hover:bg-white disabled:cursor-not-allowed disabled:opacity-50"
                      :title="option.message"
                      :disabled="sending"
                      @click="sendChoice(option)"
                    >
                      {{ option.label }}
                    </button>
                  </div>
                </div>
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

      <div class="border-t border-gray-100 bg-white p-3">
        <div v-if="activeSession?.rejection_reason && activeSession.status === 'collecting'" class="mb-2 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          <AlertTriangle :size="15" class="mt-0.5 shrink-0" />
          <span class="min-w-0 break-words">{{ activeSession.rejection_reason }}</span>
        </div>
        <div v-if="editingMessageId" class="mb-2 flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-800">
          <Pencil :size="14" class="shrink-0" />
          <span class="min-w-0 flex-1">正在编辑上一条需求，发送后会从这里重新生成。</span>
          <button
            type="button"
            title="取消编辑"
            aria-label="取消编辑"
            class="flex h-6 w-6 shrink-0 items-center justify-center rounded-md hover:bg-blue-100"
            @click="cancelEdit"
          >
            <X :size="14" />
          </button>
        </div>
        <div class="flex gap-2">
          <textarea
            v-model="draft"
            rows="2"
            class="min-h-[52px] flex-1 resize-none rounded-lg border border-gray-200 px-3 py-2 text-sm leading-5 outline-none transition focus:border-gray-500 focus:ring-2 focus:ring-gray-200"
            placeholder="描述测试目标、范围、凭据和约束"
            :disabled="sending || !canModifyActiveSession"
            @keydown.enter.exact.prevent="sendMessage"
          />
          <button
            type="button"
            title="发送"
            aria-label="发送"
            class="flex h-[52px] w-[52px] items-center justify-center rounded-lg bg-gray-950 text-white hover:bg-gray-800 disabled:cursor-not-allowed disabled:bg-gray-300"
            :disabled="sending || !draft.trim() || !canModifyActiveSession"
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
