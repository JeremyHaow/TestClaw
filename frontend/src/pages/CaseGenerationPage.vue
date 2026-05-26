<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import api from '../lib/api'
import { Sparkles, Plus, Trash2, Save, FileCode, Upload } from 'lucide-vue-next'
import { useToast } from '../composables/useToast'
import LoadingSpinner from '../components/LoadingSpinner.vue'
import EmptyState from '../components/EmptyState.vue'
import StyledSelect from '../components/StyledSelect.vue'

const toast = useToast()

const mode = ref<'manual' | 'ai' | 'swagger'>('ai')
const documents = ref<any[]>([])
const items = ref<any[]>([])
const loading = ref(false)
const generating = ref(false)
const importing = ref(false)
const savingAll = ref(false)
const savingIds = ref<Set<number>>(new Set())
const generatedCases = ref<any[]>([])
const rawResponse = ref('')

const aiForm = reactive({
  feature_description: '',
  api_schema: 'N/A',
  doc_id: '',
  count: 5,
})

const manualForm = reactive({
  title: '', stepsText: '["打开页面", "检查结果"]', expectedText: '["页面正常显示"]',
  priority: 'P1', category: 'FUNCTIONAL',
})

async function fetchDocuments() {
  try {
    const { data } = await api.get('/documents')
    documents.value = data
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '加载文档列表失败')
  }
}

async function fetchItems() {
  try {
    const { data } = await api.get('/test-cases')
    items.value = data
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '加载用例列表失败')
  }
}

async function importSwagger() {
  if (!aiForm.api_schema) {
    toast.warning('请输入 Swagger URL 或内容')
    return
  }
  importing.value = true
  try {
    const isUrl = aiForm.api_schema.startsWith('http')
    await api.post('/documents/import', {
      name: aiForm.feature_description || 'Imported API',
      format: 'openapi',
      url: isUrl ? aiForm.api_schema : undefined,
      raw_content: isUrl ? '' : aiForm.api_schema,
    })
    toast.success('文档导入成功')
    aiForm.feature_description = ''
    aiForm.api_schema = 'N/A'
    await fetchDocuments()
  } catch (err: any) {
    toast.error('导入失败: ' + (err?.response?.data?.detail || err.message))
  } finally {
    importing.value = false
  }
}

async function generateAI() {
  generating.value = true
  generatedCases.value = []
  rawResponse.value = ''
  try {
    let schema = aiForm.api_schema
    if (aiForm.doc_id) {
      const doc = documents.value.find((d: any) => d.id === aiForm.doc_id)
      if (doc) schema = JSON.stringify(doc.parsed_endpoints, null, 2)
    }
    const { data } = await api.post('/test-cases/generate-ai', {
      feature_description: aiForm.feature_description,
      api_schema: schema,
      count: aiForm.count,
    })
    generatedCases.value = data.cases || []
    rawResponse.value = data.raw_response || ''
  } catch (e: any) {
    rawResponse.value = 'Error: ' + (e.response?.data?.detail || e.message)
  } finally {
    generating.value = false
  }
}

