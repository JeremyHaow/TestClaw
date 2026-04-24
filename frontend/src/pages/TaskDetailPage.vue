<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../lib/api'
import { useTaskStore } from '../stores/tasks'
import StatusBadge from '../components/StatusBadge.vue'
import LoadingSpinner from '../components/LoadingSpinner.vue'
import { useToast } from '../composables/useToast'
import { ArrowLeft, RotateCcw, XCircle } from 'lucide-vue-next'

const route = useRoute()
const router = useRouter()
const taskStore = useTaskStore()
const toast = useToast()
const loading = ref(false)
const bugReport = ref<any>(null)
let eventSource: EventSource | null = null

function connectSSE(taskId: string) {
  disconnectSSE()
  const token = localStorage.getItem('testclaw_token')
  const url = `/api/v1/tasks/${taskId}/stream`
  eventSource = new EventSource(token ? `${url}?token=${token}` : url)
  eventSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      if (data.status && taskStore.current) {
        taskStore.current.status = data.status
      }
      if (data.execution_result && taskStore.current) {
        taskStore.current.execution_result = data.execution_result
      }
      if (data.workflow_steps && taskStore.current) {
        taskStore.current.workflow_steps = data.workflow_steps
      }
      if (data.test_cases && taskStore.current) {
        taskStore.current.test_cases = data.test_cases
      }
      if (data.test_plan && taskStore.current) {
        taskStore.current.test_plan = data.test_plan
      }
      if (data.generated_code && taskStore.current) {
        taskStore.current.generated_code = data.generated_code
      }
      if (['succeeded', 'failed', 'bug_found', 'cancelled'].includes(data.status)) {
        disconnectSSE()
        loadBugReport(taskId, data.status)
      }
    } catch {}
  }
  eventSource.onerror = () => {
    disconnectSSE()
  }
}

function disconnectSSE() {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
}

async function rerunTask() {
  try {
    const { data } = await api.post(`/tasks/${route.params.id}/rerun`)
    router.push(`/tasks/${data.id}`)
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '重跑任务失败')
  }
}

async function cancelTask() {
  try {
    await api.post(`/tasks/${route.params.id}/cancel`)
    toast.success('任务已取消')
    await loadTask(String(route.params.id))
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '取消任务失败')
  }
}

async function loadBugReport(taskId: string, status: string) {
  if (!['bug_found', 'failed', 'succeeded'].includes(status)) return
  if (bugReport.value) return
  try {
    const { data } = await api.get(`/tasks/${taskId}/bug-report`)
    bugReport.value = data
  } catch { bugReport.value = null }
}

async function loadTask(taskId: string) {
  loading.value = true
  try {
    await taskStore.fetchTask(taskId)
    bugReport.value = taskStore.current?.bug_report || null
    const status = taskStore.current?.status
    await loadBugReport(taskId, status)
    if (['queued', 'running'].includes(status)) {
      connectSSE(taskId)
    }
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '加载任务详情失败')
  } finally {
    loading.value = false
  }
}

onMounted(() => loadTask(String(route.params.id)))
onUnmounted(() => disconnectSSE())
watch(() => route.params.id, (id) => { if (id) loadTask(String(id)) })
</script>

