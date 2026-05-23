<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../lib/api'
import { useToast } from '../composables/useToast'
import { Upload, Database, FileCode, Pencil, Trash2, Check, X, Play } from 'lucide-vue-next'
import EmptyState from '../components/EmptyState.vue'
import LoadingSpinner from '../components/LoadingSpinner.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'

const toast = useToast()
const router = useRouter()

const items = ref<any[]>([])
const loading = ref(true)
const form = reactive({ name: '', raw_content: '', format: 'openapi' })
const uploading = ref(false)
const dragOver = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

// Inline edit state
const editingId = ref<string | null>(null)
const editingName = ref('')

// Delete confirm state
const deleteDialog = reactive({ show: false, id: '', name: '' })

// Endpoint method badge colors
const methodColors: Record<string, string> = {
  GET: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  POST: 'bg-blue-100 text-blue-700 border-blue-200',
  PUT: 'bg-amber-100 text-amber-700 border-amber-200',
  DELETE: 'bg-red-100 text-red-700 border-red-200',
  PATCH: 'bg-purple-100 text-purple-700 border-purple-200',
  OPTIONS: 'bg-gray-100 text-gray-600 border-gray-200',
  HEAD: 'bg-gray-100 text-gray-600 border-gray-200',
}
const endpointTotal = computed(() => items.value.reduce((total, item) => total + (item.parsed_endpoints?.length || 0), 0))
const readEndpointTotal = computed(() => items.value.reduce((total, item) => {
  return total + (item.parsed_endpoints || []).filter((ep: any) => String(ep.method || '').toUpperCase() === 'GET').length
}, 0))
const writeEndpointTotal = computed(() => Math.max(endpointTotal.value - readEndpointTotal.value, 0))

function getMethodColor(method: string) {
  return methodColors[method?.toUpperCase()] || 'bg-gray-100 text-gray-600 border-gray-200'
}

async function fetchItems() {
  loading.value = true
  try {
    const { data } = await api.get('/documents')
    items.value = data
  } catch (e: any) {
    toast.error('加载文档失败')
  } finally {
    loading.value = false
  }
}

async function submit() {
  try {
    await api.post('/documents/import', form)
    form.name = ''
    form.raw_content = ''
    form.format = 'openapi'
    toast.success('文档导入成功')
    await fetchItems()
  } catch (e: any) {
    toast.error('Import failed: ' + (e.response?.data?.detail || e.message))
  }
}

async function handleDrop(e: DragEvent) {
  dragOver.value = false
  const file = e.dataTransfer?.files?.[0]
  if (file) await uploadFile(file)
}

async function handleFileSelect(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) await uploadFile(file)
  input.value = ''
}

