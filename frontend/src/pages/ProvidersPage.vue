<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import api from '../lib/api'
import { useToast } from '../composables/useToast'
import LoadingSpinner from '../components/LoadingSpinner.vue'
import { Plus, Search, Trash2, Zap } from 'lucide-vue-next'

const toast = useToast()
const loading = ref(false)
const items = ref<any[]>([])
const discoveredModels = ref<any[]>([])
const discovering = ref(false)
const discoverError = ref('')
const testingId = ref<string | null>(null)
const form = reactive({
  name: '', type: 'openai', api_key: '', model_name: '', base_url: '',
  is_default_coder: true, is_default_vision: false, is_default_planner: false,
  max_tokens: 4096, temperature: 0.2,
})

async function fetchItems() {
  loading.value = true
  try {
    const { data } = await api.get('/providers')
    items.value = data
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '加载模型列表失败')
  } finally {
    loading.value = false
  }
}

async function submit() {
  try {
    await api.post('/providers', form)
    form.name = ''; form.api_key = ''; form.model_name = ''; form.base_url = ''
    toast.success('模型配置保存成功')
    await fetchItems()
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '保存模型配置失败')
  }
}

async function setDefault(id: string, role: string) {
  try {
    await api.put(`/providers/${id}/set-default`, null, { params: { role } })
    toast.success(`已设置为默认 ${role} 模型`)
    await fetchItems()
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '设置默认模型失败')
  }
}

async function discoverModels() {
  discovering.value = true; discoverError.value = ''; discoveredModels.value = []
  try {
    const { data } = await api.post('/providers/discover-models', { type: form.type, api_key: form.api_key, base_url: form.base_url || undefined })
    discoveredModels.value = data
    if (!data.length) {
      discoverError.value = '未发现可用模型，请检查 API Key 和网络'
      toast.warning('未发现可用模型')
    } else {
      toast.success(`发现 ${data.length} 个可用模型`)
    }
  } catch {
    discoverError.value = '模型发现请求失败'
    toast.error('模型发现请求失败')
  } finally {
    discovering.value = false
  }
}

function selectModel(m: any) {
  form.model_name = m.id
  if (!form.name) form.name = m.display_name || m.id
}

async function deleteProvider(id: string, name: string) {
  if (!confirm(`确定删除模型 "${name}" 吗？`)) return
  try {
    await api.delete(`/providers/${id}`)
    toast.success(`模型「${name}」已删除`)
    await fetchItems()
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '删除模型失败')
  }
}

async function testProvider(id: string) {
  testingId.value = id
  try {
    const { data } = await api.post(`/providers/${id}/test`)
    toast.success(`连接测试成功 (延迟: ${data.latency_ms ?? data.latency ?? 'N/A'}ms, 状态: ${data.status ?? 'ok'})`)
  } catch (err: any) {
    toast.error(`连接测试失败: ${err?.response?.data?.detail || err.message}`)
  } finally {
    testingId.value = null
  }
}

onMounted(fetchItems)
</script>