<template>
  <LoadingSpinner v-if="loading" text="加载任务详情中..." />

  <div class="space-y-8 pb-12" v-else-if="taskStore.current">
    <div class="flex items-center gap-4">
      <button @click="router.push('/tasks')" class="p-2 hover:bg-gray-100 rounded-lg text-gray-400 hover:text-gray-600 transition-all">
        <ArrowLeft :size="20" />
      </button>
      <div class="flex-1">
        <h2 class="text-2xl font-bold tracking-tight text-gray-900">{{ taskStore.current.objective }}</h2>
        <p class="text-gray-500 text-sm font-mono">{{ taskStore.current.target_url }}</p>
      </div>
      <div class="flex items-center gap-2">
        <button
          v-if="['queued','running'].includes(taskStore.current.status)"
          @click="cancelTask"
          class="px-3 py-1.5 bg-red-50 text-red-600 border border-red-200 hover:bg-red-100 rounded-lg text-xs font-bold transition-all flex items-center gap-1.5"
        >
          <XCircle :size="14" /> 取消
        </button>
        <button
          v-if="['succeeded','failed','bug_found'].includes(taskStore.current.status)"
          @click="rerunTask"
          class="px-3 py-1.5 bg-blue-50 text-blue-600 border border-blue-200 hover:bg-blue-100 rounded-lg text-xs font-bold transition-all flex items-center gap-1.5"
        >
          <RotateCcw :size="14" /> 重跑
        </button>
        <StatusBadge :status="taskStore.current.status" />
      </div>
    </div>

    <!-- Meta -->
    <div class="flex flex-wrap gap-3">
      <span class="px-3 py-1 bg-gray-100 text-gray-600 rounded-lg text-xs font-bold border border-gray-200">类型: {{ taskStore.current.test_type }}</span>
      <span v-if="taskStore.current.api_doc_id" class="px-3 py-1 bg-blue-50 text-blue-600 rounded-lg text-xs font-bold border border-blue-100">文档: {{ taskStore.current.api_doc_id }}</span>
      <span v-if="taskStore.current.environment_id" class="px-3 py-1 bg-indigo-50 text-indigo-600 rounded-lg text-xs font-bold border border-indigo-100">环境: {{ taskStore.current.environment_id }}</span>
    </div>

    <!-- Workflow Timeline -->
    <div v-if="taskStore.current.workflow_steps?.length" class="bg-white border border-gray-200 rounded-xl shadow-sm p-6">
      <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4">AI 工作流</h3>
      <div class="space-y-3">
        <div
          v-for="(step, idx) in taskStore.current.workflow_steps"
          :key="idx"
          class="flex items-center gap-4 p-4 rounded-lg border transition-all"
          :class="step.status === 'done' ? 'bg-emerald-50 border-emerald-200' : step.status === 'failed' ? 'bg-red-50 border-red-200' : 'bg-gray-50 border-gray-200'"
        >
          <div class="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold shrink-0"
            :class="step.status === 'done' ? 'bg-emerald-600 text-white' : step.status === 'failed' ? 'bg-red-600 text-white' : 'bg-gray-200 text-gray-600'">
            {{ idx + 1 }}
          </div>
          <div class="flex-1">
            <div class="text-sm font-bold text-gray-900">{{ step.node }}</div>
            <div class="text-xs text-gray-500 mt-0.5">{{ step.detail }}</div>
          </div>
          <StatusBadge :status="step.status" />
        </div>
      </div>
    </div>

    <!-- Test Plan -->
    <div v-if="taskStore.current.test_plan?.length" class="bg-white border border-gray-200 rounded-xl shadow-sm p-6">
      <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4">测试计划</h3>
      <div class="space-y-4">
        <div v-for="(plan, idx) in taskStore.current.test_plan" :key="idx" class="p-4 bg-gray-50 rounded-lg border border-gray-100">
          <div class="font-bold text-gray-900">{{ plan.title }}</div>
          <div class="text-xs text-gray-500 mt-1">类型: {{ plan.test_type }} | 阶段: {{ plan.phase }}</div>
          <ul v-if="plan.steps?.length" class="mt-3 space-y-2">
            <li v-for="(s, i) in plan.steps" :key="i" class="text-xs flex gap-2">
              <span class="text-blue-500 font-mono font-bold text-[9px] mt-0.5">{{ i + 1 }}</span>
              <span class="text-gray-600">{{ s }}</span>
            </li>
          </ul>
        </div>
      </div>
    </div>

    <!-- Test Cases -->
    <div v-if="taskStore.current.test_cases?.length" class="bg-white border border-gray-200 rounded-xl shadow-sm p-6">
      <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4">生成用例</h3>
      <div class="space-y-3">
        <div v-for="(tc, idx) in taskStore.current.test_cases" :key="idx" class="p-4 bg-gray-50 rounded-lg border border-gray-100">
          <div class="flex items-center justify-between">
            <span class="font-bold text-gray-900 text-sm">{{ tc.title }}</span>
            <span class="text-[10px] font-mono text-gray-400">{{ tc.category }} / {{ tc.priority }}</span>
          </div>
          <ul class="mt-2 space-y-1">
            <li v-for="(s, i) in tc.steps" :key="i" class="text-xs text-gray-500">{{ i + 1 }}. {{ s }}</li>
          </ul>
        </div>
      </div>
    </div>

    <!-- Execution Result -->
    <div v-if="taskStore.current.execution_result" class="bg-white border border-gray-200 rounded-xl shadow-sm p-6">
      <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4">执行结果</h3>
      <div class="grid grid-cols-2 gap-4 mb-4">
        <div class="p-3 bg-gray-50 rounded-lg border border-gray-100">
          <div class="text-[10px] font-bold text-gray-400 uppercase">退出码</div>
          <div class="text-lg font-bold mt-1" :class="taskStore.current.execution_result.status_code === 0 ? 'text-emerald-600' : 'text-red-600'">
            {{ taskStore.current.execution_result.status_code }}
          </div>
        </div>
        <div class="p-3 bg-gray-50 rounded-lg border border-gray-100">
          <div class="text-[10px] font-bold text-gray-400 uppercase">追踪路径</div>
          <div class="text-xs font-mono text-gray-600 mt-1 truncate">{{ taskStore.current.execution_result.trace_path || '无' }}</div>
        </div>
      </div>
      <div v-if="taskStore.current.execution_result.stdout" class="mb-3">
        <div class="text-[10px] font-bold text-gray-400 uppercase mb-1">标准输出</div>
        <pre class="bg-gray-50 border border-gray-100 rounded-lg p-4 text-xs font-mono text-gray-700 overflow-auto max-h-60">{{ taskStore.current.execution_result.stdout }}</pre>
      </div>
      <div v-if="taskStore.current.execution_result.stderr">
        <div class="text-[10px] font-bold text-red-400 uppercase mb-1">错误输出</div>
        <pre class="bg-red-50 border border-red-100 rounded-lg p-4 text-xs font-mono text-red-700 overflow-auto max-h-60">{{ taskStore.current.execution_result.stderr }}</pre>
      </div>
    </div>

    <!-- Generated Code -->
    <div v-if="taskStore.current.generated_code" class="bg-white border border-gray-200 rounded-xl shadow-sm p-6">
      <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4">生成代码</h3>
      <pre class="bg-gray-50 border border-gray-100 rounded-lg p-4 text-xs font-mono text-gray-700 overflow-auto max-h-80">{{ taskStore.current.generated_code }}</pre>
    </div>

    <!-- Bug Report -->
    <div v-if="bugReport" class="bg-white border border-red-200 rounded-xl shadow-sm p-6">
      <h3 class="text-xs font-bold text-red-400 uppercase tracking-widest mb-4">缺陷分析</h3>
      <div class="grid grid-cols-2 gap-4">
        <div><div class="text-[10px] font-bold text-gray-400 uppercase">标题</div><p class="text-sm text-gray-700 mt-1">{{ bugReport.title }}</p></div>
        <div><div class="text-[10px] font-bold text-gray-400 uppercase">根因</div><p class="text-sm text-gray-700 mt-1">{{ bugReport.root_cause }}</p></div>
        <div><div class="text-[10px] font-bold text-gray-400 uppercase">复现步骤</div><p class="text-sm text-gray-700 mt-1">{{ bugReport.reproduce_steps }}</p></div>
        <div><div class="text-[10px] font-bold text-gray-400 uppercase">修复建议</div><p class="text-sm text-gray-700 mt-1">{{ bugReport.fix_suggestion }}</p></div>
      </div>
    </div>
  </div>

  <div v-else-if="!loading" class="flex flex-col items-center justify-center py-24 text-center">
    <div class="text-gray-400 text-sm">任务不存在或加载失败</div>
    <button @click="router.push('/tasks')" class="mt-4 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-bold hover:bg-blue-700 transition-all">
      返回任务列表
    </button>
  </div>
</template>
