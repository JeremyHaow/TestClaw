<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import api from '../lib/api'
import { useToast } from '../composables/useToast'
import {
  Check,
  ChevronLeft,
  ChevronRight,
  FileCode,
  Link2,
  Pencil,
  Play,
  Search,
  Sparkles,
  Trash2,
  Upload,
  X,
} from 'lucide-vue-next'
import EmptyState from '../components/EmptyState.vue'
import LoadingSpinner from '../components/LoadingSpinner.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import StyledSelect from '../components/StyledSelect.vue'
import DocumentAssetCard from '../components/assets/DocumentAssetCard.vue'
import { compactLines, redactSensitiveText } from '../lib/assetHandoff'

const REAL_DOCS_URL = 'http://60.204.225.104/api/v3/api-docs'
const endpointPageSize = 12

const toast = useToast()
const router = useRouter()

const items = ref<any[]>([])
const loading = ref(true)
const importing = ref(false)
const uploading = ref(false)
const dragOver = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const importMode = ref<'url' | 'raw'>('url')
const selectedId = ref('')
const viewMode = ref<'endpoints' | 'raw'>('endpoints')
const endpointSearch = ref('')
const methodFilter = ref('')
const endpointPage = ref(1)
const selectedEndpoint = ref<any | null>(null)
const editing = ref(false)

const form = reactive({
  name: 'wms_接口文档',
  url: REAL_DOCS_URL,
  raw_content: '',
  format: 'openapi',
})

const editForm = reactive({
  id: '',
  name: '',
  source_url: '',
  raw_content: '',
  format: 'openapi',
})

const deleteDialog = reactive({ show: false, id: '', name: '' })

const methodColors: Record<string, string> = {
  GET: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  POST: 'bg-blue-50 text-blue-700 border-blue-200',
  PUT: 'bg-amber-50 text-amber-700 border-amber-200',
  DELETE: 'bg-red-50 text-red-700 border-red-200',
  PATCH: 'bg-purple-50 text-purple-700 border-purple-200',
  OPTIONS: 'bg-gray-50 text-gray-600 border-gray-200',
  HEAD: 'bg-gray-50 text-gray-600 border-gray-200',
}

const selectedDoc = computed(() => items.value.find((item) => item.id === selectedId.value) || items.value[0] || null)
const selectedEndpoints = computed(() => selectedDoc.value?.parsed_endpoints || [])
const methodOptions = computed(() => {
  const methods = new Set<string>()
  for (const endpoint of selectedEndpoints.value) {
    const method = String(endpoint.method || '').toUpperCase()
    if (method) methods.add(method)
  }
  return [...methods].sort()
})
const filteredEndpoints = computed(() => {
  const query = endpointSearch.value.trim().toLowerCase()
  return selectedEndpoints.value.filter((endpoint: any) => {
    const method = String(endpoint.method || '').toUpperCase()
    if (methodFilter.value && method !== methodFilter.value) return false
    if (!query) return true
    const haystack = [
      method,
      endpoint.path,
      endpoint.url,
      endpoint.summary,
      endpoint.operationId,
      ...(Array.isArray(endpoint.tags) ? endpoint.tags : []),
    ].join(' ').toLowerCase()
    return haystack.includes(query)
  })
})
const endpointTotalPages = computed(() => Math.max(1, Math.ceil(filteredEndpoints.value.length / endpointPageSize)))
const pagedEndpoints = computed(() => {
  const start = (endpointPage.value - 1) * endpointPageSize
  return filteredEndpoints.value.slice(start, start + endpointPageSize)
})
const endpointRangeLabel = computed(() => {
  if (!filteredEndpoints.value.length) return '0 / 0'
  const start = (endpointPage.value - 1) * endpointPageSize + 1
  const end = Math.min(endpointPage.value * endpointPageSize, filteredEndpoints.value.length)
  return `${start}-${end} / ${filteredEndpoints.value.length}`
})
const selectedRawContent = computed(() => formatRawContent(selectedDoc.value?.raw_content))
const selectedRawLineCount = computed(() => selectedRawContent.value ? selectedRawContent.value.split('\n').length : 0)

watch([selectedId, endpointSearch, methodFilter], () => {
  endpointPage.value = 1
  selectedEndpoint.value = null
})

