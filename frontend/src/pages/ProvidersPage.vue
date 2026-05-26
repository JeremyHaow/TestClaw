<script setup lang="ts">
import { computed, onMounted, reactive, ref, type Component } from 'vue'
import api from '../lib/api'
import { useToast } from '../composables/useToast'
import LoadingSpinner from '../components/LoadingSpinner.vue'
import EmptyState from '../components/EmptyState.vue'
import StyledSelect from '../components/StyledSelect.vue'
import {
  Bot,
  BrainCircuit,
  Code2,
  Compass,
  Edit3,
  Eye,
  KeyRound,
  Plus,
  Save,
  Search,
  Server,
  ShieldCheck,
  Sparkles,
  Trash2,
  X,
  Zap,
} from 'lucide-vue-next'

interface ProviderModel {
  id: string
  name: string
  type: string
  base_url?: string | null
  model_name: string
  is_default_coder: boolean
  is_default_vision: boolean
  is_default_planner: boolean
  max_tokens: number
  temperature: number
  is_active: boolean
  api_key_masked?: string | null
  system_prompt?: string | null
  agent_type?: string | null
}

interface ProviderGroup {
  key: string
  name: string
  type: string
  base_url: string
  api_key_masked: string
  models: ProviderModel[]
}

type ProviderRole = 'planner' | 'coder' | 'vision'

const toast = useToast()
const loading = ref(false)
const items = ref<ProviderModel[]>([])
const discoveredModels = ref<any[]>([])
const discovering = ref(false)
const discoverError = ref('')
const testingId = ref<string | null>(null)
const selectedProviderKey = ref('')
const showModelForm = ref(false)
const modelFormMode = ref<'create' | 'edit'>('create')

const modelForm = reactive({
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

const roleDefinitions: Array<{
  role: ProviderRole
  title: string
  description: string
  emptyHint: string
  icon: Component
  accentClass: string
}> = [
  {
    role: 'planner',
    title: '规划模型',
    description: '用于生成测试计划和用例',
    emptyHint: '未配置默认规划模型',
    icon: BrainCircuit,
    accentClass: 'bg-emerald-50 text-emerald-700',
  },
  {
    role: 'coder',
    title: '执行模型',
    description: '用于生成可执行脚本、断言和修复建议',
    emptyHint: '未配置默认执行模型',
    icon: Code2,
    accentClass: 'bg-blue-50 text-blue-700',
  },
  {
    role: 'vision',
    title: '视觉模型',
    description: '用于读取截图、页面状态和视觉证据',
    emptyHint: '未配置默认视觉模型',
    icon: Eye,
    accentClass: 'bg-indigo-50 text-indigo-700',
  },
]

const agentStrategyModes: Array<{
  name: string
  policy: string
  description: string
  items: string[]
  icon: Component
  accentClass: string
}> = [
  {
    name: '保守模式',
    policy: 'safe_read_only',
    description: '默认任务边界，适合未知系统和真实环境。',
    items: ['默认只读', '不执行删除/修改', '失败后优先询问用户'],
    icon: ShieldCheck,
    accentClass: 'bg-emerald-50 text-emerald-700',
  },
  {
    name: '平衡模式',
    policy: 'safe_with_auth',
    description: '适合有测试账号和可控测试数据的常规回归。',
    items: ['允许临时测试数据', '自动重试', '自动补充边界用例'],
    icon: Sparkles,
    accentClass: 'bg-blue-50 text-blue-700',
  },
  {
    name: '探索模式',
    policy: 'write_allowed',
    description: '适合隔离测试环境下的主动路径发现。',
    items: ['更主动发现路径', '更高工具调用次数', '适合测试环境'],
    icon: Compass,
    accentClass: 'bg-amber-50 text-amber-700',
  },
]

function providerKeyFor(item: Pick<ProviderModel, 'name' | 'type' | 'base_url' | 'api_key_masked'>) {
  return [
    item.name || '未命名 Provider',
    item.type || 'openai',
    item.base_url || '',
    item.api_key_masked || '',
  ].join('||')
}

const providerGroups = computed<ProviderGroup[]>(() => {
  const groups = new Map<string, ProviderGroup>()
  for (const item of items.value) {
    const key = providerKeyFor(item)
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        name: item.name || '未命名 Provider',
        type: item.type || 'openai',
        base_url: item.base_url || '',
        api_key_masked: item.api_key_masked || '',
        models: [],
      })
    }
    groups.get(key)!.models.push(item)
  }
  return [...groups.values()].map((group) => ({
    ...group,
    models: [...group.models].sort((a, b) => a.model_name.localeCompare(b.model_name)),
  }))
})