async function uploadFile(file: File) {
  uploading.value = true
  try {
    const formData = new FormData()
    formData.append('file', file)
    await api.post('/documents/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    toast.success('文件上传成功')
    await fetchItems()
  } catch (e: any) {
    toast.error('上传失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    uploading.value = false
  }
}

function startEdit(item: any) {
  editingId.value = item.id
  editingName.value = item.name
}

function cancelEdit() {
  editingId.value = null
  editingName.value = ''
}

async function saveEdit(id: string) {
  const name = editingName.value.trim()
  if (!name) {
    toast.warning('名称不能为空')
    return
  }
  try {
    await api.put(`/documents/${id}`, { name })
    toast.success('文档更新成功')
    editingId.value = null
    editingName.value = ''
    await fetchItems()
  } catch (e: any) {
    toast.error('更新失败: ' + (e.response?.data?.detail || e.message))
  }
}

function confirmDelete(item: any) {
  deleteDialog.id = item.id
  deleteDialog.name = item.name || `Document-${item.format}`
  deleteDialog.show = true
}

function startApiRun(item?: any) {
  const objective = item
    ? `基于已导入文档「${item.name || `Document-${item.format}`}」进行 API 契约、鉴权、边界值和错误分支检查。`
    : '基于已导入 OpenAPI / Postman 文档进行 API 契约、鉴权、边界值和错误分支检查。'
  router.push({ path: '/run', query: { test_type: 'api', objective } })
}

async function executeDelete() {
  try {
    await api.delete(`/documents/${deleteDialog.id}`)
    toast.success('文档已删除')
    deleteDialog.show = false
    deleteDialog.id = ''
    deleteDialog.name = ''
    await fetchItems()
  } catch (e: any) {
    toast.error('删除失败: ' + (e.response?.data?.detail || e.message))
  }
}

onMounted(fetchItems)
</script>

<template>
  <div class="space-y-8 pb-12">
    <div class="flex flex-col gap-1">
      <h2 class="text-2xl font-bold tracking-tight text-gray-900">接口文档</h2>
      <p class="text-gray-500 text-sm">导入 OpenAPI / Postman 文档，让智能体在 source_loader 中解析端点并生成 API 测试计划。</p>
    </div>

    <div class="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
      <div class="grid gap-3 md:grid-cols-3">
        <div class="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
          <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">文档</div>
          <div class="mt-2 text-2xl font-semibold text-gray-900">{{ items.length }}</div>
          <div class="mt-1 text-xs text-gray-500">可作为 API source</div>
        </div>
        <div class="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
          <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">端点</div>
          <div class="mt-2 text-2xl font-semibold text-gray-900">{{ endpointTotal }}</div>
          <div class="mt-1 text-xs text-gray-500">Planner 会按场景分组</div>
        </div>
        <div class="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
          <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">安全策略影响</div>
          <div class="mt-2 text-2xl font-semibold text-gray-900">{{ writeEndpointTotal }}</div>
          <div class="mt-1 text-xs text-gray-500">写入端点默认需确认/跳过</div>
        </div>
      </div>
      <button
        @click="startApiRun()"
        class="inline-flex items-center justify-center gap-2 rounded-lg bg-gray-900 px-4 py-2.5 text-sm font-bold text-white transition-all hover:bg-black"
      >
        <Play :size="16" /> 去创建 API 运行
      </button>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-2 gap-8 items-start">
      <!-- Left: Upload zone + import form -->
      <div class="space-y-6">
        <!-- Drag & Drop Upload -->
        <div
          @dragover.prevent="dragOver = true"
          @dragleave="dragOver = false"
          @drop.prevent="handleDrop"
          class="border-2 border-dashed rounded-xl p-8 text-center transition-all cursor-pointer"
          :class="dragOver ? 'border-blue-400 bg-blue-50' : 'border-gray-300 hover:border-blue-300'"
          @click="fileInput?.click()"
        >
          <Upload :size="32" class="mx-auto mb-3" :class="dragOver ? 'text-blue-500' : 'text-gray-400'" />
          <p class="text-sm font-bold" :class="dragOver ? 'text-blue-700' : 'text-gray-600'">
            {{ uploading ? '上传中...' : '拖拽文件到此处上传' }}
          </p>
          <p class="text-xs text-gray-400 mt-1">支持 .json, .yaml, .yml 格式</p>
          <input ref="fileInput" type="file" accept=".json,.yaml,.yml" @change="handleFileSelect" class="hidden" />
        </div>

        <div class="bg-white border border-gray-200 rounded-xl shadow-sm p-6 space-y-4">
          <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest">导入接口文档</h3>
          <form class="space-y-4" @submit.prevent="submit">
            <div>
              <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">名称</label>
              <input v-model="form.name" placeholder="OpenAPI 文档"
                class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all" />
            </div>
            <div>
              <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">格式</label>
              <select v-model="form.format"
                class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all">
                <option value="openapi">openapi</option>
                <option value="postman">postman</option>
                <option value="yaml">yaml</option>
              </select>
            </div>
            <div>
              <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">内容</label>
              <textarea v-model="form.raw_content" rows="12" placeholder="粘贴 OpenAPI / Postman 内容"
                class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm font-mono outline-none focus:border-blue-500 focus:bg-white transition-all resize-none" />
            </div>
            <button type="submit" class="w-full py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-bold transition-all shadow-md shadow-blue-600/10">
              导入文档
            </button>
          </form>
        </div>
      </div>

      <!-- Right: Document cards -->
      <div class="space-y-4">
        <h3 class="text-sm font-bold text-gray-900">已导入文档</h3>

        <LoadingSpinner v-if="loading" text="加载文档中..." />

        <EmptyState
          v-else-if="!items.length"
          :icon="FileCode"
          title="暂无文档"
          description="上传或导入 OpenAPI / Postman 文档开始使用"
        />

        <div v-else class="space-y-4">
          <div v-for="item in items" :key="item.id" class="bg-white border border-gray-200 rounded-xl shadow-sm p-5 hover:border-blue-200 transition-all">
            <!-- Header: icon + name + actions -->
            <div class="flex items-center gap-3 mb-3">
              <div class="p-2 bg-blue-50 rounded-lg text-blue-600 flex-shrink-0">
                <Database :size="16" />
              </div>

              <!-- Inline edit mode -->
              <div v-if="editingId === item.id" class="flex items-center gap-2 flex-1 min-w-0">
                <input
                  v-model="editingName"
                  @keyup.enter="saveEdit(item.id)"
                  @keyup.escape="cancelEdit"
                  class="flex-1 px-3 py-1.5 bg-gray-50 border border-blue-300 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all"
                  autofocus
                />
                <button @click="saveEdit(item.id)" class="p-1.5 text-emerald-600 hover:bg-emerald-50 rounded-lg transition-all" title="保存">
                  <Check :size="14" />
                </button>
                <button @click="cancelEdit" class="p-1.5 text-gray-400 hover:bg-gray-100 rounded-lg transition-all" title="取消">
                  <X :size="14" />
                </button>
              </div>

              <!-- Display mode -->
              <template v-else>
                <div class="flex-1 min-w-0">
                  <div class="font-bold text-gray-900 text-sm truncate">{{ item.name || `Document-${item.format}` }}</div>
                  <div class="text-[10px] font-mono text-gray-400 uppercase">{{ item.format }}</div>
                </div>
                <button @click="startEdit(item)" class="p-1.5 text-gray-400 hover:text-blue-600 hover:bg-blue-50 rounded-lg transition-all flex-shrink-0" title="编辑">
                  <Pencil :size="14" />
                </button>
                <button @click="confirmDelete(item)" class="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded-lg transition-all flex-shrink-0" title="删除">
                  <Trash2 :size="14" />
                </button>
                <button @click="startApiRun(item)" class="p-1.5 text-gray-400 hover:text-emerald-600 hover:bg-emerald-50 rounded-lg transition-all flex-shrink-0" title="创建 API 运行">
                  <Play :size="14" />
                </button>
              </template>
            </div>

            <!-- Endpoint badges -->
            <div v-if="item.parsed_endpoints?.length" class="flex flex-wrap gap-1.5">
              <div
                v-for="(ep, idx) in item.parsed_endpoints"
                :key="idx"
                class="inline-flex items-center gap-1.5 px-2 py-1 rounded-md border text-[10px] font-mono"
                :class="getMethodColor(ep.method)"
              >
                <span class="font-bold">{{ (ep.method || 'GET').toUpperCase() }}</span>
                <span class="opacity-75">{{ ep.path || ep.url || '/' }}</span>
              </div>
            </div>
            <div v-else class="text-[10px] text-gray-400 italic">暂无端点</div>
          </div>
        </div>
      </div>
    </div>

    <!-- Delete confirmation dialog -->
    <ConfirmDialog
      :show="deleteDialog.show"
      title="删除文档"
      :message="`确定要删除「${deleteDialog.name}」吗？此操作不可恢复。`"
      confirm-text="删除"
      cancel-text="取消"
      :danger="true"
      @confirm="executeDelete"
      @cancel="deleteDialog.show = false"
    />
  </div>
</template>
