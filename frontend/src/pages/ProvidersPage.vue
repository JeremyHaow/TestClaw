<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../lib/api'
import { useToast } from '../composables/useToast'
import LoadingSpinner from '../components/LoadingSpinner.vue'
import { Bot, Edit3, Save, Search, Trash2, X, Zap } from 'lucide-vue-next'

const toast = useToast()
const router = useRouter()
const loading = ref(false)
const items = ref<any[]>([])
const discoveredModels = ref<any[]>([])
const discovering = ref(false)
const discoverError = ref('')
const testingId = ref<string | null>(null)
const editing = ref(false)
const form = reactive({
  name: '', type: 'openai', api_key: '', model_name: '', base_url: '',
  is_default_coder: true, is_default_vision: false, is_default_planner: false,
  max_tokens: 4096, temperature: 0.2,
  system_prompt: '', agent_type: '',
})
const editForm = reactive({
  id: '',
  name: '',
  type: 'openai',
  api_key: '',
  model_name: '',
  base_url: '',
  is_default_coder: false,
  is_default_vision: false,
  is_default_planner: false,
  max_tokens: 4096,
  temperature: 0.2,
  system_prompt: '',
  agent_type: '',
})
const activeProviderCount = computed(() => items.value.length)
const defaultRoleCount = computed(() => {
  const roles = new Set<string>()
  for (const item of items.value) {
    if (item.is_default_planner) roles.add('Planner')
    if (item.is_default_coder) roles.add('Coder')
    if (item.is_default_vision) roles.add('Vision')
  }
  return roles.size
})
const roleCards = computed(() => [
  { label: 'Planner', ready: items.value.some((item) => item.is_default_planner), detail: '生成 LangGraph 测试计划' },
  { label: 'Coder', ready: items.value.some((item) => item.is_default_coder), detail: '生成脚本与用例内容' },
  { label: 'Vision', ready: items.value.some((item) => item.is_default_vision), detail: '保留给视觉/截图理解' },
])

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
    await api.post('/providers', {
      ...form,
      base_url: form.base_url || undefined,
      system_prompt: form.system_prompt || undefined,
      agent_type: form.agent_type || undefined,
    })
    form.name = ''; form.api_key = ''; form.model_name = ''; form.base_url = ''; form.system_prompt = ''; form.agent_type = ''
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

function startEdit(item: any) {
  Object.assign(editForm, {
    id: item.id,
    name: item.name || '',
    type: item.type || 'openai',
    api_key: '',
    model_name: item.model_name || '',
    base_url: item.base_url || '',
    is_default_coder: Boolean(item.is_default_coder),
    is_default_vision: Boolean(item.is_default_vision),
    is_default_planner: Boolean(item.is_default_planner),
    max_tokens: item.max_tokens || 4096,
    temperature: item.temperature ?? 0.2,
    system_prompt: item.system_prompt || '',
    agent_type: item.agent_type || '',
  })
  editing.value = true
}

function cancelEdit() {
  editing.value = false
  editForm.api_key = ''
}