const selectedProvider = computed(() => {
  if (!providerGroups.value.length) return null
  return providerGroups.value.find((group) => group.key === selectedProviderKey.value) || providerGroups.value[0]
})

function defaultModelForRole(role: ProviderRole) {
  if (role === 'planner') return items.value.find((item) => item.is_default_planner) || null
  if (role === 'coder') return items.value.find((item) => item.is_default_coder) || null
  return items.value.find((item) => item.is_default_vision) || null
}

const agentRoleCards = computed(() => roleDefinitions.map((role) => ({
  ...role,
  model: defaultModelForRole(role.role),
})))

function resetModelForm() {
  const firstModel = items.value.length === 0
  Object.assign(modelForm, {
    id: '',
    name: '',
    type: 'openai',
    api_key: '',
    model_name: '',
    base_url: '',
    is_default_coder: firstModel,
    is_default_vision: false,
    is_default_planner: firstModel,
    max_tokens: 4096,
    temperature: 0.2,
    system_prompt: '',
    agent_type: '',
  })
  discoveredModels.value = []
  discoverError.value = ''
}

async function fetchItems() {
  loading.value = true
  try {
    const { data } = await api.get('/providers')
    items.value = data
    if (!selectedProviderKey.value && providerGroups.value[0]) {
      selectedProviderKey.value = providerGroups.value[0].key
    }
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '加载模型列表失败')
  } finally {
    loading.value = false
  }
}

function openNewProvider() {
  resetModelForm()
  modelFormMode.value = 'create'
  showModelForm.value = true
}

function openAddModel(group: ProviderGroup) {
  resetModelForm()
  modelFormMode.value = 'create'
  modelForm.name = group.name
  modelForm.type = group.type
  modelForm.base_url = group.base_url
  showModelForm.value = true
}

function startEdit(item: ProviderModel) {
  resetModelForm()
  Object.assign(modelForm, {
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
  modelFormMode.value = 'edit'
  showModelForm.value = true
}

function closeModelForm() {
  showModelForm.value = false
  resetModelForm()
}

function modelPayload() {
  const payload: Record<string, any> = {
    name: modelForm.name.trim(),
    type: modelForm.type,
    model_name: modelForm.model_name.trim(),
    base_url: modelForm.base_url.trim() || null,
    is_default_coder: modelForm.is_default_coder,
    is_default_vision: modelForm.is_default_vision,
    is_default_planner: modelForm.is_default_planner,
    max_tokens: Number(modelForm.max_tokens) || 4096,
    temperature: Number(modelForm.temperature),
    system_prompt: modelForm.system_prompt.trim() || null,
    agent_type: modelForm.agent_type.trim() || null,
  }
  if (modelForm.api_key.trim()) payload.api_key = modelForm.api_key.trim()
  return payload
}

async function saveModel() {
  try {
    const payload = modelPayload()
    let saved: ProviderModel | null = null
    if (!payload.name || !payload.model_name) {
      toast.warning('Provider 名称和模型名不能为空')
      return
    }
    if (modelFormMode.value === 'create' && !payload.api_key) {
      toast.warning('新增 Provider/模型需要 API Key')
      return
    }
    if (modelFormMode.value === 'edit') {
      const { data } = await api.put(`/providers/${modelForm.id}`, payload)
      saved = data
      toast.success('模型配置已更新')
    } else {
      const { data } = await api.post('/providers', payload)
      saved = data
      toast.success('模型配置已保存')
    }
    closeModelForm()
    await fetchItems()
    if (saved) selectedProviderKey.value = providerKeyFor(saved)
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || (modelFormMode.value === 'edit' ? '更新模型配置失败' : '保存模型配置失败'))
  }
}

async function setDefault(id: string, role: string) {
  try {
    await api.put(`/providers/${id}/set-default`, null, { params: { role } })
    toast.success(`已设置为默认${roleDisplayLabel(role as ProviderRole)}模型`)
    await fetchItems()
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '设置默认模型失败')
  }
}