watch(endpointPage, () => {
  selectedEndpoint.value = null
})

function getMethodColor(method: string) {
  return methodColors[String(method || '').toUpperCase()] || 'bg-gray-50 text-gray-600 border-gray-200'
}

function endpointPath(endpoint: any) {
  return endpoint.path || endpoint.url || '/'
}

function endpointTitle(endpoint: any) {
  return endpoint.summary || endpoint.operationId || endpointPath(endpoint)
}

function prettyJson(value: any) {
  if (value === undefined || value === null || value === '') return '无'
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function looksLikeJsonContainer(value: string) {
  const trimmed = value.trim()
  return (
    (trimmed.startsWith('{') && trimmed.endsWith('}')) ||
    (trimmed.startsWith('[') && trimmed.endsWith(']'))
  )
}

function decodeEscapedJsonCandidate(value: string) {
  return value
    .replace(/\\"/g, '"')
    .replace(/\\r\\n/g, '\n')
    .replace(/\\n/g, '\n')
    .replace(/\\t/g, '\t')
}

function unwrapJsonPayload(value: any) {
  let current = value
  for (let depth = 0; depth < 3 && typeof current === 'string'; depth += 1) {
    const candidate = current.trim()
    if (!looksLikeJsonContainer(candidate)) break
    try {
      current = JSON.parse(candidate)
    } catch {
      break
    }
  }
  return current
}

function formatJsonText(raw: string) {
  const text = raw || ''
  const trimmed = text.trim()
  if (!trimmed) {
    return { ok: false, value: text, message: '原文为空，无法格式化 JSON' }
  }

  try {
    const parsed = unwrapJsonPayload(JSON.parse(trimmed))
    return { ok: true, value: JSON.stringify(parsed, null, 2) || text, message: '' }
  } catch {
    const decoded = decodeEscapedJsonCandidate(trimmed)
    if (decoded !== trimmed && looksLikeJsonContainer(decoded)) {
      try {
        const parsed = unwrapJsonPayload(JSON.parse(decoded))
        return { ok: true, value: JSON.stringify(parsed, null, 2) || text, message: '' }
      } catch {
        return { ok: false, value: text, message: '当前原文不是合法 JSON，已保留原文不变；YAML 可直接保存或导入。' }
      }
    }
    return { ok: false, value: text, message: '当前原文不是合法 JSON，已保留原文不变；YAML 可直接保存或导入。' }
  }
}

function formatRawContent(value: any) {
  if (value === undefined || value === null) return ''
  if (typeof value !== 'string') return prettyJson(value)
  const result = formatJsonText(value)
  return result.ok ? result.value : value
}

function formatImportRawJson() {
  const result = formatJsonText(form.raw_content)
  if (!result.ok) {
    toast.warning(result.message)
    return
  }
  form.raw_content = result.value
  toast.success('JSON 已格式化')
}

function formatEditRawJson() {
  const result = formatJsonText(editForm.raw_content)
  if (!result.ok) {
    toast.warning(result.message)
    return
  }
  editForm.raw_content = result.value
  toast.success('JSON 已格式化')
}

function documentSource(item: any) {
  return String(item?.source_url || item?.raw_content || '').trim()
}

function selectDocument(item: any) {
  selectedId.value = item.id
  viewMode.value = 'endpoints'
}

async function fetchItems() {
  loading.value = true
  try {
    const { data } = await api.get('/documents')
    items.value = data
    if (!selectedId.value && items.value.length) selectedId.value = items.value[0].id
  } catch {
    toast.error('加载文档失败')
  } finally {
    loading.value = false
  }
}

async function submitImport() {
  const payload: Record<string, any> = {
    name: form.name.trim() || 'OpenAPI 文档',
    format: form.format,
  }
  if (importMode.value === 'url') {
    if (!form.url.trim()) {
      toast.warning('请输入文档 URL')
      return
    }
    payload.url = form.url.trim()
  } else {
    if (!form.raw_content.trim()) {
      toast.warning('请粘贴 OpenAPI / Postman 内容')
      return
    }
    payload.raw_content = form.raw_content
  }

  importing.value = true
  try {
    const { data } = await api.post('/documents/import', payload)
    selectedId.value = data.id
    form.raw_content = ''
    toast.success('文档导入成功')
    await fetchItems()
  } catch (e: any) {
    toast.error('导入失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    importing.value = false
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
    const { data } = await api.post('/documents/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    selectedId.value = data.id
    toast.success('文件上传成功')
    await fetchItems()
  } catch (e: any) {
    toast.error('上传失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    uploading.value = false
  }
}

function startEdit(item: any) {
  editForm.id = item.id
  editForm.name = item.name || ''
  editForm.source_url = item.source_url || ''
  editForm.raw_content = formatRawContent(item.raw_content)
  editForm.format = item.format || 'openapi'
  editing.value = true
}

async function saveEdit() {
  if (!editForm.name.trim()) {
    toast.warning('名称不能为空')
    return
  }
  if (!editForm.raw_content.trim()) {
    toast.warning('原始内容不能为空')
    return
  }
  try {
    const { data } = await api.put(`/documents/${editForm.id}`, {
      name: editForm.name.trim(),
      source_url: editForm.source_url.trim(),
      raw_content: editForm.raw_content,
      format: editForm.format,
    })
    selectedId.value = data.id
    editing.value = false
    toast.success('文档已更新并重新解析端点')
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

async function executeDelete() {
  const deletedId = deleteDialog.id
  try {
    await api.delete(`/documents/${deletedId}`)
    toast.success('文档已删除')
    deleteDialog.show = false
    deleteDialog.id = ''
    deleteDialog.name = ''
    if (selectedId.value === deletedId) selectedId.value = ''
    await fetchItems()
  } catch (e: any) {
    toast.error('删除失败: ' + (e.response?.data?.detail || e.message))
  }
}

function startApiRun(item: any) {
  const source = documentSource(item)
  if (!source) {
    toast.warning('文档没有可交给运行页的 source')
    return
  }
  router.push({
    path: '/run',
    query: {
      document_id: item.id,
      test_type: 'api',
      objective: `基于接口文档「${item.name || 'OpenAPI 文档'}」执行 API 契约、鉴权、参数边界和错误分支检查。`,
      api_execution_policy: 'safe_read_only',
    },
  })
}

function authEndpointCount(item: any) {
  const endpoints = Array.isArray(item?.parsed_endpoints) ? item.parsed_endpoints : []
  return endpoints.filter((endpoint: any) => endpoint?.auth_required).length
}

function useDocumentForPlan(item: any) {
  const endpoints = Array.isArray(item?.parsed_endpoints) ? item.parsed_endpoints : []
  const context = compactLines([
    '从 TestClaw 接口文档资产创建新测试计划。',
    `文档：${item.name || 'OpenAPI 文档'}`,
    `格式：${item.format || 'openapi'}`,
    `端点数：${endpoints.length}`,
    `需要鉴权端点：${authEndpointCount(item)}`,
    item.source_url ? `Source URL：${item.source_url}` : 'Source：已保存原文，不在 URL 中传递原文内容。',
    '建议范围：API 契约、鉴权链路、参数边界、错误分支。',
    '安全边界：默认只读；不要复用凭证、Token、Cookie、会话或验证码值。',
  ])
  router.push({
    path: '/agent-plan',
    query: {
      from: 'asset',
      asset_type: 'document',
      asset_id: item.id,
      title: item.name || 'OpenAPI 文档',
      context: redactSensitiveText(context),
    },
  })
}

function previousEndpointPage() {
  endpointPage.value = Math.max(1, endpointPage.value - 1)
}

function nextEndpointPage() {
  endpointPage.value = Math.min(endpointTotalPages.value, endpointPage.value + 1)
}

onMounted(fetchItems)
</script>

<template>
  <div class="mx-auto max-w-7xl space-y-5 pb-10">
    <div class="flex flex-col gap-3 border-b border-gray-200/80 pb-5 lg:flex-row lg:items-end lg:justify-between">
      <div>
        <div class="tc-page-kicker">API Assets</div>
        <h2 class="text-xl font-semibold tracking-tight text-gray-950">接口文档</h2>
        <p class="mt-1 max-w-3xl text-sm leading-6 text-gray-500">
          导入 OpenAPI / Postman 文档后，可以在线修订原文、按端点检索浏览，并把已保存文档交给 Testing Agent 运行。
        </p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <button
          type="button"
          @click="form.url = REAL_DOCS_URL; form.name = 'wms_接口文档'; importMode = 'url'"
          class="inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-bold text-gray-600 transition-all hover:border-gray-300 hover:bg-gray-50 hover:text-gray-950"
        >
          <Link2 :size="14" /> 使用验证 URL
        </button>
        <button
          v-if="selectedDoc"
          type="button"
          @click="startApiRun(selectedDoc)"
          class="inline-flex items-center gap-2 rounded-lg bg-gray-950 px-4 py-2 text-xs font-bold text-white transition-all hover:bg-gray-800"
        >
          <Play :size="14" /> 用此文档运行
        </button>
        <button
          v-if="selectedDoc"
          type="button"
          @click="useDocumentForPlan(selectedDoc)"
          class="inline-flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-4 py-2 text-xs font-bold text-blue-700 transition-all hover:bg-blue-100"
        >
          <Sparkles :size="14" /> 用于新计划
        </button>
      </div>
    </div>

    <div class="grid gap-4 lg:grid-cols-[340px_minmax(0,1fr)]">
      <aside class="space-y-4 lg:sticky lg:top-4 lg:max-h-[calc(100vh-9rem)] lg:overflow-y-auto lg:pr-1">
        <section class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <div class="mb-4 flex items-center justify-between">
            <h3 class="text-sm font-bold text-gray-900">导入文档</h3>
            <div class="flex rounded-lg border border-gray-200 bg-gray-50 p-0.5">
              <button
                type="button"
                @click="importMode = 'url'"
                class="rounded-md px-3 py-1.5 text-xs font-bold transition-all"
                :class="importMode === 'url' ? 'bg-gray-950 text-white shadow-sm' : 'text-gray-500 hover:text-gray-800'"
              >
                URL
              </button>
              <button
                type="button"
                @click="importMode = 'raw'"
                class="rounded-md px-3 py-1.5 text-xs font-bold transition-all"
                :class="importMode === 'raw' ? 'bg-gray-950 text-white shadow-sm' : 'text-gray-500 hover:text-gray-800'"
              >
                粘贴原文
              </button>
            </div>
          </div>

          <form class="space-y-3" @submit.prevent="submitImport">
            <div>
              <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">名称</label>
              <input
                v-model="form.name"
                class="w-full rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm outline-none transition-all focus:border-blue-500 focus:bg-white"
                placeholder="OpenAPI 文档"
              />
            </div>
            <div>
              <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">格式</label>
              <StyledSelect
                v-model="form.format"
              >
                <option value="openapi">openapi</option>
                <option value="yaml">yaml</option>
                <option value="postman">postman</option>
              </StyledSelect>
            </div>
            <div v-if="importMode === 'url'">
              <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">文档 URL</label>
              <input
                v-model="form.url"
                class="w-full rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 font-mono text-xs outline-none transition-all focus:border-blue-500 focus:bg-white"
                placeholder="http://60.204.225.104/api/v3/api-docs"
              />
            </div>
            <div v-else>
              <div class="mb-1.5 flex items-center justify-between gap-2">
                <label class="block text-[10px] font-bold uppercase tracking-widest text-gray-400">原始内容</label>
                <button
                  type="button"
                  @click="formatImportRawJson"
                  class="inline-flex items-center gap-1.5 rounded-md border border-gray-200 bg-white px-2 py-1 text-[10px] font-bold text-gray-500 transition-all hover:border-blue-200 hover:text-blue-700"
                >
                  <FileCode :size="12" /> 格式化 JSON
                </button>
              </div>
              <textarea
                v-model="form.raw_content"
                rows="10"
                class="max-h-[420px] min-h-48 w-full resize-y overflow-auto rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 font-mono text-xs leading-5 outline-none transition-all focus:border-blue-500 focus:bg-white"
                placeholder="粘贴 OpenAPI / Postman JSON 或 YAML"
              />
            </div>
            <button
              type="submit"
              :disabled="importing"
              class="flex w-full items-center justify-center gap-2 rounded-lg bg-gray-950 px-4 py-2.5 text-sm font-bold text-white transition-colors hover:bg-gray-800 disabled:opacity-50"
            >
              <Link2 v-if="importMode === 'url'" :size="16" />
              <FileCode v-else :size="16" />
              {{ importing ? '导入中...' : '导入文档' }}
            </button>
          </form>
        </section>

        <section
          role="button"
          tabindex="0"
          aria-label="上传接口文档文件"
          @dragover.prevent="dragOver = true"
          @dragleave="dragOver = false"
          @drop.prevent="handleDrop"
          @click="fileInput?.click()"
          @keydown.enter.prevent="fileInput?.click()"
          @keydown.space.prevent="fileInput?.click()"
          class="cursor-pointer rounded-lg border-2 border-dashed p-5 text-center transition-all"
          :class="dragOver ? 'border-blue-400 bg-blue-50' : 'border-gray-300 bg-white hover:border-blue-300'"
        >
          <Upload :size="24" class="mx-auto mb-2" :class="dragOver ? 'text-blue-500' : 'text-gray-400'" />
          <p class="text-sm font-bold" :class="dragOver ? 'text-blue-700' : 'text-gray-600'">
            {{ uploading ? '上传中...' : '拖拽或点击上传文件' }}
          </p>
          <p class="mt-1 text-xs text-gray-400">支持 .json, .yaml, .yml</p>
          <input ref="fileInput" type="file" accept=".json,.yaml,.yml" class="hidden" @change="handleFileSelect" />
        </section>

        <section class="rounded-lg border border-gray-200 bg-white shadow-sm">
          <div class="border-b border-gray-100 px-4 py-3">
            <h3 class="text-sm font-bold text-gray-900">已导入文档</h3>
          </div>
          <LoadingSpinner v-if="loading" text="加载文档中..." />
          <EmptyState
            v-else-if="!items.length"
            :icon="FileCode"
            title="暂无文档"
            description="导入 URL、粘贴原文或上传文件后开始浏览端点"
          />
          <div v-else class="max-h-[560px] space-y-3 overflow-y-auto p-3">
            <DocumentAssetCard
              v-for="item in items"
              :key="item.id"
              :item="item"
              :selected="selectedDoc?.id === item.id"
              @view="selectDocument(item)"
              @run="startApiRun(item)"
              @plan="useDocumentForPlan(item)"
              @edit="startEdit(item)"
              @delete="confirmDelete(item)"
            />
          </div>
        </section>
      </aside>

      <main class="min-w-0 overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm lg:flex lg:max-h-[calc(100vh-9rem)] lg:flex-col">
        <LoadingSpinner v-if="loading" text="加载文档中..." />
        <EmptyState
          v-else-if="!selectedDoc"
          :icon="FileCode"
          title="选择或导入接口文档"
          description="端点浏览、在线编辑和运行交接会显示在这里"
        />
        <template v-else>
          <div class="border-b border-gray-100 px-5 py-4">
            <div class="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <h3 class="truncate text-lg font-bold text-gray-900">{{ selectedDoc.name || `Document-${selectedDoc.format}` }}</h3>
                  <span class="rounded border border-gray-200 bg-gray-50 px-2 py-0.5 text-[10px] font-bold uppercase text-gray-500">
                    {{ selectedDoc.format }}
                  </span>
                </div>
                <div class="mt-1 truncate font-mono text-xs text-gray-400">
                  {{ selectedDoc.source_url || 'manual content' }}
                </div>
                <div class="mt-2 text-xs text-gray-500">
                  {{ selectedEndpoints.length }} 个端点，运行页会直接选中此文档。
                </div>
              </div>
              <div class="flex shrink-0 flex-wrap items-center gap-2">
                <button
                  type="button"
                  @click="startEdit(selectedDoc)"
                  class="inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-bold text-gray-600 transition-all hover:border-blue-200 hover:text-blue-700"
                >
                  <Pencil :size="14" /> 编辑原文
                </button>
                <button
                  type="button"
                  @click="startApiRun(selectedDoc)"
                  class="inline-flex items-center gap-2 rounded-lg bg-gray-950 px-3 py-2 text-xs font-bold text-white transition-all hover:bg-gray-800"
                >
                  <Play :size="14" /> 去运行
                </button>
                <button
                  type="button"
                  @click="useDocumentForPlan(selectedDoc)"
                  class="inline-flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs font-bold text-blue-700 transition-all hover:bg-blue-100"
                >
                  <Sparkles :size="14" /> 新计划
                </button>
                <button
                  type="button"
                  @click="confirmDelete(selectedDoc)"
                  class="rounded-lg p-2 text-gray-400 transition-all hover:bg-red-50 hover:text-red-600"
                  aria-label="删除当前文档"
                  title="删除文档"
                >
                  <Trash2 :size="15" />
                </button>
              </div>
            </div>
          </div>

          <div class="border-b border-gray-100 px-5 py-3">
            <div class="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
              <div class="flex rounded-lg border border-gray-200 bg-gray-50 p-0.5">
                <button
                  type="button"
                  @click="viewMode = 'endpoints'"
                  class="rounded-md px-3 py-1.5 text-xs font-bold transition-all"
                :class="viewMode === 'endpoints' ? 'bg-gray-950 text-white shadow-sm' : 'text-gray-500 hover:text-gray-800'"
                >
                  端点
                </button>
                <button
                  type="button"
                  @click="viewMode = 'raw'"
                  class="rounded-md px-3 py-1.5 text-xs font-bold transition-all"
                :class="viewMode === 'raw' ? 'bg-gray-950 text-white shadow-sm' : 'text-gray-500 hover:text-gray-800'"
                >
                  查看原文
                </button>
              </div>

              <div v-if="viewMode === 'endpoints'" class="flex flex-1 flex-wrap items-center justify-end gap-2">
                <div class="relative min-w-[220px] flex-1 xl:max-w-sm">
                  <Search :size="15" class="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                  <input
                    v-model="endpointSearch"
                    class="w-full rounded-lg border border-gray-200 bg-gray-50 py-2 pl-9 pr-8 text-sm outline-none transition-all focus:border-blue-500 focus:bg-white"
                    placeholder="搜索 method / path / summary"
                  />
                  <button
                    v-if="endpointSearch"
                    type="button"
                    aria-label="清空端点搜索"
                    title="清空端点搜索"
                    @click="endpointSearch = ''"
                    class="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-gray-400 hover:bg-gray-200"
                  >
                    <X :size="13" />
                  </button>
                </div>
                <StyledSelect
                  v-model="methodFilter"
                  class="w-full sm:w-40"
                >
                  <option value="">全部方法</option>
                  <option v-for="method in methodOptions" :key="method" :value="method">{{ method }}</option>
                </StyledSelect>
              </div>
            </div>
          </div>

          <div v-if="viewMode === 'raw'" class="min-h-0 p-5 lg:flex-1">
            <div class="overflow-hidden rounded-lg border border-gray-200 bg-gray-950">
              <div class="flex items-center justify-between border-b border-white/10 px-4 py-2 text-[10px] font-bold uppercase tracking-widest text-gray-400">
                <span>格式化 JSON</span>
                <span>{{ selectedRawLineCount }} 行</span>
              </div>
              <pre class="max-h-[640px] min-h-[320px] overflow-auto whitespace-pre p-4 font-mono text-xs leading-5 text-gray-100">{{ selectedRawContent }}</pre>
            </div>
          </div>

          <div v-else class="grid min-h-[520px] gap-0 lg:min-h-0 lg:flex-1 xl:grid-cols-[minmax(0,1fr)_340px]">
            <section class="min-w-0 border-r border-gray-100">
              <div class="flex items-center justify-between border-b border-gray-100 px-4 py-3">
                <span class="text-xs font-bold text-gray-500">端点 {{ endpointRangeLabel }}</span>
                <div class="flex items-center gap-1">
                  <button
                    type="button"
                    aria-label="上一页端点"
                    @click="previousEndpointPage"
                    :disabled="endpointPage <= 1"
                    class="rounded-lg p-1.5 text-gray-500 transition-all hover:bg-gray-100 disabled:opacity-30"
                  >
                    <ChevronLeft :size="16" />
                  </button>
                  <span class="px-2 text-xs font-mono text-gray-400">{{ endpointPage }} / {{ endpointTotalPages }}</span>
                  <button
                    type="button"
                    aria-label="下一页端点"
                    @click="nextEndpointPage"
                    :disabled="endpointPage >= endpointTotalPages"
                    class="rounded-lg p-1.5 text-gray-500 transition-all hover:bg-gray-100 disabled:opacity-30"
                  >
                    <ChevronRight :size="16" />
                  </button>
                </div>
              </div>

              <div v-if="!filteredEndpoints.length" class="px-4 py-16 text-center text-sm text-gray-400">
                没有匹配的端点
              </div>
              <div v-else class="max-h-[520px] overflow-y-auto divide-y divide-gray-100 lg:max-h-full">
                <button
                  v-for="endpoint in pagedEndpoints"
                  :key="`${endpoint.method}-${endpointPath(endpoint)}-${endpoint.operationId || ''}`"
                  type="button"
                  @click="selectedEndpoint = endpoint"
                  class="block w-full px-4 py-3 text-left transition-all hover:bg-gray-50"
                  :class="selectedEndpoint === endpoint ? 'bg-blue-50' : 'bg-white'"
                >
                  <div class="flex items-start gap-3">
                    <span
                      class="mt-0.5 min-w-14 rounded border px-2 py-1 text-center text-[10px] font-bold"
                      :class="getMethodColor(endpoint.method)"
                    >
                      {{ String(endpoint.method || 'GET').toUpperCase() }}
                    </span>
                    <div class="min-w-0 flex-1">
                      <div class="truncate font-mono text-sm text-gray-900">{{ endpointPath(endpoint) }}</div>
                      <div class="mt-1 truncate text-xs text-gray-500">{{ endpointTitle(endpoint) }}</div>
                      <div v-if="endpoint.auth_required || endpoint.request_body_schema" class="mt-2 flex flex-wrap gap-1.5">
                        <span v-if="endpoint.auth_required" class="rounded bg-amber-50 px-2 py-0.5 text-[10px] font-bold text-amber-700">Auth</span>
                        <span v-if="endpoint.request_body_schema" class="rounded bg-gray-100 px-2 py-0.5 text-[10px] font-bold text-gray-600">Body</span>
                      </div>
                    </div>
                  </div>
                </button>
              </div>
            </section>

            <aside class="min-w-0 bg-gray-50/60">
              <div class="border-b border-gray-100 bg-white px-4 py-3">
                <h4 class="text-xs font-bold uppercase tracking-widest text-gray-400">端点详情</h4>
              </div>
              <div v-if="!selectedEndpoint" class="px-5 py-16 text-center text-sm text-gray-400">
                选择左侧端点查看参数、请求体和响应结构
              </div>
              <div v-else class="max-h-[520px] overflow-y-auto p-4 lg:max-h-full">
                <div class="rounded-lg border border-gray-200 bg-white p-4">
                  <div class="flex items-start gap-2">
                    <span class="rounded border px-2 py-1 text-[10px] font-bold" :class="getMethodColor(selectedEndpoint.method)">
                      {{ String(selectedEndpoint.method || 'GET').toUpperCase() }}
                    </span>
                    <div class="min-w-0 flex-1">
                      <div class="break-all font-mono text-sm font-bold text-gray-900">{{ endpointPath(selectedEndpoint) }}</div>
                      <p class="mt-2 text-xs leading-5 text-gray-500">{{ endpointTitle(selectedEndpoint) }}</p>
                    </div>
                  </div>

                  <div class="mt-4 space-y-4">
                    <div>
                      <div class="mb-1 text-[10px] font-bold uppercase tracking-widest text-gray-400">Tags</div>
                      <div class="flex flex-wrap gap-1.5">
                        <span
                          v-for="tag in selectedEndpoint.tags || []"
                          :key="tag"
                          class="rounded bg-gray-100 px-2 py-0.5 text-[10px] font-bold text-gray-600"
                        >
                          {{ tag }}
                        </span>
                        <span v-if="!(selectedEndpoint.tags || []).length" class="text-xs text-gray-400">无</span>
                      </div>
                    </div>

                    <div>
                      <div class="mb-1 text-[10px] font-bold uppercase tracking-widest text-gray-400">Parameters</div>
                      <pre class="max-h-44 overflow-auto rounded border border-gray-200 bg-gray-950 p-3 text-[11px] leading-5 text-gray-100">{{ prettyJson({
                        path: selectedEndpoint.path_params,
                        query: selectedEndpoint.query_params,
                        header: selectedEndpoint.header_params,
                      }) }}</pre>
                    </div>

                    <div>
                      <div class="mb-1 text-[10px] font-bold uppercase tracking-widest text-gray-400">Request Body</div>
                      <pre class="max-h-44 overflow-auto rounded border border-gray-200 bg-gray-950 p-3 text-[11px] leading-5 text-gray-100">{{ prettyJson(selectedEndpoint.request_body_schema || selectedEndpoint.example_request) }}</pre>
                    </div>

                    <div>
                      <div class="mb-1 text-[10px] font-bold uppercase tracking-widest text-gray-400">Response</div>
                      <pre class="max-h-44 overflow-auto rounded border border-gray-200 bg-gray-950 p-3 text-[11px] leading-5 text-gray-100">{{ prettyJson(selectedEndpoint.response_schema || selectedEndpoint.example_response) }}</pre>
                    </div>
                  </div>
                </div>
              </div>
            </aside>
          </div>
        </template>
      </main>
    </div>

    <div v-if="editing" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div class="max-h-[90vh] w-full max-w-5xl overflow-hidden rounded-lg border border-gray-200 bg-white shadow-xl">
        <div class="flex items-center justify-between border-b border-gray-100 px-5 py-4">
          <div>
            <h3 class="text-sm font-bold text-gray-900">在线编辑接口文档</h3>
            <p class="mt-1 text-xs text-gray-500">保存后后端会重新解析端点；运行页会直接选择这个已保存文档。</p>
          </div>
          <button
            type="button"
            aria-label="关闭文档编辑"
            title="关闭文档编辑"
            @click="editing = false"
            class="rounded-lg p-2 text-gray-400 transition-all hover:bg-gray-100 hover:text-gray-700"
          >
            <X :size="18" />
          </button>
        </div>
        <div class="max-h-[calc(90vh-130px)] overflow-y-auto p-5">
          <div class="grid gap-4 md:grid-cols-[minmax(0,1fr)_160px]">
            <div>
              <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">名称</label>
              <input
                v-model="editForm.name"
                class="w-full rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm outline-none transition-all focus:border-blue-500 focus:bg-white"
              />
            </div>
            <div>
              <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">格式</label>
                <StyledSelect
                  v-model="editForm.format"
                >
                  <option value="openapi">openapi</option>
                  <option value="yaml">yaml</option>
                  <option value="postman">postman</option>
                </StyledSelect>
            </div>
          </div>
          <div class="mt-4">
            <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">Source URL</label>
            <input
              v-model="editForm.source_url"
              class="w-full rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 font-mono text-xs outline-none transition-all focus:border-blue-500 focus:bg-white"
              placeholder="可选；执行时优先使用此 URL 拉取文档"
            />
          </div>
          <div class="mt-4">
            <div class="mb-1.5 flex items-center justify-between gap-2">
              <label class="block text-[10px] font-bold uppercase tracking-widest text-gray-400">原始内容</label>
              <button
                type="button"
                @click="formatEditRawJson"
                class="inline-flex items-center gap-1.5 rounded-md border border-gray-200 bg-white px-2 py-1 text-[10px] font-bold text-gray-500 transition-all hover:border-blue-200 hover:text-blue-700"
              >
                <FileCode :size="12" /> 格式化 JSON
              </button>
            </div>
            <textarea
              v-model="editForm.raw_content"
              rows="22"
              class="max-h-[52vh] min-h-[360px] w-full resize-y overflow-auto rounded-lg border border-gray-200 bg-gray-950 px-4 py-3 font-mono text-xs leading-5 text-gray-100 outline-none transition-all focus:border-blue-500"
            />
          </div>
        </div>
        <div class="flex justify-end gap-2 border-t border-gray-100 px-5 py-4">
          <button
            type="button"
            @click="editing = false"
            class="rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-bold text-gray-600 transition-all hover:bg-gray-50"
          >
            取消
          </button>
          <button
            type="button"
            @click="saveEdit"
            class="inline-flex items-center gap-2 rounded-lg bg-gray-950 px-4 py-2 text-sm font-bold text-white transition-all hover:bg-gray-800"
          >
            <Check :size="15" /> 保存并重新解析
          </button>
        </div>
      </div>
    </div>

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
