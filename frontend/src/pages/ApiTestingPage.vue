<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import api from '../lib/api'
import { useToast } from '../composables/useToast'
import LoadingSpinner from '../components/LoadingSpinner.vue'
import { Play, CheckCircle2, XCircle } from 'lucide-vue-next'

const toast = useToast()
const loading = ref(false)
const environments = ref<any[]>([])
const documents = ref<any[]>([])
const results = ref<any[]>([])
const executing = ref(false)
const selectedEnv = ref('')
const selectedDoc = ref('')

const endpoints = ref<any[]>([])
const selectedEndpoints = ref<Set<number>>(new Set())

const manualReq = reactive({
  method: 'GET',
  url: '',
  headers: '{}',
  body: '',
  expected_status: 200,
})

async function fetchData() {
  loading.value = true
  try {
    const [envs, docs] = await Promise.all([api.get('/environments'), api.get('/documents')])
    environments.value = envs.data
    documents.value = docs.data
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '加载测试配置数据失败')
  } finally {
    loading.value = false
  }
}

function loadEndpoints() {
  const doc = documents.value.find((d: any) => d.id === selectedDoc.value)
  if (doc?.parsed_endpoints) {
    endpoints.value = doc.parsed_endpoints
    selectedEndpoints.value = new Set(endpoints.value.map((_: any, i: number) => i))
  } else {
    endpoints.value = []
  }
}

function toggleEndpoint(idx: number) {
  if (selectedEndpoints.value.has(idx)) {
    selectedEndpoints.value.delete(idx)
  } else {
    selectedEndpoints.value.add(idx)
  }
}

async function executeSelected() {
  executing.value = true
  results.value = []
  const env = environments.value.find((e: any) => e.id === selectedEnv.value)
  const baseUrl = env?.base_url || ''

  const toExecute = endpoints.value.filter((_: any, i: number) => selectedEndpoints.value.has(i))
  try {
    const { data } = await api.post('/api-tests/execute-batch', {
      environment_url: baseUrl,
      endpoints: toExecute.map((ep: any) => ({
        method: ep.method || 'GET',
        url: ep.path || ep.url || '/',
        headers: {},
        body: null,
        expected_status: 200,
      })),
    })
    results.value = data.results || []
  } catch (e: any) {
    results.value = [{ endpoint: 'Error', method: '', status_code: 0, passed: false, assertion_error: e.response?.data?.detail || e.message }]
  } finally {
    executing.value = false
  }
}

async function executeManual() {
  executing.value = true
  results.value = []
  try {
    let headers = {}
    let body = null
    try { headers = JSON.parse(manualReq.headers) } catch {}
    try { if (manualReq.body) body = JSON.parse(manualReq.body) } catch {}
    const { data } = await api.post('/api-tests/execute', {
      method: manualReq.method,
      url: manualReq.url,
      headers,
      body,
      expected_status: manualReq.expected_status,
    })
    results.value = [{ endpoint: manualReq.url, method: manualReq.method, ...data }]
  } catch (e: any) {
    results.value = [{ endpoint: manualReq.url, method: manualReq.method, status_code: 0, passed: false, assertion_error: e.response?.data?.detail || e.message }]
  } finally {
    executing.value = false
  }
}

onMounted(fetchData)
</script>