async function saveCase(tc: any, idx: number) {
  savingIds.value.add(idx)
  try {
    await api.post('/test-cases/generate', {
      title: tc.title || 'AI Generated',
      steps: tc.steps || [],
      expected: tc.expected || [],
      priority: tc.priority || 'P1',
      category: tc.category || 'FUNCTIONAL',
      source: 'ai',
      test_data: tc.test_data || {},
    })
    await fetchItems()
    toast.success('保存成功')
  } catch (e: any) {
    toast.error('保存失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    savingIds.value.delete(idx)
  }
}

async function saveAllCases() {
  savingAll.value = true
  try {
    for (let i = 0; i < generatedCases.value.length; i++) {
      await saveCase(generatedCases.value[i], i)
    }
    toast.success(`已保存 ${generatedCases.value.length} 条用例`)
  } catch (e: any) {
    toast.error('批量保存失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    savingAll.value = false
  }
}

async function saveManual() {
  await api.post('/test-cases/generate', {
    title: manualForm.title,
    steps: JSON.parse(manualForm.stepsText),
    expected: JSON.parse(manualForm.expectedText),
    priority: manualForm.priority,
    category: manualForm.category,
    source: 'manual',
    test_data: {},
  })
  manualForm.title = ''
  await fetchItems()
}

async function remove(id: string) {
  await api.delete(`/test-cases/${id}`)
  await fetchItems()
}

onMounted(async () => {
  loading.value = true
  await Promise.all([fetchDocuments(), fetchItems()])
  loading.value = false
})
</script>

<template>
  <div class="space-y-8 pb-12">
    <div class="flex flex-col gap-1">
      <h2 class="text-2xl font-bold tracking-tight text-gray-900">用例生成中心</h2>
      <p class="text-gray-500 text-sm">支持手动创建、AI 智能生成和 Swagger 导入三种方式。</p>
    </div>

    <!-- Mode Tabs -->
    <div class="flex gap-2">
      <button v-for="m in [{v:'ai',l:'AI 生成'},{v:'swagger',l:'文档导入'},{v:'manual',l:'手动创建'}]" :key="m.v"
        @click="mode = m.v as any"
        class="px-4 py-2 rounded-lg text-sm font-bold transition-all"
        :class="mode === m.v ? 'bg-gray-950 text-white shadow-md' : 'bg-white border border-gray-200 text-gray-600 hover:bg-gray-50'">
        {{ m.l }}
      </button>
    </div>

    <LoadingSpinner v-if="loading" text="加载数据中..." />

    <!-- AI Generation -->
    <div v-if="mode === 'ai'" class="grid grid-cols-1 lg:grid-cols-2 gap-8 items-start">
      <div class="bg-white border border-gray-200 rounded-lg shadow-sm p-6 space-y-4">
        <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest">AI 生成用例</h3>
        <div>
          <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">功能描述</label>
          <textarea v-model="aiForm.feature_description" rows="4" placeholder="描述需要测试的功能，例如：用户登录流程，包含正确和错误密码场景..."
            class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all resize-none" />
        </div>
        <div>
          <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">关联 API 文档（可选）</label>
          <StyledSelect v-model="aiForm.doc_id">
            <option value="">不关联</option>
            <option v-for="doc in documents" :key="doc.id" :value="doc.id">{{ doc.name || `Document-${doc.format}` }} ({{ doc.format }})</option>
          </StyledSelect>
        </div>
        <div>
          <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">API Schema（可选）</label>
          <textarea v-model="aiForm.api_schema" rows="3" placeholder="粘贴 OpenAPI schema 或留空"
            class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm font-mono outline-none focus:border-blue-500 focus:bg-white transition-all resize-none" />
        </div>
        <div>
          <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">生成数量</label>
          <input v-model.number="aiForm.count" type="number" min="1" max="10"
            class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all" />
        </div>
        <button @click="generateAI" :disabled="generating || !aiForm.feature_description"
          class="w-full py-2.5 bg-gray-950 hover:bg-gray-800 disabled:opacity-50 text-white rounded-lg text-sm font-bold transition-all shadow-md shadow-blue-600/10 flex items-center justify-center gap-2">
          <Sparkles :size="16" />
          {{ generating ? 'AI 生成中...' : 'AI 生成用例' }}
        </button>
      </div>

      <div class="space-y-4">
        <div class="flex items-center justify-between">
          <h3 class="text-sm font-bold text-gray-900">生成结果</h3>
          <button v-if="generatedCases.length" @click="saveAllCases" :disabled="savingAll"
            class="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white rounded-lg text-xs font-bold transition-all flex items-center gap-1.5">
            <Save :size="12" /> {{ savingAll ? '保存中...' : `全部保存 (${generatedCases.length})` }}
          </button>
        </div>
        <LoadingSpinner v-if="generating" text="AI 正在生成用例..." />
        <EmptyState v-else-if="!generatedCases.length"
          title="暂无生成结果"
          description="输入功能描述后点击 AI 生成按钮" />
        <template v-else>
          <div v-for="(tc, idx) in generatedCases" :key="idx" class="bg-white border border-gray-200 rounded-lg p-5 shadow-sm">
            <div class="flex items-start justify-between mb-3">
              <div>
                <div class="font-bold text-gray-900 text-sm">{{ tc.title }}</div>
                <div class="text-[10px] font-mono text-gray-400 mt-0.5">{{ tc.category || 'FUNCTIONAL' }} / {{ tc.priority || 'P1' }}</div>
              </div>
              <button @click="saveCase(tc, idx)" :disabled="savingIds.has(idx)"
                class="px-3 py-1 bg-blue-50 text-blue-600 rounded text-xs font-bold hover:bg-blue-100 disabled:opacity-50 transition-all flex items-center gap-1">
                <Save :size="12" /> {{ savingIds.has(idx) ? '保存中...' : '保存' }}
              </button>
            </div>
            <div v-if="tc.steps?.length" class="space-y-1 mb-2">
              <div class="text-[9px] font-bold uppercase tracking-widest text-gray-400">步骤</div>
              <ul class="space-y-1">
                <li v-for="(s, i) in tc.steps" :key="i" class="text-xs flex gap-2">
                  <span class="text-blue-500 font-mono font-bold text-[9px] mt-0.5">{{ Number(i) + 1 }}</span>
                  <span class="text-gray-600">{{ s }}</span>
                </li>
              </ul>
            </div>
            <div v-if="tc.expected?.length">
              <div class="text-[9px] font-bold uppercase tracking-widest text-gray-400 mb-1">预期</div>
              <ul class="space-y-1">
                <li v-for="(e, i) in tc.expected" :key="i" class="text-xs text-gray-600">{{ e }}</li>
              </ul>
            </div>
          </div>
        </template>
        <div v-if="rawResponse && !generatedCases.length" class="bg-gray-50 border border-gray-200 rounded-lg p-4">
          <div class="text-[10px] font-bold text-gray-400 uppercase mb-2">AI 原始响应</div>
          <pre class="text-xs font-mono text-gray-600 whitespace-pre-wrap">{{ rawResponse }}</pre>
        </div>
      </div>
    </div>

    <!-- Swagger Import -->
    <div v-if="mode === 'swagger'" class="grid grid-cols-1 lg:grid-cols-2 gap-8 items-start">
      <div class="bg-white border border-gray-200 rounded-lg shadow-sm p-6 space-y-4">
        <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest">导入 Swagger/OpenAPI</h3>
        <div>
          <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">文档名称</label>
          <input v-model="aiForm.feature_description" placeholder="我的 API 文档"
            class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all" />
        </div>
        <div>
          <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">Swagger URL 或内容</label>
          <textarea v-model="aiForm.api_schema" rows="8" placeholder="https://petstore.swagger.io/v2/swagger.json 或粘贴 JSON 内容"
            class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm font-mono outline-none focus:border-blue-500 focus:bg-white transition-all resize-none" />
        </div>
        <button @click="importSwagger" :disabled="importing"
          class="w-full py-2.5 bg-gray-950 hover:bg-gray-800 disabled:opacity-50 text-white rounded-lg text-sm font-bold transition-all shadow-md shadow-blue-600/10 flex items-center justify-center gap-2">
          {{ importing ? '导入中...' : '导入文档' }}
        </button>
      </div>

      <div class="space-y-4">
        <h3 class="text-sm font-bold text-gray-900">已导入文档</h3>
        <div v-for="doc in documents" :key="doc.id" class="bg-white border border-gray-200 rounded-lg p-5 shadow-sm">
          <div class="font-bold text-gray-900 text-sm">{{ doc.name || `Document-${doc.format}` }}</div>
          <div class="text-[10px] font-mono text-gray-400 uppercase">{{ doc.format }}</div>
          <pre class="bg-gray-50 border border-gray-100 rounded-lg p-3 mt-2 text-[10px] font-mono text-gray-600 overflow-auto max-h-40">{{ JSON.stringify(doc.parsed_endpoints, null, 2) }}</pre>
        </div>
        <p v-if="!documents.length" class="text-center text-gray-400 text-sm py-12">暂无文档</p>
      </div>
    </div>

    <!-- Manual Create -->
    <div v-if="mode === 'manual'" class="grid grid-cols-1 lg:grid-cols-2 gap-8 items-start">
      <div class="bg-white border border-gray-200 rounded-lg shadow-sm p-6 space-y-4">
        <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest">手动创建用例</h3>
        <form class="space-y-4" @submit.prevent="saveManual">
          <div>
            <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">标题</label>
            <input v-model="manualForm.title" placeholder="校验登录成功"
              class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all" />
          </div>
          <div>
            <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">步骤 JSON</label>
            <textarea v-model="manualForm.stepsText" rows="4"
              class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm font-mono outline-none focus:border-blue-500 focus:bg-white transition-all resize-none" />
          </div>
          <div>
            <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">预期 JSON</label>
            <textarea v-model="manualForm.expectedText" rows="3"
              class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm font-mono outline-none focus:border-blue-500 focus:bg-white transition-all resize-none" />
          </div>
          <button type="submit" class="w-full py-2.5 bg-gray-950 hover:bg-gray-800 text-white rounded-lg text-sm font-bold transition-all shadow-md shadow-blue-600/10 flex items-center justify-center gap-2">
            <Plus :size="16" /> 保存用例
          </button>
        </form>
      </div>

      <div class="space-y-4">
        <div class="flex items-center justify-between">
          <h3 class="text-sm font-bold text-gray-900">用例列表</h3>
          <span class="text-xs text-gray-400 font-mono">{{ items.length }} 条</span>
        </div>
        <div class="space-y-3">
          <div v-for="item in items" :key="item.id" class="bg-white border border-gray-200 rounded-lg p-5 shadow-sm hover:border-blue-200 transition-all group">
            <div class="flex items-start justify-between mb-3">
              <div>
                <div class="font-bold text-gray-900 text-sm">{{ item.title }}</div>
                <div class="text-[10px] font-mono text-gray-400 mt-0.5">{{ item.category }} / {{ item.priority }}</div>
              </div>
              <button @click="remove(item.id)" class="text-gray-400 hover:text-red-500 transition-colors opacity-0 group-hover:opacity-100">
                <Trash2 :size="14" />
              </button>
            </div>
            <div class="space-y-2">
              <div class="text-[9px] font-bold uppercase tracking-widest text-gray-400">步骤</div>
              <ul class="space-y-1">
                <li v-for="(s, i) in item.steps" :key="i" class="text-xs flex gap-2">
                  <span class="text-blue-500 font-mono font-bold text-[9px] mt-0.5">{{ Number(i) + 1 }}</span>
                  <span class="text-gray-600">{{ typeof s === 'string' ? s : JSON.stringify(s) }}</span>
                </li>
              </ul>
            </div>
          </div>
          <p v-if="!items.length" class="text-center text-gray-400 text-sm py-12">暂无用例</p>
        </div>
      </div>
    </div>
  </div>
</template>
