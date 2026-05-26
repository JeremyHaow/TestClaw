<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import api from '../lib/api'
import { useToast } from '../composables/useToast'
import SearchInput from '../components/SearchInput.vue'
import Pagination from '../components/Pagination.vue'
import EmptyState from '../components/EmptyState.vue'
import LoadingSpinner from '../components/LoadingSpinner.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import StyledSelect from '../components/StyledSelect.vue'
import {
  Check,
  CheckSquare,
  FileCode,
  MinusSquare,
  Pencil,
  Play,
  Plus,
  Square,
  Trash2,
  X,
} from 'lucide-vue-next'
import TestCaseAssetCard from '../components/assets/TestCaseAssetCard.vue'
import { compactLines, redactSensitiveText } from '../lib/assetHandoff'

const toast = useToast()
const router = useRouter()

const items = ref<any[]>([])
const suites = ref<any[]>([])
const loading = ref(false)
const search = ref('')
const page = ref(1)
const pageSize = ref(15)
const total = ref(0)
const selectedIds = ref<Set<string>>(new Set())
const editingId = ref<string | null>(null)
const detailItem = ref<any | null>(null)
const editForm = reactive({ title: '', stepsText: '', expectedText: '', priority: 'P1', category: 'FUNCTIONAL' })
const confirmDelete = ref<string | null>(null)
const confirmBulkDelete = ref(false)
const filterPriority = ref('')
const filterCategory = ref('')
const showCreate = ref(false)
const createForm = reactive({
  title: '',
  category: 'FUNCTIONAL',
  priority: 'P1',
  steps: [''],
  expected: [''],
})
const creating = ref(false)
const suiteRunning = ref(false)

const CATEGORY_LABELS: Record<string, string> = {
  FUNCTIONAL: '功能',
  UI: '界面',
  API: '接口',
  PERFORMANCE: '性能',
  SECURITY: '安全',
}

const CASE_TYPE_LABELS: Record<string, string> = {
  api: '接口',
  ui: '界面',
  case: '用例',
}

const allSelected = computed(() => items.value.length > 0 && items.value.every((item) => selectedIds.value.has(item.id)))
const someSelected = computed(() => items.value.some((item) => selectedIds.value.has(item.id)) && !allSelected.value)
const selectAllLabel = computed(() => allSelected.value ? '取消选择当前页全部用例' : '选择当前页全部用例')
const suiteMap = computed<Record<string, string[]>>(() => {
  const mapped: Record<string, string[]> = {}
  for (const suite of suites.value) {
    for (const id of suite.test_case_ids || []) {
      if (!mapped[id]) mapped[id] = []
      mapped[id].push(suite.name)
    }
  }
  return mapped
})

async function fetchSuites() {
  try {
    const { data } = await api.get('/test-cases/suites')
    suites.value = Array.isArray(data) ? data : []
  } catch {
    suites.value = []
  }
}

async function fetchItems() {
  loading.value = true
  try {
    const params: Record<string, any> = { page: page.value, page_size: pageSize.value }
    if (search.value) params.search = search.value
    if (filterPriority.value) params.priority = filterPriority.value
    if (filterCategory.value) params.category = filterCategory.value
    const { data, headers } = await api.get('/test-cases', { params })
    items.value = Array.isArray(data) ? data : data.items || []
    total.value = headers['x-total-count'] ? Number(headers['x-total-count']) : (data.total ?? items.value.length)
  } catch {
    toast.error('加载用例失败')
  } finally {
    loading.value = false
  }
}

async function updateCase(id: string, data: Record<string, any>) {
  try {
    await api.put(`/test-cases/${id}`, data)
    toast.success('用例已更新')
    editingId.value = null
    detailItem.value = null
    await fetchItems()
  } catch {
    toast.error('更新失败')
  }
}

async function remove(id: string) {
  try {
    await api.delete(`/test-cases/${id}`)
    selectedIds.value.delete(id)
    if (detailItem.value?.id === id) detailItem.value = null
    toast.success('用例已删除')
    await fetchItems()
    await fetchSuites()
  } catch {
    toast.error('删除失败')
  }
}