async function saveEdit() {
  try {
    const payload: any = {
      name: editForm.name,
      type: editForm.type,
      model_name: editForm.model_name,
      base_url: editForm.base_url || null,
      is_default_coder: editForm.is_default_coder,
      is_default_vision: editForm.is_default_vision,
      is_default_planner: editForm.is_default_planner,
      max_tokens: Number(editForm.max_tokens) || 4096,
      temperature: Number(editForm.temperature),
      system_prompt: editForm.system_prompt || null,
      agent_type: editForm.agent_type || null,
    }
    if (editForm.api_key.trim()) payload.api_key = editForm.api_key.trim()
    await api.put(`/providers/${editForm.id}`, payload)
    toast.success('模型配置已更新')
    cancelEdit()
    await fetchItems()
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '更新模型配置失败')
  }
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
      <h2 class="text-2xl font-bold tracking-tight text-gray-900">模型与 Agent</h2>
      <p class="text-gray-500 text-sm">配置 LangChain 模型提供者和 Planner/Coder/Vision 角色，运行预检会读取这些默认角色。</p>
    </div>

    <div class="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
      <div class="grid gap-3 md:grid-cols-4">
        <div class="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
          <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">Provider</div>
          <div class="mt-2 text-2xl font-semibold text-gray-900">{{ activeProviderCount }}</div>
          <div class="mt-1 text-xs text-gray-500">可用于 LangChain Gateway</div>
        </div>
        <div class="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
          <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">默认角色</div>
          <div class="mt-2 text-2xl font-semibold text-gray-900">{{ defaultRoleCount }}/3</div>
          <div class="mt-1 text-xs text-gray-500">Planner / Coder / Vision</div>
        </div>
        <div
          v-for="role in roleCards"
          :key="role.label"
          class="rounded-xl border p-4 shadow-sm"
          :class="role.ready ? 'border-emerald-100 bg-emerald-50' : 'border-amber-100 bg-amber-50'"
        >
          <div class="flex items-center justify-between gap-3">
            <div class="text-sm font-bold text-gray-900">{{ role.label }}</div>
            <span class="rounded px-2 py-0.5 text-[10px] font-bold" :class="role.ready ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'">
              {{ role.ready ? 'Ready' : 'Missing' }}
            </span>
          </div>
          <div class="mt-2 text-xs leading-5 text-gray-600">{{ role.detail }}</div>
        </div>
      </div>
      <button
        @click="router.push('/run')"
        class="inline-flex items-center justify-center gap-2 rounded-lg bg-gray-900 px-4 py-2.5 text-sm font-bold text-white transition-all hover:bg-black"
      >
        <Bot :size="16" /> 去运行预检
      </button>
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

            <div class="grid grid-cols-2 gap-3">
              <div>
                <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">Max Tokens</label>
                <input v-model.number="form.max_tokens" type="number" min="1"
                  class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all" />
              </div>
              <div>
                <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">Temperature</label>
                <input v-model.number="form.temperature" type="number" min="0" max="2" step="0.1"
                  class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all" />
              </div>
            </div>

            <div>
              <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">Agent Type</label>
              <input v-model="form.agent_type" placeholder="可选，例如 planner / coder"
                class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all" />
            </div>

            <div>
              <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">System Prompt</label>
              <textarea v-model="form.system_prompt" rows="3" placeholder="可选，覆盖该模型的系统提示词"
                class="w-full resize-none px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all" />
            </div>

            <div class="grid grid-cols-3 gap-2">
              <label class="flex items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs font-bold text-gray-600">
                <input v-model="form.is_default_coder" type="checkbox" /> Coder
              </label>
              <label class="flex items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs font-bold text-gray-600">
                <input v-model="form.is_default_vision" type="checkbox" /> Vision
              </label>
              <label class="flex items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs font-bold text-gray-600">
                <input v-model="form.is_default_planner" type="checkbox" /> Planner
              </label>
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
                  <button @click="startEdit(item)"
                    class="px-3 py-1 bg-white border border-gray-200 hover:bg-gray-50 hover:border-gray-300 rounded text-[10px] font-bold text-gray-500 hover:text-gray-800 transition-all flex items-center gap-1">
                    <Edit3 :size="10" />
                    编辑
                  </button>
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

    <div v-if="editing" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div class="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-xl border border-gray-200 bg-white shadow-xl">
        <div class="flex items-center justify-between border-b border-gray-100 px-6 py-4">
          <div>
            <h3 class="text-sm font-bold text-gray-900">编辑模型配置</h3>
            <p class="mt-1 text-xs text-gray-500">留空 API Key 会保留当前密钥。</p>
          </div>
          <button @click="cancelEdit" class="rounded-lg p-2 text-gray-400 transition-all hover:bg-gray-100 hover:text-gray-700">
            <X :size="18" />
          </button>
        </div>

        <form class="space-y-4 px-6 py-5" @submit.prevent="saveEdit">
          <div class="grid gap-4 md:grid-cols-2">
            <div>
              <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">名称</label>
              <input v-model="editForm.name" required
                class="w-full rounded-lg border border-gray-200 bg-gray-50 px-4 py-2.5 text-sm outline-none transition-all focus:border-blue-500 focus:bg-white" />
            </div>
            <div>
              <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">类型</label>
              <select v-model="editForm.type"
                class="w-full rounded-lg border border-gray-200 bg-gray-50 px-4 py-2.5 text-sm outline-none transition-all focus:border-blue-500 focus:bg-white">
                <option value="openai">openai</option>
                <option value="anthropic">anthropic</option>
              </select>
            </div>
          </div>

          <div class="grid gap-4 md:grid-cols-2">
            <div>
              <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">模型名</label>
              <input v-model="editForm.model_name" required
                class="w-full rounded-lg border border-gray-200 bg-gray-50 px-4 py-2.5 text-sm outline-none transition-all focus:border-blue-500 focus:bg-white" />
            </div>
            <div>
              <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">Agent Type</label>
              <input v-model="editForm.agent_type" placeholder="可选"
                class="w-full rounded-lg border border-gray-200 bg-gray-50 px-4 py-2.5 text-sm outline-none transition-all focus:border-blue-500 focus:bg-white" />
            </div>
          </div>

          <div>
            <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">API Key</label>
            <input v-model="editForm.api_key" type="password" placeholder="留空表示不修改"
              class="w-full rounded-lg border border-gray-200 bg-gray-50 px-4 py-2.5 font-mono text-sm outline-none transition-all focus:border-blue-500 focus:bg-white" />
          </div>

          <div>
            <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">Base URL</label>
            <input v-model="editForm.base_url" placeholder="可选"
              class="w-full rounded-lg border border-gray-200 bg-gray-50 px-4 py-2.5 text-sm outline-none transition-all focus:border-blue-500 focus:bg-white" />
          </div>

          <div class="grid gap-4 md:grid-cols-2">
            <div>
              <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">Max Tokens</label>
              <input v-model.number="editForm.max_tokens" type="number" min="1"
                class="w-full rounded-lg border border-gray-200 bg-gray-50 px-4 py-2.5 text-sm outline-none transition-all focus:border-blue-500 focus:bg-white" />
            </div>
            <div>
              <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">Temperature</label>
              <input v-model.number="editForm.temperature" type="number" min="0" max="2" step="0.1"
                class="w-full rounded-lg border border-gray-200 bg-gray-50 px-4 py-2.5 text-sm outline-none transition-all focus:border-blue-500 focus:bg-white" />
            </div>
          </div>

          <div>
            <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">System Prompt</label>
            <textarea v-model="editForm.system_prompt" rows="5" placeholder="可选，覆盖该模型的系统提示词"
              class="w-full resize-none rounded-lg border border-gray-200 bg-gray-50 px-4 py-2.5 text-sm outline-none transition-all focus:border-blue-500 focus:bg-white" />
          </div>

          <div class="grid grid-cols-3 gap-2">
            <label class="flex items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs font-bold text-gray-600">
              <input v-model="editForm.is_default_coder" type="checkbox" /> Coder
            </label>
            <label class="flex items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs font-bold text-gray-600">
              <input v-model="editForm.is_default_vision" type="checkbox" /> Vision
            </label>
            <label class="flex items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs font-bold text-gray-600">
              <input v-model="editForm.is_default_planner" type="checkbox" /> Planner
            </label>
          </div>

          <div class="flex justify-end gap-2 border-t border-gray-100 pt-4">
            <button type="button" @click="cancelEdit" class="rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-bold text-gray-600 transition-all hover:bg-gray-50">
              取消
            </button>
            <button type="submit" class="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-bold text-white transition-all hover:bg-blue-700">
              <Save :size="15" /> 保存修改
            </button>
          </div>
        </form>
      </div>
    </div>
  </div>
</template>
