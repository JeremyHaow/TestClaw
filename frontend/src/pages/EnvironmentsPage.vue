<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../lib/api'
import { Globe, Plus, X } from 'lucide-vue-next'
import { useToast } from '../composables/useToast'
import EmptyState from '../components/EmptyState.vue'
import LoadingSpinner from '../components/LoadingSpinner.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import EnvironmentAssetCard from '../components/assets/EnvironmentAssetCard.vue'
import { compactLines, redactSensitiveText } from '../lib/assetHandoff'

interface KeyValue {
  key: string
  value: string
}

const toast = useToast()
const router = useRouter()
const items = ref<any[]>([])
const loading = ref(false)
const submitting = ref(false)
const editingId = ref<string | null>(null)
const deleteTarget = ref<any>(null)

const form = reactive({
  name: '',
  base_url: '',
  variables: [] as KeyValue[],
  is_production: false,
})

function resetForm() {
  form.name = ''
  form.base_url = ''
  form.variables = []
  form.is_production = false
  editingId.value = null
}

function variablesToObject(): Record<string, string> {
  const result: Record<string, string> = {}
  for (const kv of form.variables) {
    if (kv.key.trim()) {
      result[kv.key.trim()] = kv.value
    }
  }
  return result
}

function objectToVariables(obj: Record<string, any>): KeyValue[] {
  if (!obj || typeof obj !== 'object') return []
  return Object.entries(obj).map(([key, value]) => ({ key, value: String(value) }))
}

function addVariable() {
  form.variables.push({ key: '', value: '' })
}

function removeVariable(index: number) {
  form.variables.splice(index, 1)
}

async function fetchItems() {
  loading.value = true
  try {
    const { data } = await api.get('/environments')
    items.value = data
  } catch {
    toast.error('加载环境列表失败')
  } finally {
    loading.value = false
  }
}

function startEdit(item: any) {
  editingId.value = item.id
  form.name = item.name
  form.base_url = item.base_url || ''
  form.variables = objectToVariables(item.variables)
  form.is_production = item.is_production || false
}

function cancelEdit() {
  resetForm()
}

async function submit() {
  submitting.value = true
  try {
    const payload = {
      name: form.name,
      base_url: form.base_url,
      variables: variablesToObject(),
      is_production: form.is_production,
    }
    if (editingId.value) {
      await api.put(`/environments/${editingId.value}`, payload)
      toast.success('环境更新成功')
    } else {
      await api.post('/environments', payload)
      toast.success('环境创建成功')
    }
    resetForm()
    await fetchItems()
  } catch {
    toast.error(editingId.value ? '更新环境失败' : '创建环境失败')
  } finally {
    submitting.value = false
  }
}

function confirmDelete(item: any) {
  deleteTarget.value = item
}

async function doDelete() {
  if (!deleteTarget.value) return
  const target = deleteTarget.value
  try {
    await api.delete(`/environments/${target.id}`)
    toast.success(`环境 "${target.name}" 已删除`)
    deleteTarget.value = null
    if (editingId.value === target.id) resetForm()
    await fetchItems()
  } catch {
    toast.error('删除环境失败')
  }
}

async function copyEnvironment(item: any) {
  submitting.value = true
  try {
    await api.post('/environments', {
      name: `${item.name} (副本)`,
      base_url: item.base_url || '',
      variables: {},
      is_production: item.is_production || false,
    })
    toast.success('环境结构已复制，请重新填写变量')
    await fetchItems()
  } catch {
    toast.error('复制环境失败')
  } finally {
    submitting.value = false
  }
}

function startEnvironmentRun(item: any) {
  if (!item?.base_url) {
    startEdit(item)
    toast.warning('请先补充 Base URL，再用于运行')
    return
  }
  router.push({
    path: '/run',
    query: {
      base_url: item.base_url,
      source: item.base_url,
      test_type: 'ui',
      objective: `在「${item.name}」环境执行页面可达性、关键入口和安全边界冒烟检查。`,
      setup_instructions: `使用测试环境「${item.name}」。变量已在测试环境资产中维护，运行日志只显示脱敏值。${item.is_production ? '这是生产环境，保持安全只读策略。' : ''}`,
    },
  })
}

function environmentVariableKeys(item: any) {
  return Object.keys(item?.variables || {}).slice(0, 12)
}

function useEnvironmentForPlan(item: any) {
  const variableKeys = environmentVariableKeys(item)
  const context = compactLines([
    '从 TestClaw 测试环境资产创建新测试计划。',
    `环境：${item.name || '未命名环境'}`,
    item.base_url ? `Base URL：${item.base_url}` : 'Base URL：待补充',
    `默认策略：${item.is_production ? '生产环境，安全只读' : '测试环境，默认只读'}`,
    `变量键：${variableKeys.length ? variableKeys.join(', ') : '无'}`,
    '变量值不进入计划上下文；如需要凭证，请在计划模式中重新提供并由系统脱敏。',
    '建议范围：先做可达性与鉴权边界确认，再执行 API/UI 冒烟或回归检查。',
  ])
  router.push({
    path: '/agent-plan',
    query: {
      from: 'asset',
      asset_type: 'environment',
      asset_id: item.id,
      title: item.name || '测试环境',
      context: redactSensitiveText(context),
    },
  })
}

onMounted(fetchItems)
</script>

