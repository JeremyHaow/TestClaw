<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../lib/api'
import StatusBadge from '../components/StatusBadge.vue'
import LoadingSpinner from '../components/LoadingSpinner.vue'
import { useToast } from '../composables/useToast'
import { ArrowLeft, RotateCcw, XCircle, CheckCircle2, XCircleIcon, Camera, FileText, Zap, Monitor, Terminal } from 'lucide-vue-next'

const route = useRoute()
const router = useRouter()
const toast = useToast()
const loading = ref(false)
const run = ref<any>(null)
let eventSource: EventSource | null = null

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
      if (data.type === 'workflow' && run.value) {
        run.value.workflow_steps = data.steps
      }
      if (data.type === 'done') {
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
    run.value = data
    if (['queued', 'running'].includes(data.status)) {
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
        <div class="p-3 bg-gray-50 rounded-lg border border-gray-100">
          <div class="text-[10px] font-bold text-gray-400 uppercase">API 测试</div>
          <div class="text-sm font-bold text-gray-900 mt-1">{{ run.final_report.api_test_summary?.pass_rate || 'N/A' }}</div>
          <div class="text-[10px] text-gray-500">{{ run.final_report.api_test_summary?.passed || 0 }}/{{ run.final_report.api_test_summary?.total || 0 }} 通过</div>
        </div>
        <div class="p-3 bg-gray-50 rounded-lg border border-gray-100">
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
      <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4">AI 工作流</h3>
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
    <div class="flex gap-1 bg-gray-100 rounded-lg p-1">
      <button
        v-for="tab in [
          { key: 'report', label: '报告', icon: FileText },
          { key: 'api', label: 'API 测试', icon: Zap },
          { key: 'ui', label: 'UI 测试', icon: Monitor },
          { key: 'cases', label: '测试用例', icon: FileText },
          { key: 'logs', label: '日志', icon: Terminal },
        ]"
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
        <div class="grid grid-cols-3 gap-4 mb-4">
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
        </div>

        <div class="overflow-x-auto">
          <table class="w-full text-left text-xs">
            <thead>
              <tr class="bg-gray-50 text-gray-500 border-b border-gray-100">
                <th class="px-4 py-2 font-semibold uppercase tracking-wider text-[10px]">状态</th>
                <th class="px-4 py-2 font-semibold uppercase tracking-wider text-[10px]">分类</th>
                <th class="px-4 py-2 font-semibold uppercase tracking-wider text-[10px]">方法</th>
                <th class="px-4 py-2 font-semibold uppercase tracking-wider text-[10px]">URL</th>
                <th class="px-4 py-2 font-semibold uppercase tracking-wider text-[10px]">状态码</th>
                <th class="px-4 py-2 font-semibold uppercase tracking-wider text-[10px]">耗时</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-100">
              <tr v-for="(r, i) in run.api_execution_result.results" :key="i" class="hover:bg-gray-50">
                <td class="px-4 py-2">
                  <CheckCircle2 v-if="r.passed" :size="14" class="text-emerald-500" />
                  <XCircleIcon v-else :size="14" class="text-red-500" />
                </td>
                <td class="px-4 py-2 text-gray-500">{{ r.category }}</td>
                <td class="px-4 py-2 font-mono font-bold text-gray-700">{{ r.method }}</td>
                <td class="px-4 py-2 font-mono text-gray-500 truncate max-w-xs">{{ r.url }}</td>
                <td class="px-4 py-2 font-mono" :class="r.status_code >= 200 && r.status_code < 400 ? 'text-emerald-600' : 'text-red-600'">{{ r.status_code }}</td>
                <td class="px-4 py-2 font-mono text-gray-500">{{ r.elapsed_ms }}ms</td>
              </tr>
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
            <div class="text-[10px] font-bold text-gray-400 uppercase">命令数</div>
            <div class="text-lg font-bold text-gray-900 mt-1">{{ run.ui_execution_result.total }}</div>
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
            <span class="font-mono font-bold text-gray-700 shrink-0">{{ cmd.command }}</span>
            <span v-if="cmd.stderr" class="text-red-500 truncate">{{ cmd.stderr }}</span>
          </div>
        </div>

        <!-- Screenshots -->
        <div v-if="run.artifacts?.ui_screenshots?.length" class="mt-6">
          <h4 class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-3 flex items-center gap-2">
            <Camera :size="14" /> 截图
          </h4>
          <div class="grid grid-cols-2 lg:grid-cols-3 gap-3">
            <div v-for="(shot, i) in run.artifacts.ui_screenshots" :key="i"
              class="bg-gray-100 rounded-lg border border-gray-200 overflow-hidden"
            >
              <div class="p-2 text-[10px] font-mono text-gray-500 truncate">{{ shot }}</div>
            </div>
          </div>
        </div>
      </div>
      <div v-else class="text-center py-12 text-gray-400 text-sm">未执行 UI 测试</div>
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
            <ul v-if="tc.steps?.length" class="mt-2 space-y-1">
              <li v-for="(s, j) in tc.steps" :key="j" class="text-xs text-gray-500">{{ j + 1 }}. {{ s }}</li>
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
            <ul v-if="tc.steps?.length" class="mt-2 space-y-1">
              <li v-for="(s, j) in tc.steps" :key="j" class="text-xs text-gray-500">{{ j + 1 }}. {{ s }}</li>
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
</template>
