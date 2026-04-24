<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import api from '../lib/api'
import { Globe, Shield, Plus, Pencil, Trash2, Copy, X } from 'lucide-vue-next'
import { useToast } from '../composables/useToast'
import EmptyState from '../components/EmptyState.vue'
import LoadingSpinner from '../components/LoadingSpinner.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'

const toast = useToast()
const items = ref<any[]>([])
const loading = ref(false)
const submitting = ref(false)
const editingId = ref<string | null>(null)
const deleteTarget = ref<any>(null)

interface KeyValue {
  key: string
  value: string
}

const form = reactive({
  name: '',
  base_url: '',
  variables: [{ key: 'TOKEN', value: 'demo' }] as KeyValue[],
  is_production: false,
})

function resetForm() {
  form.name = ''
  form.base_url = ''
  form.variables = [{ key: 'TOKEN', value: 'demo' }]
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
      variables: item.variables || {},
      is_production: item.is_production || false,
    })
    toast.success('环境复制成功')
    await fetchItems()
  } catch {
    toast.error('复制环境失败')
  } finally {
    submitting.value = false
  }
}

onMounted(fetchItems)
</script>

<template>
  <div class="space-y-8 pb-12">
    <div class="flex flex-col gap-1">
      <h2 class="text-2xl font-bold tracking-tight text-gray-900">环境管理</h2>
      <p class="text-gray-500 text-sm">管理测试环境和变量配置，支持多环境切换。</p>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
      <!-- Create / Edit Form -->
      <div class="lg:col-span-5">
        <div class="bg-white border border-gray-200 rounded-xl shadow-sm p-6 space-y-4">
          <div class="flex items-center justify-between">
            <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest">
              {{ editingId ? '编辑环境' : '创建环境' }}
            </h3>
            <button v-if="editingId" @click="cancelEdit" type="button"
              class="text-gray-400 hover:text-gray-600 transition-all">
              <X :size="16" />
            </button>
          </div>
          <form class="space-y-4" @submit.prevent="submit">
            <div>
              <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">环境名</label>
              <input v-model="form.name" placeholder="测试环境" required
                class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all" />
            </div>
            <div>
              <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">Base URL</label>
              <input v-model="form.base_url" placeholder="https://staging.example.com"
                class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all" />
            </div>

            <!-- Key-Value Editor -->
            <div>
              <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">环境变量</label>
              <div class="border border-gray-200 rounded-lg overflow-hidden">
                <div v-if="form.variables.length" class="divide-y divide-gray-100">
                  <div v-for="(kv, index) in form.variables" :key="index"
                    class="flex items-center gap-1 px-2 py-1.5 bg-white hover:bg-gray-50 transition-colors">
                    <input v-model="kv.key" placeholder="KEY"
                      class="flex-1 min-w-0 px-2 py-1.5 bg-gray-50 border border-gray-200 rounded text-xs font-mono outline-none focus:border-blue-500 focus:bg-white transition-all" />
                    <span class="text-gray-300 text-xs font-bold">=</span>
                    <input v-model="kv.value" placeholder="VALUE"
                      class="flex-1 min-w-0 px-2 py-1.5 bg-gray-50 border border-gray-200 rounded text-xs font-mono outline-none focus:border-blue-500 focus:bg-white transition-all" />
                    <button type="button" @click="removeVariable(index)"
                      class="p-1 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded transition-all shrink-0"
                      title="删除变量">
                      <X :size="14" />
                    </button>
                  </div>
                </div>
                <div v-else class="px-4 py-3 text-xs text-gray-400 text-center">
                  暂无变量
                </div>
                <button type="button" @click="addVariable"
                  class="w-full px-4 py-2 text-xs font-bold text-blue-600 bg-blue-50 hover:bg-blue-100 border-t border-gray-200 transition-all flex items-center justify-center gap-1">
                  <Plus :size="14" />
                  添加变量
                </button>
              </div>
            </div>

            <label class="flex items-center gap-2 text-sm text-gray-600">
              <input v-model="form.is_production" type="checkbox" class="rounded border-gray-300" />
              <span>生产环境</span>
            </label>
            <button type="submit" :disabled="submitting"
              class="w-full py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-bold transition-all shadow-md shadow-blue-600/10">
              {{ submitting ? '保存中...' : (editingId ? '更新环境' : '保存环境') }}
            </button>
          </form>
        </div>
      </div>

      <!-- Environment List -->
      <div class="lg:col-span-7 space-y-4">
        <LoadingSpinner v-if="loading" text="加载中..." />
        <EmptyState v-else-if="!items.length" :icon="Globe" title="暂无环境配置"
          description="创建一个测试环境来开始管理你的变量配置" />
        <template v-else>
          <div v-for="item in items" :key="item.id"
            class="bg-white border border-gray-200 rounded-xl shadow-sm p-5 hover:border-blue-200 transition-all">
            <div class="flex items-center justify-between mb-3">
              <div class="flex items-center gap-3">
                <div class="p-2 rounded-lg"
                  :class="item.is_production ? 'bg-amber-50 text-amber-600' : 'bg-blue-50 text-blue-600'">
                  <Globe :size="16" />
                </div>
                <div>
                  <div class="font-bold text-gray-900">{{ item.name }}</div>
                  <div class="text-xs font-mono text-gray-400">{{ item.base_url || '--' }}</div>
                </div>
              </div>
              <div class="flex items-center gap-2">
                <span v-if="item.is_production"
                  class="px-2 py-0.5 bg-amber-50 text-amber-700 rounded text-[10px] font-bold border border-amber-100">PROD</span>
                <span
                  class="px-2 py-0.5 bg-gray-100 text-gray-500 rounded text-[10px] font-bold border border-gray-200">
                  {{ item.variables ? Object.keys(item.variables).length : 0 }} 变量
                </span>
                <div class="flex gap-1 ml-2">
                  <button @click="startEdit(item)"
                    class="p-1.5 text-gray-400 hover:text-blue-600 hover:bg-blue-50 rounded-lg transition-all"
                    title="编辑">
                    <Pencil :size="14" />
                  </button>
                  <button @click="copyEnvironment(item)"
                    class="p-1.5 text-gray-400 hover:text-emerald-600 hover:bg-emerald-50 rounded-lg transition-all"
                    title="复制">
                    <Copy :size="14" />
                  </button>
                  <button @click="confirmDelete(item)"
                    class="p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-all"
                    title="删除">
                    <Trash2 :size="14" />
                  </button>
                </div>
              </div>
            </div>
            <div v-if="item.variables && Object.keys(item.variables).length"
              class="bg-gray-50 border border-gray-100 rounded-lg p-3 max-h-32 overflow-auto">
              <div v-for="(val, key) in item.variables" :key="key"
                class="flex items-center gap-2 text-xs font-mono">
                <span class="text-gray-400 font-bold">{{ key }}</span>
                <span class="text-gray-300">=</span>
                <span class="text-gray-600 truncate">{{ val }}</span>
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>

    <!-- Delete Confirm Dialog -->
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