async function bulkDelete() {
  const ids = [...selectedIds.value]
  try {
    await Promise.all(ids.map((id) => api.delete(`/test-cases/${id}`)))
    selectedIds.value.clear()
    if (detailItem.value && ids.includes(detailItem.value.id)) detailItem.value = null
    toast.success(`已删除 ${ids.length} 条用例`)
    await fetchItems()
    await fetchSuites()
  } catch {
    toast.error('批量删除失败')
  }
  confirmBulkDelete.value = false
}

async function runSelectedSuite() {
  const ids = [...selectedIds.value]
  if (!ids.length) {
    toast.warning('请先选择要复用的测试用例')
    return
  }
  suiteRunning.value = true
  try {
    const suiteName = `选中用例套件 ${new Date().toLocaleString('zh-CN')}`
    const { data: suite } = await api.post('/test-cases/suites', {
      name: suiteName,
      test_case_ids: ids,
    })
    const { data } = await api.post(`/test-cases/suites/${suite.id}/run`)
    selectedIds.value = new Set()
    toast.success(`已提交 ${data.total || ids.length} 条用例执行`)
    router.push(`/runs/${data.task_id}`)
  } catch (e: any) {
    toast.error(e?.response?.data?.detail || '执行套件失败')
  } finally {
    suiteRunning.value = false
  }
}

function toggleSelect(id: string) {
  const selected = new Set(selectedIds.value)
  if (selected.has(id)) selected.delete(id)
  else selected.add(id)
  selectedIds.value = selected
}

function toggleSelectAll() {
  selectedIds.value = allSelected.value ? new Set() : new Set(items.value.map((item) => item.id))
}

function rowSelectLabel(item: any) {
  return selectedIds.value.has(item.id) ? `取消选择用例：${item.title}` : `选择用例：${item.title}`
}

function arrayText(value: any): string {
  if (Array.isArray(value)) return value.map(formatStep).join('\n')
  if (typeof value === 'string') return value
  if (value == null) return ''
  return JSON.stringify(value, null, 2)
}

function parseListText(value: string): any[] {
  const trimmed = value.trim()
  if (!trimmed) return []
  try {
    const parsed = JSON.parse(trimmed)
    return Array.isArray(parsed) ? parsed : [parsed]
  } catch {
    return trimmed.split('\n').map((line) => line.trim()).filter(Boolean)
  }
}

function openDetail(item: any) {
  detailItem.value = item
  editingId.value = null
}

function startEdit(item: any) {
  detailItem.value = item
  editingId.value = item.id
  editForm.title = item.title
  editForm.stepsText = JSON.stringify(item.steps || [], null, 2)
  editForm.expectedText = JSON.stringify(item.expected || [], null, 2)
  editForm.priority = item.priority || 'P1'
  editForm.category = item.category || 'FUNCTIONAL'
}

function cancelEdit() {
  editingId.value = null
}

function saveEdit(id: string) {
  updateCase(id, {
    title: editForm.title,
    steps: parseListText(editForm.stepsText),
    expected: parseListText(editForm.expectedText),
    priority: editForm.priority,
    category: editForm.category,
    test_data: detailItem.value?.test_data || null,
    source: detailItem.value?.source || null,
  })
}

function openCreate() {
  createForm.title = ''
  createForm.category = 'FUNCTIONAL'
  createForm.priority = 'P1'
  createForm.steps = ['']
  createForm.expected = ['']
  showCreate.value = true
}

function addStep() { createForm.steps.push('') }
function removeStep(i: number) { createForm.steps.splice(i, 1) }
function addExpected() { createForm.expected.push('') }
function removeExpected(i: number) { createForm.expected.splice(i, 1) }