<template>
  <div class="space-y-8 pb-12">
    <div class="flex flex-col gap-1">
      <h2 class="text-2xl font-bold tracking-tight text-gray-900">接口测试</h2>
      <p class="text-gray-500 text-sm">管理 API 端点，配置环境变量，一键执行并查看断言结果。</p>
    </div>

    <LoadingSpinner v-if="loading" text="加载测试配置中..." />

    <div v-else class="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
      <!-- Left: Config -->
      <div class="lg:col-span-5 space-y-6">
        <!-- Environment & Document Selection -->
        <div class="bg-white border border-gray-200 rounded-xl shadow-sm p-6 space-y-4">
          <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest">测试配置</h3>
          <div>
            <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">测试环境</label>
            <select v-model="selectedEnv"
              class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all">
              <option value="">选择环境</option>
              <option v-for="env in environments" :key="env.id" :value="env.id">{{ env.name }} — {{ env.base_url }}</option>
            </select>
          </div>
          <div>
            <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">API 文档</label>
            <select v-model="selectedDoc" @change="loadEndpoints"
              class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all">
              <option value="">选择文档</option>
              <option v-for="doc in documents" :key="doc.id" :value="doc.id">{{ doc.name || `Document-${doc.format}` }} ({{ doc.format }})</option>
            </select>
          </div>
          <button v-if="endpoints.length" @click="executeSelected" :disabled="executing"
            class="w-full py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-bold transition-all shadow-md shadow-blue-600/10 flex items-center justify-center gap-2">
            <Play :size="16" />
            {{ executing ? '执行中...' : `执行选中端点 (${selectedEndpoints.size})` }}
          </button>
        </div>

        <!-- Endpoint List -->
        <div v-if="endpoints.length" class="bg-white border border-gray-200 rounded-xl shadow-sm p-6 space-y-3">
          <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest">端点列表</h3>
          <div v-for="(ep, idx) in endpoints" :key="idx"
            @click="toggleEndpoint(idx)"
            class="flex items-center gap-3 p-3 rounded-lg border cursor-pointer transition-all"
            :class="selectedEndpoints.has(idx) ? 'bg-blue-50 border-blue-200' : 'bg-gray-50 border-gray-200 hover:border-blue-200'">
            <input type="checkbox" :checked="selectedEndpoints.has(idx)" class="rounded border-gray-300" />
            <span class="px-2 py-0.5 rounded text-[10px] font-bold font-mono"
              :class="ep.method === 'GET' ? 'bg-emerald-50 text-emerald-700' : ep.method === 'POST' ? 'bg-blue-50 text-blue-700' : ep.method === 'PUT' ? 'bg-amber-50 text-amber-700' : 'bg-red-50 text-red-700'">
              {{ ep.method || 'GET' }}
            </span>
            <span class="text-xs font-mono text-gray-700 truncate">{{ ep.path || ep.url }}</span>
          </div>
        </div>

        <!-- Manual Request -->
        <div class="bg-white border border-gray-200 rounded-xl shadow-sm p-6 space-y-4">
          <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest">手动请求</h3>
          <div class="flex gap-2">
            <select v-model="manualReq.method" class="w-24 px-3 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500">
              <option>GET</option><option>POST</option><option>PUT</option><option>DELETE</option><option>PATCH</option>
            </select>
            <input v-model="manualReq.url" placeholder="https://api.example.com/endpoint"
              class="flex-1 px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all" />
          </div>
          <div>
            <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">Headers JSON</label>
            <textarea v-model="manualReq.headers" rows="2"
              class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm font-mono outline-none focus:border-blue-500 focus:bg-white transition-all resize-none" />
          </div>
          <div>
            <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">Body JSON</label>
            <textarea v-model="manualReq.body" rows="3" placeholder="可选"
              class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm font-mono outline-none focus:border-blue-500 focus:bg-white transition-all resize-none" />
          </div>
          <button @click="executeManual" :disabled="executing || !manualReq.url"
            class="w-full py-2.5 bg-gray-900 hover:bg-black disabled:opacity-50 text-white rounded-lg text-sm font-bold transition-all flex items-center justify-center gap-2">
            <Play :size="16" /> {{ executing ? '执行中...' : '发送请求' }}
          </button>
        </div>
      </div>

      <!-- Right: Results -->
      <div class="lg:col-span-7 space-y-4">
        <h3 class="text-sm font-bold text-gray-900">执行结果</h3>
        <div v-for="(r, idx) in results" :key="idx" class="bg-white border rounded-xl shadow-sm overflow-hidden"
          :class="r.passed ? 'border-emerald-200' : 'border-red-200'">
          <div class="px-5 py-4 border-b flex items-center justify-between"
            :class="r.passed ? 'bg-emerald-50 border-emerald-100' : 'bg-red-50 border-red-100'">
            <div class="flex items-center gap-3">
              <CheckCircle2 v-if="r.passed" :size="18" class="text-emerald-600" />
              <XCircle v-else :size="18" class="text-red-600" />
              <div>
                <div class="font-bold text-sm text-gray-900">{{ r.method }} {{ r.endpoint }}</div>
                <div class="text-xs text-gray-500">{{ r.elapsed_ms }}ms</div>
              </div>
            </div>
            <span class="px-2 py-1 rounded text-xs font-bold font-mono"
              :class="r.status_code >= 200 && r.status_code < 400 ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700'">
              {{ r.status_code }}
            </span>
          </div>
          <div class="p-5 space-y-3">
            <div v-if="r.assertion_error" class="p-3 bg-red-50 border border-red-100 rounded-lg">
              <div class="text-[10px] font-bold text-red-400 uppercase mb-1">断言错误</div>
              <div class="text-xs text-red-700">{{ r.assertion_error }}</div>
            </div>
            <div v-if="r.body">
              <div class="text-[10px] font-bold text-gray-400 uppercase mb-1">响应体</div>
              <pre class="bg-gray-50 border border-gray-100 rounded-lg p-3 text-xs font-mono text-gray-700 overflow-auto max-h-60">{{ typeof r.body === 'string' ? r.body : JSON.stringify(r.body, null, 2) }}</pre>
            </div>
          </div>
        </div>
        <p v-if="!results.length" class="text-center text-gray-400 text-sm py-12">执行测试后结果将显示在此处</p>
      </div>
    </div>
  </div>
</template>