<template>
  <div class="space-y-8 pb-12">
    <div class="flex flex-col gap-1">
      <h2 class="text-2xl font-bold tracking-tight text-gray-900">模型配置</h2>
      <p class="text-gray-500 text-sm">管理 AI 模型提供者，发现可用模型并配置默认角色。</p>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
      <!-- Create Form -->
      <div class="lg:col-span-5 space-y-6">
        <div class="bg-white border border-gray-200 rounded-xl shadow-sm p-6 space-y-4">
          <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest">新增模型配置</h3>
          <form class="space-y-4" @submit.prevent="submit">
            <div>
              <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">名称</label>
              <input v-model="form.name" placeholder="OpenAI 主模型"
                class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all" />
            </div>
            <div>
              <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">类型</label>
              <select v-model="form.type"
                class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all">
                <option value="openai">openai</option>
                <option value="anthropic">anthropic</option>
              </select>
            </div>
            <div>
              <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">API Key</label>
              <input v-model="form.api_key" type="password" placeholder="sk-..."
                class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all" />
            </div>
            <div>
              <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">Base URL</label>
              <input v-model="form.base_url" placeholder="可选"
                class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all" />
            </div>

            <button type="button" @click="discoverModels" :disabled="!form.api_key || discovering"
              class="w-full py-2.5 bg-gray-900 hover:bg-black disabled:opacity-50 text-white rounded-lg text-sm font-bold transition-all shadow-sm flex items-center justify-center gap-2">
              <Search :size="16" />
              {{ discovering ? '发现中...' : '发现模型' }}
            </button>

            <p v-if="discoverError" class="text-red-500 text-xs">{{ discoverError }}</p>

            <div v-if="discoveredModels.length" class="space-y-2">
              <div class="text-[10px] font-bold text-gray-400 uppercase">发现 {{ discoveredModels.length }} 个模型</div>
              <div class="flex flex-wrap gap-2 max-h-40 overflow-y-auto">
                <button v-for="m in discoveredModels" :key="m.id" type="button" @click="selectModel(m)"
                  class="px-3 py-1.5 rounded-lg text-xs font-bold border transition-all"
                  :class="form.model_name === m.id ? 'bg-blue-50 border-blue-200 text-blue-700' : 'bg-gray-50 border-gray-200 text-gray-600 hover:bg-gray-100'">
                  {{ m.display_name || m.id }}
                </button>
              </div>
            </div>

            <div>
              <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">模型名</label>
              <input v-model="form.model_name" placeholder="gpt-4o"
                class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all" />
            </div>

            <button type="submit" class="w-full py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-bold transition-all shadow-md shadow-blue-600/10">
              保存配置
            </button>
          </form>
        </div>
      </div>

      <!-- Provider List -->
      <div class="lg:col-span-7 space-y-4">
        <div class="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">
          <div class="px-6 py-4 border-b border-gray-100">
            <h3 class="font-semibold text-gray-900">模型列表</h3>
          </div>
          <LoadingSpinner v-if="loading" text="加载模型列表中..." />
          <div v-else class="divide-y divide-gray-100">
            <div v-for="item in items" :key="item.id" class="p-6 hover:bg-gray-50 transition-colors">
              <div class="flex items-start justify-between">
                <div>
                  <div class="font-bold text-gray-900">{{ item.name }}</div>
                  <div class="text-xs text-gray-500 mt-0.5">{{ item.type }} / {{ item.model_name }}</div>
                  <div v-if="item.api_key_masked" class="text-[10px] font-mono text-gray-400 mt-1">Key: {{ item.api_key_masked }}</div>
                  <div class="flex flex-wrap gap-2 mt-2">
                    <span v-if="item.is_default_coder" class="px-2 py-0.5 bg-blue-50 text-blue-600 rounded text-[10px] font-bold border border-blue-100">Coder</span>
                    <span v-if="item.is_default_vision" class="px-2 py-0.5 bg-indigo-50 text-indigo-600 rounded text-[10px] font-bold border border-indigo-100">Vision</span>
                    <span v-if="item.is_default_planner" class="px-2 py-0.5 bg-emerald-50 text-emerald-600 rounded text-[10px] font-bold border border-emerald-100">Planner</span>
                  </div>
                </div>
                <div class="flex gap-2 items-center">
                  <button @click="testProvider(item.id)" :disabled="testingId === item.id"
                    class="px-3 py-1 bg-white border border-gray-200 hover:bg-amber-50 hover:border-amber-200 rounded text-[10px] font-bold text-gray-500 hover:text-amber-600 disabled:opacity-50 transition-all flex items-center gap-1">
                    <Zap :size="10" />
                    {{ testingId === item.id ? '测试中...' : '测试' }}
                  </button>
                  <button @click="setDefault(item.id, 'coder')" class="px-3 py-1 bg-white border border-gray-200 hover:bg-blue-50 hover:border-blue-200 rounded text-[10px] font-bold text-gray-500 hover:text-blue-600 transition-all">Coder</button>
                  <button @click="setDefault(item.id, 'vision')" class="px-3 py-1 bg-white border border-gray-200 hover:bg-indigo-50 hover:border-indigo-200 rounded text-[10px] font-bold text-gray-500 hover:text-indigo-600 transition-all">Vision</button>
                  <button @click="setDefault(item.id, 'planner')" class="px-3 py-1 bg-white border border-gray-200 hover:bg-emerald-50 hover:border-emerald-200 rounded text-[10px] font-bold text-gray-500 hover:text-emerald-600 transition-all">Planner</button>
                  <button @click="deleteProvider(item.id, item.name)" class="p-1 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded transition-all" title="删除">
                    <Trash2 :size="14" />
                  </button>
                </div>
              </div>
            </div>
            <p v-if="!items.length" class="text-center text-gray-400 text-sm py-12">暂无模型配置</p>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