async function submitCreate() {
  if (!createForm.title.trim()) { toast.warning('标题不能为空'); return }
  const steps = createForm.steps.map((step) => step.trim()).filter(Boolean)
  const expected = createForm.expected.map((item) => item.trim()).filter(Boolean)
  if (!steps.length) { toast.warning('至少需要一个测试步骤'); return }
  creating.value = true
  try {
    await api.post('/test-cases', {
      title: createForm.title.trim(),
      category: createForm.category,
      priority: createForm.priority,
      steps,
      expected,
    })
    toast.success('用例创建成功')
    showCreate.value = false
    await fetchItems()
  } catch (e: any) {
    toast.error('创建失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    creating.value = false
  }
}

function formatStep(value: any): string {
  return typeof value === 'string' ? value : JSON.stringify(value)
}

function caseAsset(item: any) {
  return item.test_data?.case_asset || {}
}

function caseType(item: any) {
  const assetType = String(caseAsset(item).case_type || '').toLowerCase()
  if (assetType) return assetType
  const category = String(item.category || '').toLowerCase()
  if (category.includes('api') || item.test_data?.request_template) return 'api'
  if (category.includes('ui') || category.includes('page') || item.test_data?.playwright_commands) return 'ui'
  return 'case'
}

function categoryLabel(value: any) {
  const key = String(value || '').toUpperCase()
  return CATEGORY_LABELS[key] || String(value || '未分类')
}

function caseTypeLabel(item: any) {
  const type = caseType(item)
  return CASE_TYPE_LABELS[type] || type.toUpperCase()
}

function sourceKind(item: any) {
  const source = String(item.source || '')
  if (caseAsset(item).source_run_id || source.startsWith('run_case_asset:')) return '运行沉淀'
  if (source.startsWith('agent:')) return 'Agent 生成'
  if (source) return source
  return '手动维护'
}

function sourceRunId(item: any) {
  const asset = caseAsset(item)
  if (asset.source_run_id) return String(asset.source_run_id)
  const source = String(item.source || '')
  if (source.startsWith('run_case_asset:')) return source.split(':')[1] || ''
  return ''
}

function sourceProject(item: any) {
  return item.test_data?.project || item.test_data?.project_id || item.test_data?.target_url || item.test_data?.base_url || ''
}

function sourceSuiteNames(item: any) {
  return suiteMap.value[item.id] || []
}

function sourceDetail(item: any) {
  const asset = caseAsset(item)
  const source = asset.source ? `${asset.source} #${Number(asset.source_index ?? 0) + 1}` : ''
  return source || item.source || 'manual'
}

function previewText(item: any) {
  const steps = Array.isArray(item.steps) ? item.steps : []
  if (!steps.length) return '未记录步骤'
  return steps.slice(0, 2).map(formatStep).join(' / ')
}

function compactCaseList(values: any, limit = 4) {
  const items = Array.isArray(values) ? values : parseListText(arrayText(values))
  return items.slice(0, limit).map((item: any, index: number) => `${index + 1}. ${formatStep(item)}`).join('；')
}

function useCaseForPlan(item: any) {
  const runId = sourceRunId(item)
  const context = compactLines([
    '从 TestClaw 用例资产创建新测试计划。',
    `用例：${item.title}`,
    `类型：${caseTypeLabel(item)}`,
    `优先级：${item.priority || 'P1'}`,
    `来源：${sourceKind(item)}`,
    runId ? `来源运行：${runId}` : '',
    sourceProject(item) ? `目标/项目：${sourceProject(item)}` : '',
    sourceSuiteNames(item).length ? `所属套件：${sourceSuiteNames(item).join(', ')}` : '',
    `步骤：${compactCaseList(item.steps) || '未记录'}`,
    `预期：${compactCaseList(item.expected) || '未记录'}`,
    '请把该用例加入新计划，可扩展相邻场景；默认保持只读安全边界。',
  ])
  router.push({
    path: '/agent-plan',
    query: {
      from: 'asset',
      asset_type: 'test-case',
      asset_id: item.id,
      title: item.title,
      context: redactSensitiveText(context),
    },
  })
}

watch([search, filterPriority, filterCategory], () => {
  page.value = 1
  fetchItems()
})

watch(page, () => {
  fetchItems()
})

onMounted(async () => {
  await Promise.all([fetchItems(), fetchSuites()])
})
</script>

<template>
  <div class="mx-auto max-w-7xl space-y-5 pb-10">
    <div class="flex flex-wrap items-start justify-between gap-3 border-b border-gray-200/80 pb-5">
      <div class="flex flex-col gap-1">
        <div class="tc-page-kicker">套件</div>
        <h2 class="text-xl font-semibold tracking-tight text-gray-950">用例资产</h2>
        <p class="max-w-3xl text-sm text-gray-500">按来源、运行、套件和分类管理可复用用例；长步骤和预期结果在详情面板中查看和编辑。</p>
      </div>
      <div class="flex items-center gap-3">
        <span class="font-mono text-xs text-gray-400">{{ total }} 条</span>
        <button @click="openCreate"
          class="flex items-center gap-2 rounded-lg bg-gray-950 px-4 py-2 text-sm font-bold text-white transition-colors hover:bg-gray-800">
          <Plus :size="16" /> 创建用例
        </button>
      </div>
    </div>

    <div class="rounded-lg border border-gray-200 bg-white p-3 shadow-sm">
      <div class="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
        <div class="w-full min-w-0 sm:min-w-[16rem] sm:flex-1">
          <SearchInput v-model="search" placeholder="搜索标题、分类或来源..." />
        </div>
        <StyledSelect v-model="filterPriority" class="w-full sm:w-40 sm:flex-none" size="sm">
          <option value="">全部优先级</option>
          <option value="P0">P0</option>
          <option value="P1">P1</option>
          <option value="P2">P2</option>
          <option value="P3">P3</option>
        </StyledSelect>
        <StyledSelect v-model="filterCategory" class="w-full sm:w-48 sm:flex-none" size="sm">
          <option value="">全部分类</option>
          <option value="FUNCTIONAL">功能</option>
          <option value="UI">界面</option>
          <option value="API">接口</option>
          <option value="PERFORMANCE">性能</option>
          <option value="SECURITY">安全</option>
        </StyledSelect>
        <button v-if="selectedIds.size > 0" @click="runSelectedSuite" :disabled="suiteRunning"
          class="flex w-full items-center justify-center gap-2 rounded-lg bg-gray-950 px-4 py-2 text-xs font-bold text-white shadow-sm transition-all hover:bg-gray-800 disabled:opacity-50 sm:w-auto sm:flex-none">
          <Play :size="14" />
          {{ suiteRunning ? '提交中...' : `运行选中 (${selectedIds.size})` }}
        </button>
        <button v-if="selectedIds.size > 0" @click="confirmBulkDelete = true"
          class="flex w-full items-center justify-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-xs font-bold text-white transition-colors hover:bg-red-700 sm:w-auto sm:flex-none">
          <Trash2 :size="14" />
          删除选中 ({{ selectedIds.size }})
        </button>
      </div>
    </div>

    <div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <LoadingSpinner v-if="loading" text="加载中..." />
      <template v-else-if="items.length === 0">
        <EmptyState :icon="FileCode" title="暂无用例" description="还没有任何测试用例，请稍后再试或调整筛选条件。" />
      </template>
      <template v-else>
        <div class="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 bg-gray-50 px-4 py-3">
          <div class="flex items-center gap-3">
            <button
              type="button"
              :aria-label="selectAllLabel"
              :title="selectAllLabel"
              @click="toggleSelectAll"
              class="inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-bold text-gray-600 transition-colors hover:border-blue-200 hover:text-blue-700"
            >
              <CheckSquare v-if="allSelected" :size="15" />
              <MinusSquare v-else-if="someSelected" :size="15" />
              <Square v-else :size="15" />
              当前页
            </button>
            <span class="text-xs text-gray-500">卡片视图，当前页 {{ items.length }} 条</span>
          </div>
          <span class="text-xs text-gray-500">{{ selectedIds.size ? `已选择 ${selectedIds.size} 条` : '未选择用例' }}</span>
        </div>
        <div class="grid max-h-[calc(100vh-18rem)] gap-3 overflow-auto p-3 xl:grid-cols-2">
          <TestCaseAssetCard
            v-for="item in items"
            :key="item.id"
            :item="item"
            :selected="selectedIds.has(item.id)"
            :suite-names="sourceSuiteNames(item)"
            @select="toggleSelect(item.id)"
            @detail="openDetail(item)"
            @plan="useCaseForPlan(item)"
            @edit="startEdit(item)"
            @delete="confirmDelete = item.id"
          />
        </div>
        <Pagination :page="page" :page-size="pageSize" :total="total" @update:page="page = $event" />
      </template>
    </div>

    <Teleport to="body">
      <aside v-if="detailItem" class="fixed inset-y-0 right-0 z-50 flex w-full max-w-xl flex-col border-l border-gray-200 bg-white shadow-2xl">
        <div class="flex items-start justify-between gap-4 border-b border-gray-100 px-6 py-4">
          <div class="min-w-0">
            <h3 class="truncate text-lg font-bold text-gray-900">{{ editingId === detailItem.id ? '编辑用例' : detailItem.title }}</h3>
            <p class="mt-1 text-xs text-gray-500">{{ sourceKind(detailItem) }} / {{ categoryLabel(detailItem.category) }} / {{ detailItem.priority }}</p>
          </div>
          <button
            type="button"
            aria-label="关闭用例详情"
            title="关闭用例详情"
            @click="detailItem = null; editingId = null"
            class="rounded-lg p-2 text-gray-400 transition-all hover:bg-gray-100 hover:text-gray-700"
          >
            <X :size="18" />
          </button>
        </div>

        <div class="min-h-0 flex-1 overflow-y-auto px-6 py-5">
          <template v-if="editingId === detailItem.id">
            <div class="space-y-4">
              <div>
                <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">标题</label>
                <input v-model="editForm.title" class="w-full rounded-lg border border-gray-200 bg-gray-50 px-4 py-2.5 text-sm outline-none transition-all focus:border-blue-500 focus:bg-white" />
              </div>
              <div class="grid grid-cols-2 gap-3">
                <div>
                  <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">分类</label>
                  <StyledSelect v-model="editForm.category">
                    <option value="FUNCTIONAL">功能</option>
                    <option value="UI">界面</option>
                    <option value="API">接口</option>
                    <option value="PERFORMANCE">性能</option>
                    <option value="SECURITY">安全</option>
                  </StyledSelect>
                </div>
                <div>
                  <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">优先级</label>
                  <StyledSelect v-model="editForm.priority">
                    <option value="P0">P0</option>
                    <option value="P1">P1</option>
                    <option value="P2">P2</option>
                    <option value="P3">P3</option>
                  </StyledSelect>
                </div>
              </div>
              <div>
                <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">步骤 JSON 或逐行文本</label>
                <textarea v-model="editForm.stepsText" rows="10" class="w-full resize-none rounded-lg border border-gray-200 bg-gray-50 px-4 py-2.5 font-mono text-xs outline-none transition-all focus:border-blue-500 focus:bg-white" />
              </div>
              <div>
                <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">预期 JSON 或逐行文本</label>
                <textarea v-model="editForm.expectedText" rows="6" class="w-full resize-none rounded-lg border border-gray-200 bg-gray-50 px-4 py-2.5 font-mono text-xs outline-none transition-all focus:border-blue-500 focus:bg-white" />
              </div>
            </div>
          </template>
          <template v-else>
            <div class="space-y-5">
              <div class="grid gap-3 sm:grid-cols-2">
                <div class="rounded-lg border border-gray-200 bg-gray-50 p-3">
                  <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">来源</div>
                  <div class="mt-1 text-sm font-semibold text-gray-900">{{ sourceKind(detailItem) }}</div>
                  <div class="mt-1 break-all font-mono text-[11px] text-gray-400">{{ sourceDetail(detailItem) }}</div>
                </div>
                <div class="rounded-lg border border-gray-200 bg-gray-50 p-3">
                  <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">运行 / 项目</div>
                  <div class="mt-1 break-all font-mono text-xs text-gray-600">{{ sourceRunId(detailItem) || '无关联运行' }}</div>
                  <div class="mt-1 break-all font-mono text-[11px] text-gray-400">{{ sourceProject(detailItem) || '无项目信息' }}</div>
                </div>
              </div>

              <div>
                <h4 class="mb-2 text-xs font-bold uppercase tracking-widest text-gray-400">步骤</h4>
                <div class="max-h-80 space-y-2 overflow-y-auto rounded-lg border border-gray-200 bg-gray-50 p-3">
                  <div v-for="(step, index) in (detailItem.steps || [])" :key="index" class="grid grid-cols-[28px_minmax(0,1fr)] gap-2 text-sm">
                    <span class="font-mono text-xs text-gray-400">{{ Number(index) + 1 }}</span>
                    <p class="whitespace-pre-wrap break-words text-gray-700">{{ formatStep(step) }}</p>
                  </div>
                  <p v-if="!(detailItem.steps || []).length" class="text-xs text-gray-400">未记录步骤</p>
                </div>
              </div>

              <div>
                <h4 class="mb-2 text-xs font-bold uppercase tracking-widest text-gray-400">预期结果</h4>
                <div class="max-h-60 space-y-2 overflow-y-auto rounded-lg border border-gray-200 bg-gray-50 p-3">
                  <div v-for="(expected, index) in parseListText(arrayText(detailItem.expected))" :key="index" class="grid grid-cols-[28px_minmax(0,1fr)] gap-2 text-sm">
                    <span class="font-mono text-xs text-gray-400">{{ index + 1 }}</span>
                    <p class="whitespace-pre-wrap break-words text-gray-700">{{ formatStep(expected) }}</p>
                  </div>
                  <p v-if="!arrayText(detailItem.expected)" class="text-xs text-gray-400">未记录预期结果</p>
                </div>
              </div>
            </div>
          </template>
        </div>

        <div class="flex justify-end gap-2 border-t border-gray-100 px-6 py-4">
          <template v-if="editingId === detailItem.id">
            <button @click="cancelEdit" class="rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-bold text-gray-600 transition-all hover:bg-gray-50">取消</button>
            <button @click="saveEdit(detailItem.id)" class="flex items-center gap-2 rounded-lg bg-gray-950 px-4 py-2 text-sm font-bold text-white transition-all hover:bg-gray-800">
              <Check :size="15" /> 保存
            </button>
          </template>
          <template v-else>
            <button @click="startEdit(detailItem)" class="flex items-center gap-2 rounded-lg bg-gray-950 px-4 py-2 text-sm font-bold text-white transition-all hover:bg-gray-800">
              <Pencil :size="15" /> 编辑
            </button>
          </template>
        </div>
      </aside>
    </Teleport>

    <ConfirmDialog
      :show="confirmDelete !== null"
      title="删除用例"
      message="确定要删除这条用例吗？此操作不可撤销。"
      confirm-text="删除"
      :danger="true"
      @confirm="() => { if (confirmDelete) remove(confirmDelete); confirmDelete = null }"
      @cancel="confirmDelete = null"
    />

    <ConfirmDialog
      :show="confirmBulkDelete"
      title="批量删除"
      :message="`确定要删除选中的 ${selectedIds.size} 条用例吗？此操作不可撤销。`"
      confirm-text="全部删除"
      :danger="true"
      @confirm="bulkDelete"
      @cancel="confirmBulkDelete = false"
    />

    <Teleport to="body">
      <div v-if="showCreate" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" @click.self="showCreate = false">
        <div class="max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-lg bg-white shadow-2xl">
          <div class="sticky top-0 flex items-center justify-between border-b border-gray-100 bg-white px-6 py-4">
            <h3 class="text-lg font-bold text-gray-900">创建测试用例</h3>
            <button
              type="button"
              aria-label="关闭创建用例弹窗"
              title="关闭创建用例弹窗"
              @click="showCreate = false"
              class="p-1 text-gray-400 transition-colors hover:text-gray-600"
            >
              <X :size="20" />
            </button>
          </div>
          <div class="space-y-4 p-6">
            <div>
              <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">标题 *</label>
              <input v-model="createForm.title" placeholder="用例标题"
                class="w-full rounded-lg border border-gray-200 bg-gray-50 px-4 py-2.5 text-sm outline-none transition-all focus:border-blue-500 focus:bg-white" />
            </div>
            <div class="grid grid-cols-2 gap-4">
              <div>
                <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">分类</label>
                <StyledSelect v-model="createForm.category">
                  <option value="FUNCTIONAL">功能</option>
                  <option value="UI">界面</option>
                  <option value="API">接口</option>
                  <option value="PERFORMANCE">性能</option>
                  <option value="SECURITY">安全</option>
                </StyledSelect>
              </div>
              <div>
                <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">优先级</label>
                <StyledSelect v-model="createForm.priority">
                  <option value="P0">P0 - 阻塞</option>
                  <option value="P1">P1 - 严重</option>
                  <option value="P2">P2 - 一般</option>
                  <option value="P3">P3 - 轻微</option>
                </StyledSelect>
              </div>
            </div>
            <div>
              <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">测试步骤 *</label>
              <div class="space-y-2">
                <div v-for="(step, index) in createForm.steps" :key="index" class="flex items-center gap-2">
                  <span class="w-5 font-mono text-xs text-gray-400">{{ index + 1 }}</span>
                  <input v-model="createForm.steps[index]" :placeholder="`步骤 ${index + 1}`"
                    class="flex-1 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm outline-none transition-all focus:border-blue-500 focus:bg-white" />
                  <button
                    v-if="createForm.steps.length > 1"
                    type="button"
                    :aria-label="`删除步骤 ${index + 1}`"
                    :title="`删除步骤 ${index + 1}`"
                    @click="removeStep(index)"
                    class="p-1 text-gray-400 transition-colors hover:text-red-500"
                  >
                    <X :size="14" />
                  </button>
                </div>
                <button @click="addStep" class="text-xs font-bold text-blue-600 transition-colors hover:text-blue-800">+ 添加步骤</button>
              </div>
            </div>
            <div>
              <label class="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-gray-400">预期结果</label>
              <div class="space-y-2">
                <div v-for="(expected, index) in createForm.expected" :key="index" class="flex items-center gap-2">
                  <span class="w-5 font-mono text-xs text-gray-400">{{ index + 1 }}</span>
                  <input v-model="createForm.expected[index]" :placeholder="`预期结果 ${index + 1}`"
                    class="flex-1 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm outline-none transition-all focus:border-blue-500 focus:bg-white" />
                  <button
                    v-if="createForm.expected.length > 1"
                    type="button"
                    :aria-label="`删除预期结果 ${index + 1}`"
                    :title="`删除预期结果 ${index + 1}`"
                    @click="removeExpected(index)"
                    class="p-1 text-gray-400 transition-colors hover:text-red-500"
                  >
                    <X :size="14" />
                  </button>
                </div>
                <button @click="addExpected" class="text-xs font-bold text-blue-600 transition-colors hover:text-blue-800">+ 添加预期结果</button>
              </div>
            </div>
          </div>
          <div class="sticky bottom-0 flex justify-end gap-3 border-t border-gray-100 bg-white px-6 py-4">
            <button @click="showCreate = false" class="rounded-lg px-4 py-2 text-sm font-bold text-gray-600 transition-all hover:bg-gray-100">取消</button>
            <button @click="submitCreate" :disabled="creating"
              class="rounded-lg bg-gray-950 px-6 py-2 text-sm font-bold text-white transition-colors hover:bg-gray-800 disabled:opacity-50">
              {{ creating ? '创建中...' : '创建' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>
