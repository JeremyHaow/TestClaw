<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import api from '../lib/api'
import { useToast } from '../composables/useToast'
import { BookOpen, Check, Edit3, Plus, Search, Trash2, X } from 'lucide-vue-next'
import EmptyState from '../components/EmptyState.vue'
import LoadingSpinner from '../components/LoadingSpinner.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'

const toast = useToast()
const items = ref<any[]>([])
const loading = ref(false)
const searchQuery = ref('')
const searchResults = ref<any[]>([])
const searching = ref(false)
const newContent = ref('')
const adding = ref(false)
const deleteTarget = ref<any>(null)
const selectedEntry = ref<any | null>(null)
const editing = ref(false)
const editContent = ref('')

const displayedItems = computed(() => {
  if (searchQuery.value.trim()) return searchResults.value
  return items.value
})

let searchTimer: ReturnType<typeof setTimeout> | null = null

function debouncedSearch() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(doSearch, 300)
}

onUnmounted(() => {
  if (searchTimer) clearTimeout(searchTimer)
})

async function fetchItems() {
  loading.value = true
  try {
    const { data } = await api.get('/knowledge')
    items.value = data
    if (!selectedEntry.value && data.length) selectedEntry.value = data[0]
    if (selectedEntry.value) {
      selectedEntry.value = data.find((item: any) => item.id === selectedEntry.value?.id) || selectedEntry.value
    }
  } catch {
    toast.error('加载知识库失败')
  } finally {
    loading.value = false
  }
}

async function doSearch() {
  const query = searchQuery.value.trim()
  if (!query) {
    searchResults.value = []
    return
  }
  searching.value = true
  try {
    const { data } = await api.get('/knowledge/search', { params: { q: query } })
    searchResults.value = data
  } catch {
    toast.error('搜索失败')
  } finally {
    searching.value = false
  }
}

function clearSearch() {
  searchQuery.value = ''
  searchResults.value = []
}

function selectEntry(entry: any) {
  selectedEntry.value = entry
  editing.value = false
  editContent.value = ''
}

async function addEntry() {
  const content = newContent.value.trim()
  if (!content) return
  adding.value = true
  try {
    const { data } = await api.post('/knowledge', { content })
    newContent.value = ''
    selectedEntry.value = data
    toast.success(data.embedding_available ? '知识条目已添加并生成向量' : '知识条目已添加，暂未生成向量')
    await fetchItems()
  } catch {
    toast.error('添加失败')
  } finally {
    adding.value = false
  }
}

function startEdit() {
  if (!selectedEntry.value) return
  editContent.value = selectedEntry.value.content || ''
  editing.value = true
}

async function saveEdit() {
  if (!selectedEntry.value) return
  const content = editContent.value.trim()
  if (!content) {
    toast.warning('知识内容不能为空')
    return
  }
  try {
    const { data } = await api.put(`/knowledge/${selectedEntry.value.id}`, { content })
    selectedEntry.value = data
    editing.value = false
    toast.success(data.embedding_available ? '知识条目已更新并重新生成向量' : '知识条目已更新，向量暂不可用')
    await fetchItems()
    if (searchQuery.value.trim()) await doSearch()
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '更新失败')
  }
}

async function doDelete() {
  if (!deleteTarget.value) return
  const target = deleteTarget.value
  try {
    await api.delete(`/knowledge/${target.id}`)
    toast.success('知识条目已删除')
    deleteTarget.value = null
    if (selectedEntry.value?.id === target.id) selectedEntry.value = null
    await fetchItems()
    if (searchQuery.value.trim()) await doSearch()
  } catch {
    toast.error('删除失败')
  }
}

function embeddingLabel(entry: any) {
  return entry?.embedding_available ? '向量已生成' : '无向量'
}

function sourceLabel(entry: any) {
  return entry?.source_script_id ? `run ${String(entry.source_script_id).slice(0, 8)}` : '手动知识'
}

onMounted(fetchItems)
</script>

