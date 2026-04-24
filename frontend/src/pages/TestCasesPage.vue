<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import api from '../lib/api'
import { useToast } from '../composables/useToast'
import SearchInput from '../components/SearchInput.vue'
import Pagination from '../components/Pagination.vue'
import EmptyState from '../components/EmptyState.vue'
import LoadingSpinner from '../components/LoadingSpinner.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import { FileCode, Pencil, Trash2, X, Check, Square, CheckSquare, MinusSquare } from 'lucide-vue-next'

const toast = useToast()

// --- State ---
const items = ref<any[]>([])
const loading = ref(false)
const search = ref('')
const page = ref(1)
const pageSize = ref(15)
const total = ref(0)
const selectedIds = ref<Set<string>>(new Set())
const editingId = ref<string | null>(null)
const editForm = reactive({ title: '', stepsText: '', expectedText: '', priority: 'P1', category: 'FUNCTIONAL' })
const confirmDelete = ref<string | null>(null)
const confirmBulkDelete = ref(false)
const filterPriority = ref('')
const filterCategory = ref('')

// --- Computed ---
const allSelected = computed(() => items.value.length > 0 && items.value.every((i) => selectedIds.value.has(i.id)))
const someSelected = computed(() => items.value.some((i) => selectedIds.value.has(i.id)) && !allSelected.value)

// --- API ---
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
  } catch (e: any) {
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
    await fetchItems()
  } catch {
    toast.error('更新失败')
  }
}

async function remove(id: string) {
  try {
    await api.delete(`/test-cases/${id}`)
    selectedIds.value.delete(id)
    toast.success('用例已删除')
    await fetchItems()
  } catch {
    toast.error('删除失败')
  }
}

async function bulkDelete() {
  const ids = [...selectedIds.value]
  try {
    await Promise.all(ids.map((id) => api.delete(`/test-cases/${id}`)))
    selectedIds.value.clear()
    toast.success(`已删除 ${ids.length} 条用例`)
    await fetchItems()
  } catch {
    toast.error('批量删除失败')
  }
  confirmBulkDelete.value = false
}

// --- Selection ---
function toggleSelect(id: string) {
  const s = new Set(selectedIds.value)
  if (s.has(id)) s.delete(id)
  else s.add(id)
  selectedIds.value = s
}

function toggleSelectAll() {
  if (allSelected.value) {
    selectedIds.value = new Set()
  } else {
    selectedIds.value = new Set(items.value.map((i) => i.id))
  }
}

// --- Inline Edit ---
function startEdit(item: any) {
  editingId.value = item.id
  editForm.title = item.title
  editForm.stepsText = JSON.stringify(item.steps || [])
  editForm.expectedText = JSON.stringify(item.expected || [])
  editForm.priority = item.priority || 'P1'
  editForm.category = item.category || 'FUNCTIONAL'
}

function cancelEdit() {
  editingId.value = null
}

function saveEdit(id: string) {
  let steps: any
  let expected: any
  try {
    steps = JSON.parse(editForm.stepsText)
    expected = JSON.parse(editForm.expectedText)
  } catch {
    toast.error('步骤或预期 JSON 格式错误')
    return
  }
  updateCase(id, {
    title: editForm.title,
    steps,
    expected,
    priority: editForm.priority,
    category: editForm.category,
  })
}

// --- Watchers ---
watch([search, filterPriority, filterCategory], () => {
  page.value = 1
  fetchItems()
})

watch(page, () => {
  fetchItems()
})

onMounted(fetchItems)
</script>

