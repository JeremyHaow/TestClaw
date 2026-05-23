<script setup lang="ts">
import { ref, nextTick } from 'vue'
import api, { apiUrl } from '../lib/api'
import { Sparkles, Play, Terminal, Loader2, Camera, Globe, RotateCcw } from 'lucide-vue-next'

const url = ref('')
const objective = ref('测试页面核心功能')
const script = ref('')
const generating = ref(false)
const executing = ref(false)
const logLines = ref<{ command?: string; type?: string; data?: string; stdout?: string; stderr?: string; status_code?: number }[]>([])
const logContainer = ref<HTMLElement | null>(null)
const quickCommand = ref('')

// Quick command templates
const quickCommands = [
  { label: '打开页面', cmd: 'open', icon: Globe },
  { label: '截图', cmd: 'screenshot test.png', icon: Camera },
  { label: '页面快照', cmd: 'snapshot', icon: RotateCcw },
]

async function generateScript() {
  generating.value = true
  script.value = ''
  logLines.value = []
  try {
    const { data } = await api.post('/ui-tests/generate-script', {
      url: url.value,
      objective: objective.value,
    })
    script.value = data.script
  } catch (e: any) {
    script.value = '# Error: ' + (e.response?.data?.detail || e.message)
  } finally {
    generating.value = false
  }
}