<template>
  <div class="mx-auto max-w-7xl space-y-4 pb-10">
    <div class="flex flex-col gap-3 border-b border-gray-200 pb-4 lg:flex-row lg:items-end lg:justify-between">
      <div class="min-w-0">
        <h2 class="text-xl font-semibold tracking-tight text-gray-950">RAG 知识库</h2>
        <p class="mt-1 max-w-3xl text-sm text-gray-500">管理测试经验、缺陷根因和修复建议；每条知识按真实 embedding 状态显示，不把降级检索伪装成向量命中。</p>
      </div>
      <span class="w-fit rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-bold text-gray-600">
        {{ items.length }} 条知识
      </span>
    </div>

    <div class="grid grid-cols-1 gap-4 lg:grid-cols-[340px_minmax(0,1fr)] lg:items-start">
      <aside class="space-y-4 lg:sticky lg:top-4 lg:max-h-[calc(100vh-9rem)] lg:overflow-y-auto lg:pr-1">
        <section class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <h3 class="mb-3 text-xs font-bold uppercase tracking-widest text-gray-400">添加知识</h3>
          <textarea v-model="newContent" rows="5" placeholder="输入知识内容..."
            class="w-full resize-none rounded-lg border border-gray-200 bg-gray-50 px-4 py-2.5 text-sm outline-none transition-all focus:border-blue-500 focus:bg-white" />
          <button @click="addEntry" :disabled="adding || !newContent.trim()"
            class="mt-3 flex w-full items-center justify-center gap-2 rounded-lg bg-blue-600 py-2.5 text-sm font-bold text-white transition-colors hover:bg-blue-700 disabled:opacity-50">
            <Plus :size="16" />
            {{ adding ? '添加中...' : '添加条目' }}
          </button>
        </section>

        <section class="rounded-lg border border-gray-200 bg-white shadow-sm">
          <div class="border-b border-gray-100 p-4">
            <h3 class="mb-3 text-xs font-bold uppercase tracking-widest text-gray-400">搜索 / 列表</h3>
            <div class="relative">
              <Search :size="16" class="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
              <input v-model="searchQuery" @input="debouncedSearch" placeholder="搜索知识内容..."
                class="w-full rounded-lg border border-gray-200 bg-gray-50 py-2.5 pl-9 pr-8 text-sm outline-none transition-all focus:border-blue-500 focus:bg-white" />
              <button v-if="searchQuery" @click="clearSearch" class="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-gray-400 hover:bg-gray-200">
                <X :size="14" />
              </button>
            </div>
            <div class="mt-2 text-[11px] text-gray-400">
              {{ searchQuery ? `搜索结果 ${searchResults.length} 条` : `全部条目 ${items.length} 条` }}
            </div>
          </div>

          <LoadingSpinner v-if="loading || searching" text="加载中..." />
          <EmptyState v-else-if="!displayedItems.length" :icon="BookOpen" title="暂无知识条目" description="添加知识条目来构建你的测试知识库。" />
          <div v-else class="max-h-96 min-h-[260px] divide-y divide-gray-100 overflow-y-auto lg:max-h-[calc(100vh-28rem)]">
            <button
              v-for="entry in displayedItems"
              :key="entry.id"
              @click="selectEntry(entry)"
              class="w-full p-4 text-left transition-colors hover:bg-gray-50"
              :class="selectedEntry?.id === entry.id ? 'bg-blue-50/60' : 'bg-white'"
            >
              <div class="flex items-start justify-between gap-3">
                <div class="min-w-0">
                  <div class="flex flex-wrap items-center gap-2">
                    <span class="font-mono text-[10px] text-gray-400">{{ entry.id.slice(0, 8) }}</span>
                    <span class="rounded px-2 py-0.5 text-[10px] font-bold" :class="entry.embedding_available ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'">
                      {{ embeddingLabel(entry) }}
                    </span>
                  </div>
                  <p class="mt-2 line-clamp-3 text-sm leading-5 text-gray-700">{{ entry.content }}</p>
                  <div class="mt-2 flex flex-wrap gap-2 text-[10px] text-gray-400">
                    <span>{{ sourceLabel(entry) }}</span>
                    <span>{{ entry.created_at }}</span>
                  </div>
                </div>
              </div>
            </button>
          </div>
        </section>
      </aside>

      <section class="min-w-0 overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm lg:flex lg:max-h-[calc(100vh-9rem)] lg:flex-col">
        <div class="flex flex-wrap items-start justify-between gap-3 border-b border-gray-100 px-5 py-4">
          <div>
            <h3 class="text-sm font-bold text-gray-900">知识详情</h3>
            <p v-if="selectedEntry" class="mt-1 text-xs text-gray-500">{{ sourceLabel(selectedEntry) }} / {{ embeddingLabel(selectedEntry) }}</p>
          </div>
          <div v-if="selectedEntry" class="flex items-center gap-2">
            <button v-if="!editing" @click="startEdit"
              class="inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-bold text-gray-600 transition-all hover:bg-blue-50 hover:text-blue-700">
              <Edit3 :size="14" /> 编辑
            </button>
            <button @click="deleteTarget = selectedEntry"
              class="rounded-lg p-2 text-gray-400 transition-all hover:bg-red-50 hover:text-red-500" title="删除">
              <Trash2 :size="16" />
            </button>
          </div>
        </div>

        <div v-if="!selectedEntry" class="p-6">
          <EmptyState :icon="BookOpen" title="选择一条知识" description="从左侧列表选择条目后查看详情、编辑内容和确认向量状态。" />
        </div>

        <div v-else class="min-h-0 p-5 lg:flex-1 lg:overflow-y-auto">
          <div class="mb-4 grid gap-3 sm:grid-cols-3">
            <div class="rounded-lg border border-gray-200 bg-gray-50 p-3">
              <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">Embedding</div>
              <div class="mt-1 text-sm font-semibold" :class="selectedEntry.embedding_available ? 'text-emerald-700' : 'text-amber-700'">
                {{ embeddingLabel(selectedEntry) }}
              </div>
            </div>
            <div class="rounded-lg border border-gray-200 bg-gray-50 p-3">
              <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">来源</div>
              <div class="mt-1 truncate text-sm font-semibold text-gray-900">{{ sourceLabel(selectedEntry) }}</div>
            </div>
            <div class="rounded-lg border border-gray-200 bg-gray-50 p-3">
              <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">创建时间</div>
              <div class="mt-1 truncate text-xs font-mono text-gray-500">{{ selectedEntry.created_at }}</div>
            </div>
          </div>

          <template v-if="editing">
            <textarea v-model="editContent" rows="18"
              class="max-h-[calc(100vh-420px)] min-h-[320px] w-full resize-none rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-sm leading-6 outline-none transition-all focus:border-blue-500 focus:bg-white" />
            <div class="mt-4 flex justify-end gap-2">
              <button @click="editing = false" class="rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-bold text-gray-600 transition-all hover:bg-gray-50">取消</button>
              <button @click="saveEdit" class="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-bold text-white transition-all hover:bg-blue-700">
                <Check :size="15" /> 保存
              </button>
            </div>
          </template>

          <div v-else class="max-h-[420px] overflow-y-auto rounded-lg border border-gray-200 bg-gray-50 p-4 lg:max-h-[calc(100vh-22rem)]">
            <p class="whitespace-pre-wrap break-words text-sm leading-6 text-gray-700">{{ selectedEntry.content }}</p>
          </div>
        </div>
      </section>
    </div>

    <ConfirmDialog
      :show="!!deleteTarget"
      title="删除知识条目"
      :message="`确定要删除这条知识条目吗？`"
      confirm-text="删除"
      cancel-text="取消"
      :danger="true"
      @confirm="doDelete"
      @cancel="deleteTarget = null"
    />
  </div>
</template>