<template>
  <div class="space-y-6 pb-12">
    <!-- Header -->
    <div class="flex items-center justify-between">
      <div class="flex flex-col gap-1">
        <h2 class="text-2xl font-bold tracking-tight text-gray-900">用例库</h2>
        <p class="text-gray-500 text-sm">管理和生成测试用例，支持手动创建与 AI 自动生成。</p>
      </div>
      <span class="text-xs text-gray-400 font-mono">{{ total }} 条</span>
    </div>

    <!-- Filters -->
    <div class="bg-white border border-gray-200 rounded-xl shadow-sm p-4">
      <div class="flex flex-wrap items-center gap-3">
        <div class="flex-1 min-w-[200px]">
          <SearchInput v-model="search" placeholder="搜索用例标题..." />
        </div>
        <select
          v-model="filterPriority"
          class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all"
        >
          <option value="">全部优先级</option>
          <option value="P0">P0</option>
          <option value="P1">P1</option>
          <option value="P2">P2</option>
          <option value="P3">P3</option>
        </select>
        <select
          v-model="filterCategory"
          class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all"
        >
          <option value="">全部分类</option>
          <option value="FUNCTIONAL">FUNCTIONAL</option>
          <option value="UI">UI</option>
          <option value="API">API</option>
          <option value="PERFORMANCE">PERFORMANCE</option>
          <option value="SECURITY">SECURITY</option>
        </select>
        <button
          v-if="selectedIds.size > 0"
          @click="confirmBulkDelete = true"
          class="flex items-center gap-2 px-4 py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg text-xs font-bold transition-all shadow-md shadow-red-600/10"
        >
          <Trash2 :size="14" />
          删除选中 ({{ selectedIds.size }})
        </button>
      </div>
    </div>

    <!-- Table -->
    <div class="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">
      <LoadingSpinner v-if="loading" text="加载中..." />
      <template v-else-if="items.length === 0">
        <EmptyState
          :icon="FileCode"
          title="暂无用例"
          description="还没有任何测试用例，请稍后再试或调整筛选条件。"
        />
      </template>
      <template v-else>
        <div class="overflow-x-auto">
          <table class="w-full text-left text-sm">
            <thead>
              <tr class="bg-gray-50 text-gray-500 border-b border-gray-100">
                <th class="w-10 px-4 py-3">
                  <button @click="toggleSelectAll" class="text-gray-400 hover:text-blue-600 transition-colors">
                    <CheckSquare v-if="allSelected" :size="16" />
                    <MinusSquare v-else-if="someSelected" :size="16" />
                    <Square v-else :size="16" />
                  </button>
                </th>
                <th class="px-4 py-3 font-bold uppercase tracking-widest text-[10px]">标题</th>
                <th class="px-4 py-3 font-bold uppercase tracking-widest text-[10px] w-32">分类</th>
                <th class="px-4 py-3 font-bold uppercase tracking-widest text-[10px] w-20">优先级</th>
                <th class="px-4 py-3 font-bold uppercase tracking-widest text-[10px]">步骤预览</th>
                <th class="px-4 py-3 font-bold uppercase tracking-widest text-[10px] w-24 text-right">操作</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-100">
              <tr
                v-for="item in items"
                :key="item.id"
                class="hover:bg-gray-50 transition-colors"
                :class="{ 'bg-blue-50/40': selectedIds.has(item.id) }"
              >
                <!-- Checkbox -->
                <td class="px-4 py-3">
                  <button @click="toggleSelect(item.id)" class="text-gray-400 hover:text-blue-600 transition-colors">
                    <CheckSquare v-if="selectedIds.has(item.id)" :size="16" class="text-blue-600" />
                    <Square v-else :size="16" />
                  </button>
                </td>

                <!-- Title -->
                <td class="px-4 py-3">
                  <template v-if="editingId === item.id">
                    <input
                      v-model="editForm.title"
                      class="w-full px-2 py-1 bg-white border border-blue-400 rounded text-sm outline-none"
                    />
                  </template>
                  <template v-else>
                    <span class="font-medium text-gray-900">{{ item.title }}</span>
                  </template>
                </td>

                <!-- Category -->
                <td class="px-4 py-3">
                  <template v-if="editingId === item.id">
                    <select v-model="editForm.category" class="w-full px-2 py-1 bg-white border border-blue-400 rounded text-xs outline-none">
                      <option value="FUNCTIONAL">FUNCTIONAL</option>
                      <option value="UI">UI</option>
                      <option value="API">API</option>
                      <option value="PERFORMANCE">PERFORMANCE</option>
                      <option value="SECURITY">SECURITY</option>
                    </select>
                  </template>
                  <template v-else>
                    <span class="text-xs font-mono text-gray-500">{{ item.category }}</span>
                  </template>
                </td>

                <!-- Priority -->
                <td class="px-4 py-3">
                  <template v-if="editingId === item.id">
                    <select v-model="editForm.priority" class="w-full px-2 py-1 bg-white border border-blue-400 rounded text-xs outline-none">
                      <option value="P0">P0</option>
                      <option value="P1">P1</option>
                      <option value="P2">P2</option>
                      <option value="P3">P3</option>
                    </select>
                  </template>
                  <template v-else>
                    <span
                      class="inline-block px-2 py-0.5 rounded text-[10px] font-bold"
                      :class="{
                        'bg-red-100 text-red-700': item.priority === 'P0',
                        'bg-orange-100 text-orange-700': item.priority === 'P1',
                        'bg-yellow-100 text-yellow-700': item.priority === 'P2',
                        'bg-gray-100 text-gray-600': item.priority === 'P3',
                      }"
                    >{{ item.priority }}</span>
                  </template>
                </td>

                <!-- Steps preview -->
                <td class="px-4 py-3">
                  <template v-if="editingId === item.id">
                    <div class="space-y-1">
                      <input
                        v-model="editForm.stepsText"
                        class="w-full px-2 py-1 bg-white border border-blue-400 rounded text-xs font-mono outline-none"
                        placeholder='["步骤1", "步骤2"]'
                      />
                      <input
                        v-model="editForm.expectedText"
                        class="w-full px-2 py-1 bg-white border border-blue-400 rounded text-xs font-mono outline-none"
                        placeholder='["预期结果"]'
                      />
                    </div>
                  </template>
                  <template v-else>
                    <div class="text-xs text-gray-500 truncate max-w-sm">
                      {{ (item.steps || []).slice(0, 2).map((s: any) => typeof s === 'string' ? s : JSON.stringify(s)).join(' → ') }}
                      <span v-if="(item.steps || []).length > 2" class="text-gray-400"> ...</span>
                    </div>
                  </template>
                </td>

                <!-- Actions -->
                <td class="px-4 py-3 text-right">
                  <template v-if="editingId === item.id">
                    <div class="flex items-center justify-end gap-1">
                      <button
                        @click="saveEdit(item.id)"
                        class="p-1.5 rounded-lg text-green-600 hover:bg-green-50 transition-all"
                        title="保存"
                      >
                        <Check :size="14" />
                      </button>
                      <button
                        @click="cancelEdit"
                        class="p-1.5 rounded-lg text-gray-400 hover:bg-gray-100 transition-all"
                        title="取消"
                      >
                        <X :size="14" />
                      </button>
                    </div>
                  </template>
                  <template v-else>
                    <div class="flex items-center justify-end gap-1">
                      <button
                        @click="startEdit(item)"
                        class="p-1.5 rounded-lg text-gray-400 hover:text-blue-600 hover:bg-blue-50 transition-all"
                        title="编辑"
                      >
                        <Pencil :size="14" />
                      </button>
                      <button
                        @click="confirmDelete = item.id"
                        class="p-1.5 rounded-lg text-gray-400 hover:text-red-600 hover:bg-red-50 transition-all"
                        title="删除"
                      >
                        <Trash2 :size="14" />
                      </button>
                    </div>
                  </template>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <Pagination
          :page="page"
          :page-size="pageSize"
          :total="total"
          @update:page="page = $event"
        />
      </template>
    </div>

    <!-- Single Delete Confirm -->
    <ConfirmDialog
      :show="confirmDelete !== null"
      title="删除用例"
      message="确定要删除这条用例吗？此操作不可撤销。"
      confirm-text="删除"
      :danger="true"
      @confirm="() => { if (confirmDelete) remove(confirmDelete); confirmDelete = null }"
      @cancel="confirmDelete = null"
    />

    <!-- Bulk Delete Confirm -->
    <ConfirmDialog
      :show="confirmBulkDelete"
      title="批量删除"
      :message="`确定要删除选中的 ${selectedIds.size} 条用例吗？此操作不可撤销。`"
      confirm-text="全部删除"
      :danger="true"
      @confirm="bulkDelete"
      @cancel="confirmBulkDelete = false"
    />
  </div>
</template>