async function discoverModels() {
  discovering.value = true
  discoverError.value = ''
  discoveredModels.value = []
  try {
    const { data } = await api.post('/providers/discover-models', {
      type: modelForm.type,
      api_key: modelForm.api_key,
      base_url: modelForm.base_url || undefined,
    })
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

function selectModel(model: any) {
  modelForm.model_name = model.id
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
    if (data.status === 'error') {
      toast.error(`连接测试失败: ${data.detail || 'provider returned error'}`)
      return
    }
    toast.success(`连接测试成功 (${data.latency_ms ?? data.latency ?? 'N/A'}ms)`)
  } catch (err: any) {
    toast.error(`连接测试失败: ${err?.response?.data?.detail || err.message}`)
  } finally {
    testingId.value = null
  }
}

function roleLabels(item: ProviderModel): ProviderRole[] {
  const roles: ProviderRole[] = []
  if (item.is_default_planner) roles.push('planner')
  if (item.is_default_coder) roles.push('coder')
  if (item.is_default_vision) roles.push('vision')
  return roles
}

function roleDisplayLabel(role: ProviderRole) {
  if (role === 'planner') return '规划'
  if (role === 'coder') return '执行'
  return '视觉'
}

function roleToneClass(role: ProviderRole) {
  if (role === 'planner') return 'border-emerald-100 bg-emerald-50 text-emerald-700'
  if (role === 'coder') return 'border-blue-100 bg-blue-50 text-blue-700'
  return 'border-indigo-100 bg-indigo-50 text-indigo-700'
}

function modelStatusLabel(model: ProviderModel | null) {
  if (!model) return '未配置'
  return model.is_active ? '可用' : '停用'
}

onMounted(fetchItems)
</script>

<template>
  <div class="mx-auto max-w-7xl space-y-5 pb-10">
    <div class="flex flex-wrap items-start justify-between gap-3 border-b border-gray-200/80 pb-5">
      <div class="flex flex-col gap-1">
        <div class="tc-page-kicker">模型</div>
        <h2 class="text-xl font-semibold tracking-tight text-gray-950">模型与 Agent</h2>
        <p class="max-w-3xl text-sm text-gray-500">
          先配置模型服务商（Provider），再在服务商下维护具体模型。规划、执行和视觉默认角色只设置在模型行上。
        </p>
      </div>
      <button
        @click="openNewProvider"
        class="inline-flex items-center justify-center gap-2 rounded-lg bg-gray-950 px-4 py-2 text-sm font-bold text-white transition-colors hover:bg-gray-800"
      >
        <Plus :size="16" /> 新增服务商
      </button>
    </div>

    <LoadingSpinner v-if="loading" text="加载模型列表中..." />

    <template v-else>
      <section class="grid gap-3 lg:grid-cols-3">
        <article
          v-for="role in agentRoleCards"
          :key="role.role"
          data-testid="provider-role-card"
          class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm"
        >
          <div class="flex items-start gap-3">
            <div class="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg" :class="role.accentClass">
              <component :is="role.icon" :size="18" />
            </div>
            <div class="min-w-0 flex-1">
              <h3 class="text-sm font-semibold text-gray-950">{{ role.title }}</h3>
              <p class="mt-1 text-xs leading-5 text-gray-500">{{ role.description }}</p>
            </div>
          </div>

          <div class="mt-4 space-y-2 border-t border-gray-100 pt-3 text-xs">
            <div class="flex items-center justify-between gap-3">
              <span class="text-gray-400">当前</span>
              <span class="min-w-0 truncate font-mono font-semibold text-gray-800">
                {{ role.model?.model_name || role.emptyHint }}
              </span>
            </div>
            <div class="flex items-center justify-between gap-3">
              <span class="text-gray-400">状态</span>
              <span
                class="rounded-full border px-2 py-0.5 text-[10px] font-bold"
                :class="role.model?.is_active ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-amber-200 bg-amber-50 text-amber-700'"
              >
                {{ modelStatusLabel(role.model) }}
              </span>
            </div>
            <div v-if="role.model" class="truncate text-[11px] text-gray-400">
              {{ role.model.name }} / {{ role.model.type }}
            </div>
          </div>

          <div class="mt-4 flex flex-wrap items-center gap-2">
            <template v-if="role.model">
              <button
                type="button"
                class="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-bold text-gray-700 transition-all hover:border-gray-300 hover:bg-gray-50"
                @click="setDefault(role.model.id, role.role)"
              >
                设为默认
              </button>
              <button
                type="button"
                :disabled="testingId === role.model.id"
                class="inline-flex items-center gap-1.5 rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs font-bold text-amber-700 transition-all hover:bg-amber-100 disabled:opacity-50"
                @click="testProvider(role.model.id)"
              >
                <Zap :size="13" /> 测试连接
              </button>
              <button
                type="button"
                class="inline-flex items-center gap-1.5 rounded-lg border border-blue-200 bg-blue-50 px-3 py-1.5 text-xs font-bold text-blue-700 transition-all hover:bg-blue-100"
                @click="startEdit(role.model)"
              >
                <Edit3 :size="13" /> 编辑
              </button>
            </template>
            <button
              v-else
              type="button"
              class="inline-flex items-center gap-1.5 rounded-lg bg-gray-950 px-3 py-1.5 text-xs font-bold text-white transition-all hover:bg-gray-800"
              @click="openNewProvider"
            >
              <Plus :size="13" /> 配置模型
            </button>
          </div>
        </article>
      </section>

      <section class="space-y-3">
        <div class="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h3 class="text-sm font-bold text-gray-900">Agent 策略</h3>
            <p class="mt-1 text-xs text-gray-500">
              策略在智能计划和任务委派中落到 api_execution_policy，本页只展示行为边界，不伪造未持久化的全局开关。
            </p>
          </div>
          <span class="rounded-full border border-gray-200 bg-white px-3 py-1 text-[10px] font-bold uppercase text-gray-500">
            策略模式
          </span>
        </div>
        <div class="grid gap-3 lg:grid-cols-3">
          <article
            v-for="mode in agentStrategyModes"
            :key="mode.name"
            data-testid="agent-strategy-card"
            class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm"
          >
            <div class="flex items-start gap-3">
              <div class="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg" :class="mode.accentClass">
                <component :is="mode.icon" :size="18" />
              </div>
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <h4 class="text-sm font-semibold text-gray-950">{{ mode.name }}</h4>
                  <span class="rounded border border-gray-200 bg-gray-50 px-2 py-0.5 font-mono text-[10px] font-bold text-gray-500">
                    {{ mode.policy }}
                  </span>
                </div>
                <p class="mt-1 text-xs leading-5 text-gray-500">{{ mode.description }}</p>
              </div>
            </div>
            <ul class="mt-4 space-y-1.5 border-t border-gray-100 pt-3 text-xs text-gray-600">
              <li v-for="item in mode.items" :key="item" class="flex gap-2">
                <span class="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-gray-300" />
                <span>{{ item }}</span>
              </li>
            </ul>
          </article>
        </div>
      </section>

      <div v-if="!providerGroups.length" class="rounded-lg border border-gray-200 bg-white">
        <EmptyState :icon="Bot" title="还没有模型服务商" description="新增服务商并添加第一个模型后，Agent 运行预检才能选择可用模型。" />
      </div>

      <div v-else class="grid gap-4 lg:grid-cols-[300px_minmax(0,1fr)]">
      <aside class="space-y-3 lg:sticky lg:top-4 lg:max-h-[calc(100vh-9rem)] lg:overflow-y-auto">
        <div class="rounded-lg border border-gray-200 bg-white p-3 shadow-sm">
          <div class="mb-3 flex items-center justify-between">
            <h3 class="text-xs font-bold uppercase tracking-widest text-gray-400">服务商</h3>
            <span class="text-xs font-mono text-gray-400">{{ providerGroups.length }}</span>
          </div>
          <div class="max-h-[calc(100vh-15rem)] space-y-2 overflow-y-auto pr-1">
            <button
              v-for="group in providerGroups"
              :key="group.key"
              @click="selectedProviderKey = group.key"
              class="w-full rounded-lg border p-3 text-left transition-all"
              :class="selectedProvider?.key === group.key ? 'border-gray-300 bg-gray-100' : 'border-gray-200 bg-white hover:border-gray-300 hover:bg-gray-50'"
            >
              <div class="flex items-start justify-between gap-2">
                <div class="min-w-0">
                  <div class="truncate text-sm font-bold text-gray-900">{{ group.name }}</div>
                  <div class="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] font-bold uppercase text-gray-500">
                    <span class="rounded border border-gray-200 bg-white px-1.5 py-0.5">{{ group.type }}</span>
                    <span class="rounded border border-gray-200 bg-white px-1.5 py-0.5">{{ group.models.length }} 个模型</span>
                  </div>
                </div>
                <Server :size="16" class="shrink-0 text-gray-400" />
              </div>
              <div class="mt-2 truncate text-[11px] font-mono text-gray-400">{{ group.base_url || '默认端点' }}</div>
              <div v-if="group.api_key_masked" class="mt-1 flex items-center gap-1 text-[11px] font-mono text-gray-400">
                <KeyRound :size="12" /> {{ group.api_key_masked }}
              </div>
            </button>
          </div>
        </div>
      </aside>

      <section v-if="selectedProvider" class="min-w-0 overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm lg:flex lg:max-h-[calc(100vh-9rem)] lg:flex-col">
        <div class="flex flex-wrap items-start justify-between gap-3 border-b border-gray-100 px-5 py-4">
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <h3 class="truncate text-base font-bold text-gray-900">{{ selectedProvider.name }}</h3>
              <span class="rounded border border-gray-200 bg-gray-50 px-2 py-0.5 text-[10px] font-bold uppercase text-gray-500">
                {{ selectedProvider.type }}
              </span>
            </div>
            <div class="mt-1 truncate text-xs font-mono text-gray-400">{{ selectedProvider.base_url || '使用 SDK 默认 Base URL' }}</div>
            <p class="mt-2 text-xs text-gray-500">
              角色默认值在具体模型上生效。新增同服务商模型时需要重新输入 API Key；后端不会返回明文密钥。
            </p>
          </div>
          <button
            @click="openAddModel(selectedProvider)"
            class="inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-bold text-gray-600 transition-all hover:border-blue-200 hover:bg-blue-50 hover:text-blue-700"
          >
            <Plus :size="14" /> 添加模型
          </button>
        </div>

        <div class="min-h-0 overflow-auto">
          <table class="w-full text-left text-sm">
            <thead>
              <tr class="border-b border-gray-100 bg-gray-50 text-[10px] font-bold uppercase tracking-widest text-gray-400">
                <th class="px-5 py-3">模型</th>
                <th class="px-5 py-3">参数</th>
                <th class="px-5 py-3">默认角色</th>
                <th class="px-5 py-3 text-right">操作</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-100">
              <tr v-for="model in selectedProvider.models" :key="model.id" class="hover:bg-gray-50">
                <td class="px-5 py-4">
                  <div class="font-semibold text-gray-900">{{ model.model_name }}</div>
                  <div class="mt-1 text-xs text-gray-500">{{ model.agent_type || '未指定 Agent Type' }}</div>
                  <div v-if="model.system_prompt" class="mt-1 max-w-sm truncate text-[11px] text-gray-400">{{ model.system_prompt }}</div>
                </td>
                <td class="px-5 py-4 text-xs text-gray-500">
                  <div>tokens {{ model.max_tokens }}</div>
                  <div>temp {{ model.temperature }}</div>
                </td>
                <td class="px-5 py-4">
                  <div v-if="roleLabels(model).length" class="flex flex-wrap gap-1.5">
                    <span
                      v-for="role in roleLabels(model)"
                      :key="role"
                      class="rounded border px-2 py-0.5 text-[10px] font-bold"
                      :class="roleToneClass(role)"
                    >
                      {{ roleDisplayLabel(role) }}
                    </span>
                  </div>
                  <span v-else class="text-xs text-gray-400">未设为默认</span>
                </td>
                <td class="px-5 py-4">
                  <div class="flex flex-wrap items-center justify-end gap-1.5">
                    <button @click="setDefault(model.id, 'planner')" class="rounded border border-gray-200 bg-white px-2 py-1 text-[10px] font-bold text-gray-500 transition-all hover:bg-emerald-50 hover:text-emerald-700">设为规划</button>
                    <button @click="setDefault(model.id, 'coder')" class="rounded border border-gray-200 bg-white px-2 py-1 text-[10px] font-bold text-gray-500 transition-all hover:bg-blue-50 hover:text-blue-700">设为执行</button>
                    <button @click="setDefault(model.id, 'vision')" class="rounded border border-gray-200 bg-white px-2 py-1 text-[10px] font-bold text-gray-500 transition-all hover:bg-indigo-50 hover:text-indigo-700">设为视觉</button>
                    <button @click="testProvider(model.id)" :disabled="testingId === model.id" class="rounded-lg p-1.5 text-gray-400 transition-all hover:bg-amber-50 hover:text-amber-600 disabled:opacity-50" title="测试连接">
                      <Zap :size="14" />
                    </button>
                    <button @click="startEdit(model)" class="rounded-lg p-1.5 text-gray-400 transition-all hover:bg-blue-50 hover:text-blue-600" title="编辑">
                      <Edit3 :size="14" />
                    </button>
                    <button @click="deleteProvider(model.id, model.model_name)" class="rounded-lg p-1.5 text-gray-400 transition-all hover:bg-red-50 hover:text-red-600" title="删除">
                      <Trash2 :size="14" />
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
      </div>
    </template>

    <div v-if="showModelForm" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div class="max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-lg border border-gray-200 bg-white shadow-xl">
        <div class="flex items-center justify-between border-b border-gray-100 px-6 py-4">
          <div>
            <h3 class="text-sm font-bold text-gray-900">{{ modelFormMode === 'edit' ? '编辑模型' : '配置服务商与模型' }}</h3>
            <p class="mt-1 text-xs text-gray-500">
              {{ modelFormMode === 'edit' ? '留空 API Key 会保留当前密钥。' : '当前后端以一行模型配置保存服务商信息，因此新增时需要同时填写第一个模型。' }}
            </p>
          </div>
          <button
            type="button"
            aria-label="关闭服务商配置"
            title="关闭服务商配置"
            @click="closeModelForm"
            class="rounded-lg p-2 text-gray-400 transition-all hover:bg-gray-100 hover:text-gray-700"
          >
            <X :size="18" />
          </button>
        </div>

        <form class="space-y-5 px-6 py-5" @submit.prevent="saveModel">
          <section class="grid gap-4 md:grid-cols-2">
            <div>
              <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">服务商名称</label>
              <input v-model="modelForm.name" required placeholder="OpenAI / 内部网关"
                class="w-full rounded-lg border border-gray-200 bg-gray-50 px-4 py-2.5 text-sm outline-none transition-all focus:border-blue-500 focus:bg-white" />
            </div>
            <div>
              <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">类型</label>
              <StyledSelect v-model="modelForm.type">
                <option value="openai">openai</option>
                <option value="anthropic">anthropic</option>
              </StyledSelect>
            </div>
            <div>
              <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">Base URL</label>
              <input v-model="modelForm.base_url" placeholder="可选，例如 https://api.openai.com/v1"
                class="w-full rounded-lg border border-gray-200 bg-gray-50 px-4 py-2.5 text-sm outline-none transition-all focus:border-blue-500 focus:bg-white" />
            </div>
            <div>
              <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">API Key</label>
              <input v-model="modelForm.api_key" type="password" :required="modelFormMode !== 'edit'" placeholder="后端只保存加密值"
                class="w-full rounded-lg border border-gray-200 bg-gray-50 px-4 py-2.5 font-mono text-sm outline-none transition-all focus:border-blue-500 focus:bg-white" />
            </div>
          </section>

          <section class="space-y-4 rounded-lg border border-gray-200 bg-gray-50 p-4">
            <div class="flex flex-wrap items-center justify-between gap-3">
              <h4 class="text-xs font-bold uppercase tracking-widest text-gray-400">模型配置</h4>
              <button type="button" @click="discoverModels" :disabled="!modelForm.api_key || discovering"
                class="inline-flex items-center gap-2 rounded-lg bg-gray-950 px-3 py-2 text-xs font-bold text-white transition-all hover:bg-gray-800 disabled:opacity-50">
                <Search :size="14" />
                {{ discovering ? '发现中...' : '发现模型' }}
              </button>
            </div>
            <p v-if="discoverError" class="text-xs text-red-500">{{ discoverError }}</p>
            <div v-if="discoveredModels.length" class="max-h-28 overflow-y-auto rounded-lg border border-gray-200 bg-white p-2">
              <div class="flex flex-wrap gap-2">
                <button v-for="model in discoveredModels" :key="model.id" type="button" @click="selectModel(model)"
                  class="rounded-lg border px-3 py-1.5 text-xs font-bold transition-all"
                  :class="modelForm.model_name === model.id ? 'border-blue-200 bg-blue-50 text-blue-700' : 'border-gray-200 bg-gray-50 text-gray-600 hover:bg-gray-100'">
                  {{ model.display_name || model.id }}
                </button>
              </div>
            </div>

            <div class="grid gap-4 md:grid-cols-2">
              <div>
                <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">模型名</label>
                <input v-model="modelForm.model_name" required placeholder="gpt-4o-mini"
                  class="w-full rounded-lg border border-gray-200 bg-white px-4 py-2.5 text-sm outline-none transition-all focus:border-blue-500" />
              </div>
              <div>
                <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">Agent 类型</label>
                <input v-model="modelForm.agent_type" placeholder="可选，例如 planner / coder"
                  class="w-full rounded-lg border border-gray-200 bg-white px-4 py-2.5 text-sm outline-none transition-all focus:border-blue-500" />
              </div>
              <div>
                <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">最大 Token 数</label>
                <input v-model.number="modelForm.max_tokens" type="number" min="1"
                  class="w-full rounded-lg border border-gray-200 bg-white px-4 py-2.5 text-sm outline-none transition-all focus:border-blue-500" />
              </div>
              <div>
                <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">温度</label>
                <input v-model.number="modelForm.temperature" type="number" min="0" max="2" step="0.1"
                  class="w-full rounded-lg border border-gray-200 bg-white px-4 py-2.5 text-sm outline-none transition-all focus:border-blue-500" />
              </div>
            </div>

            <div>
              <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">系统提示词</label>
              <textarea v-model="modelForm.system_prompt" rows="3" placeholder="可选，覆盖该模型的系统提示词"
                class="w-full resize-none rounded-lg border border-gray-200 bg-white px-4 py-2.5 text-sm outline-none transition-all focus:border-blue-500" />
            </div>

            <div class="grid gap-2 sm:grid-cols-3">
              <label class="flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-bold text-gray-600">
                <input v-model="modelForm.is_default_planner" type="checkbox" /> 规划默认
              </label>
              <label class="flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-bold text-gray-600">
                <input v-model="modelForm.is_default_coder" type="checkbox" /> 执行默认
              </label>
              <label class="flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-bold text-gray-600">
                <input v-model="modelForm.is_default_vision" type="checkbox" /> 视觉默认
              </label>
            </div>
          </section>

          <div class="flex justify-end gap-2 border-t border-gray-100 pt-4">
            <button type="button" @click="closeModelForm" class="rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-bold text-gray-600 transition-all hover:bg-gray-50">
              取消
            </button>
            <button type="submit" class="flex items-center gap-2 rounded-lg bg-gray-950 px-4 py-2 text-sm font-bold text-white transition-all hover:bg-gray-800">
              <Save :size="15" /> 保存
            </button>
          </div>
        </form>
      </div>
    </div>
  </div>
</template>