async function executeScript() {
  if (!script.value.trim()) return
  executing.value = true
  logLines.value = []

  try {
    const response = await fetch(apiUrl('/ui-tests/run-cli/stream'), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${localStorage.getItem('testclaw_token')}`,
      },
      body: JSON.stringify({ script: script.value }),
    })

    if (!response.ok) {
      const err = await response.json()
      logLines.value.push({ type: 'stderr', data: err.detail || 'Request failed' })
      executing.value = false
      return
    }

    const reader = response.body?.getReader()
    const decoder = new TextDecoder()
    if (!reader) return

    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const chunk = JSON.parse(line.slice(6))
            logLines.value.push(chunk)
            await nextTick()
            if (logContainer.value) {
              logContainer.value.scrollTop = logContainer.value.scrollHeight
            }
          } catch {}
        }
      }
    }
  } catch (e: any) {
    logLines.value.push({ type: 'error', data: e.message })
  } finally {
    executing.value = false
  }
}

async function runQuickCommand(cmd: string) {
  if (cmd.startsWith('open') && !url.value) {
    logLines.value.push({ command: cmd, type: 'error', data: '请先输入目标 URL', status_code: 1 })
    return
  }
  const fullCmd = cmd.startsWith('open') && url.value ? `open ${url.value}` : cmd
  executing.value = true
  try {
    const { data } = await api.post('/ui-tests/command', { command: fullCmd })
    logLines.value.push({ command: fullCmd, ...data })
    await nextTick()
    if (logContainer.value) {
      logContainer.value.scrollTop = logContainer.value.scrollHeight
    }
  } catch (e: any) {
    logLines.value.push({ command: fullCmd, type: 'error', data: e.response?.data?.detail || e.message, status_code: 1 })
  } finally {
    executing.value = false
  }
}

async function runCustomCommand() {
  if (!quickCommand.value.trim()) return
  await runQuickCommand(quickCommand.value)
  quickCommand.value = ''
}

function getLineColor(line: any) {
  if (line.type === 'stderr' || line.type === 'error') return 'text-red-400'
  if (line.status_code === 0) return 'text-emerald-400'
  if (line.status_code && line.status_code !== 0) return 'text-red-400'
  return 'text-green-300'
}
</script>

<template>
  <div class="space-y-6 pb-12">
    <div class="flex flex-col gap-1">
      <h2 class="text-2xl font-bold tracking-tight text-gray-900">UI 测试</h2>
      <p class="text-gray-500 text-sm">使用 Playwright CLI 交互式控制浏览器，AI 生成测试步骤。</p>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
      <!-- Config + Quick Actions -->
      <div class="lg:col-span-4 space-y-4">
        <div class="bg-white border border-gray-200 rounded-xl shadow-sm p-5 space-y-4">
          <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest">测试配置</h3>
          <div>
            <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">目标 URL</label>
            <input v-model="url" placeholder="https://example.com"
              class="w-full px-3 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all" />
          </div>
          <div>
            <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">测试目标</label>
            <textarea v-model="objective" rows="3" placeholder="描述要测试的功能..."
              class="w-full px-3 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all resize-none" />
          </div>
          <button @click="generateScript" :disabled="generating || !url"
            class="w-full py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-bold transition-all shadow-md flex items-center justify-center gap-2">
            <Sparkles :size="16" />
            {{ generating ? 'AI 生成中...' : 'AI 生成测试步骤' }}
          </button>
        </div>

        <!-- Quick Commands -->
        <div class="bg-white border border-gray-200 rounded-xl shadow-sm p-5 space-y-3">
          <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest">快捷命令</h3>
          <div class="grid grid-cols-3 gap-2">
            <button v-for="qc in quickCommands" :key="qc.cmd"
              @click="runQuickCommand(qc.cmd)"
              :disabled="executing"
              class="p-2 bg-gray-50 border border-gray-200 rounded-lg text-xs font-bold text-gray-600 hover:bg-blue-50 hover:border-blue-200 hover:text-blue-700 transition-all flex flex-col items-center gap-1">
              <component :is="qc.icon" :size="16" />
              {{ qc.label }}
            </button>
          </div>
          <div class="flex gap-2">
            <input v-model="quickCommand" placeholder="输入命令..." @keydown.enter="runCustomCommand"
              class="flex-1 px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-xs font-mono outline-none focus:border-blue-500" />
            <button @click="runCustomCommand" :disabled="executing || !quickCommand"
              class="px-3 py-2 bg-gray-900 hover:bg-black disabled:opacity-50 text-white rounded-lg text-xs font-bold transition-all">
              <Play :size="12" />
            </button>
          </div>
        </div>
      </div>

      <!-- Script Editor + Logs -->
      <div class="lg:col-span-8 space-y-4">
        <!-- Script -->
        <div class="flex items-center justify-between">
          <h3 class="text-sm font-bold text-gray-900">测试脚本</h3>
          <button @click="executeScript" :disabled="executing || !script"
            class="px-4 py-1.5 rounded-lg text-xs font-bold transition-all flex items-center gap-1.5"
            :class="executing ? 'bg-red-600 hover:bg-red-700 text-white' : 'bg-emerald-600 hover:bg-emerald-700 text-white disabled:opacity-50'">
            <Loader2 v-if="executing" :size="12" class="animate-spin" />
            <Play v-else :size="12" />
            {{ executing ? '执行中...' : '执行全部' }}
          </button>
        </div>

        <div class="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">
          <div class="px-4 py-2 border-b border-gray-100 flex items-center gap-2 bg-gray-50">
            <Terminal :size="14" class="text-gray-400" />
            <span class="text-xs text-gray-500 font-mono">playwright-cli commands</span>
          </div>
          <textarea v-model="script" rows="12" placeholder="# Playwright CLI 测试命令（每行一个）&#10;# 例如：&#10;open https://example.com&#10;snapshot&#10;click button[submit]&#10;screenshot step1.png"
            class="w-full px-4 py-3 bg-gray-900 text-green-400 font-mono text-sm outline-none resize-none border-0 focus:ring-0" />
        </div>

        <!-- Execution Log -->
        <div class="bg-gray-900 border border-gray-700 rounded-xl shadow-sm overflow-hidden">
          <div class="px-4 py-2 border-b border-gray-700 flex items-center gap-2">
            <div class="flex gap-1.5">
              <div class="w-3 h-3 rounded-full bg-red-500"></div>
              <div class="w-3 h-3 rounded-full bg-yellow-500"></div>
              <div class="w-3 h-3 rounded-full bg-green-500"></div>
            </div>
            <span class="text-xs text-gray-400 font-mono ml-2">Execution Log</span>
            <span class="text-[10px] text-gray-500 ml-auto">{{ logLines.length }} entries</span>
          </div>
          <div ref="logContainer" class="p-4 max-h-96 overflow-y-auto font-mono text-xs leading-relaxed">
            <div v-if="!logLines.length" class="text-gray-600 text-center py-8">
              执行命令后日志将显示在此处
            </div>
            <div v-for="(line, i) in logLines" :key="i" class="mb-2">
              <div v-if="line.command" class="text-blue-400">
                <span class="text-gray-600 select-none mr-2">$</span>{{ line.command }}
              </div>
              <div v-if="line.stdout" class="text-green-300 ml-4 whitespace-pre-wrap">{{ line.stdout }}</div>
              <div v-if="line.stderr" class="text-red-400 ml-4 whitespace-pre-wrap">{{ line.stderr }}</div>
              <div v-if="line.data && !line.stdout" :class="getLineColor(line)" class="ml-4 whitespace-pre-wrap">{{ line.data }}</div>
              <div v-if="line.status_code !== undefined" class="ml-4 mt-0.5"
                :class="line.status_code === 0 ? 'text-emerald-500' : 'text-red-500'">
                exit: {{ line.status_code }}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