<template>
  <div class="mx-auto max-w-7xl space-y-5 pb-10">
    <div class="flex flex-wrap items-start justify-between gap-3 border-b border-gray-200/80 pb-5">
      <div class="flex flex-col gap-1">
        <div class="tc-page-kicker">Targets</div>
        <h2 class="text-xl font-semibold tracking-tight text-gray-950">测试环境</h2>
        <p class="max-w-3xl text-sm text-gray-500">维护可运行 Base URL 和变量。列表只展示脱敏变量，具体运行入口放在每个环境上。</p>
      </div>
    </div>

    <div class="grid grid-cols-1 gap-4 lg:grid-cols-[340px_minmax(0,1fr)] lg:items-start">
      <section class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm lg:sticky lg:top-4">
        <div class="mb-4 flex items-center justify-between">
          <h3 class="text-xs font-bold uppercase tracking-widest text-gray-400">
            {{ editingId ? '编辑环境' : '创建环境' }}
          </h3>
          <button v-if="editingId" @click="cancelEdit" type="button" class="rounded-lg p-1.5 text-gray-400 transition-all hover:bg-gray-100 hover:text-gray-700">
            <X :size="16" />
          </button>
        </div>

        <form class="space-y-4" @submit.prevent="submit">
          <div>
            <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">环境名</label>
            <input v-model="form.name" placeholder="测试环境" required
              class="w-full rounded-lg border border-gray-200 bg-gray-50 px-4 py-2.5 text-sm outline-none transition-all focus:border-blue-500 focus:bg-white" />
          </div>
          <div>
            <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">Base URL</label>
            <input v-model="form.base_url" placeholder="https://staging.example.com"
              class="w-full rounded-lg border border-gray-200 bg-gray-50 px-4 py-2.5 text-sm outline-none transition-all focus:border-blue-500 focus:bg-white" />
          </div>

          <div>
            <div class="mb-1.5 flex items-center justify-between">
              <label class="block text-[10px] font-bold uppercase tracking-widest text-gray-400">环境变量</label>
              <button type="button" @click="addVariable" class="inline-flex items-center gap-1 text-xs font-bold text-gray-700 hover:text-gray-950">
                <Plus :size="13" /> 添加
              </button>
            </div>
            <div class="max-h-64 overflow-y-auto rounded-lg border border-gray-200 bg-gray-50 p-2">
              <div v-if="form.variables.length" class="space-y-2">
                <div v-for="(kv, index) in form.variables" :key="index" class="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] items-center gap-2">
                  <input v-model="kv.key" placeholder="KEY"
                    class="min-w-0 rounded border border-gray-200 bg-white px-2 py-1.5 font-mono text-xs outline-none transition-all focus:border-blue-500" />
                  <input v-model="kv.value" placeholder="VALUE"
                    class="min-w-0 rounded border border-gray-200 bg-white px-2 py-1.5 font-mono text-xs outline-none transition-all focus:border-blue-500" />
                  <button type="button" @click="removeVariable(index)" class="rounded p-1 text-gray-400 transition-all hover:bg-red-50 hover:text-red-500" title="删除变量">
                    <X :size="14" />
                  </button>
                </div>
              </div>
              <div v-else class="px-4 py-6 text-center text-xs text-gray-400">暂无变量</div>
            </div>
            <p v-if="editingId" class="mt-2 text-[11px] leading-5 text-gray-400">脱敏值原样保存时会保留后端已有密文。</p>
          </div>

          <label class="flex items-center gap-2 text-sm text-gray-600">
            <input v-model="form.is_production" type="checkbox" class="rounded border-gray-300" />
            <span>生产环境</span>
          </label>

          <button type="submit" :disabled="submitting"
            class="w-full rounded-lg bg-gray-950 py-2.5 text-sm font-bold text-white transition-colors hover:bg-gray-800 disabled:opacity-50">
            {{ submitting ? '保存中...' : (editingId ? '更新环境' : '保存环境') }}
          </button>
        </form>
      </section>

      <section class="min-w-0 overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm lg:max-h-[calc(100vh-9rem)]">
        <div class="flex items-center justify-between border-b border-gray-100 px-5 py-4">
          <h3 class="text-sm font-bold text-gray-900">环境列表</h3>
          <span class="text-xs font-mono text-gray-400">{{ items.length }} 个</span>
        </div>
        <LoadingSpinner v-if="loading" text="加载中..." />
        <EmptyState v-else-if="!items.length" :icon="Globe" title="暂无环境配置" description="创建一个包含 Base URL 的环境后，可以直接带入运行页。" />
        <div v-else class="max-h-[calc(100vh-260px)] space-y-3 overflow-y-auto p-3">
          <EnvironmentAssetCard
            v-for="item in items"
            :key="item.id"
            :item="item"
            @run="startEnvironmentRun(item)"
            @plan="useEnvironmentForPlan(item)"
            @edit="startEdit(item)"
            @copy="copyEnvironment(item)"
            @delete="confirmDelete(item)"
          />
        </div>
      </section>
    </div>

    <ConfirmDialog
      :show="!!deleteTarget"
      title="删除环境"
      :message="`确定要删除环境「${deleteTarget?.name}」吗？此操作不可恢复。`"
      confirm-text="删除"
      cancel-text="取消"
      :danger="true"
      @confirm="doDelete"
      @cancel="deleteTarget = null"
    />
  </div>
</template>
