<script setup lang="ts">
import { onMounted, ref } from 'vue'
import api from '../lib/api'
import { useToast } from '../composables/useToast'
import { BookOpen, Plus, Trash2, Search, X } from 'lucide-vue-next'
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

async function fetchItems() {
  loading.value = true
  try {
    const { data } = await api.get('/knowledge')
    items.value = data
  } catch {
    toast.error('加载知识库失败')
  } finally {
    loading.value = false
  }
}

async function doSearch() {
  if (!searchQuery.value.trim()) {
    searchResults.value = []
    return
  }
  searching.value = true
  try {
    const { data } = await api.get('/knowledge/search', { params: { q: searchQuery.value } })
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

async function addEntry() {
  if (!newContent.value.trim()) return
  adding.value = true
  try {
    await api.post('/knowledge', { content: newContent.value })
    newContent.value = ''
    toast.success('知识条目已添加')
    await fetchItems()
  } catch {
    toast.error('添加失败')
  } finally {
    adding.value = false
  }
}

async function doDelete() {
  if (!deleteTarget.value) return
  const target = deleteTarget.value
  try {
    await api.delete(`/knowledge/${target.id}`)
    toast.success('知识条目已删除')
    deleteTarget.value = null
    await fetchItems()
  } catch {
    toast.error('删除失败')
  }
}

onMounted(fetchItems)
</script>

<template>
  <div class="space-y-8 pb-12">
    <div class="flex flex-col gap-1">
      <h2 class="text-2xl font-bold tracking-tight text-gray-900">知识库</h2>
      <p class="text-gray-500 text-sm">管理和搜索测试知识，支持全文检索。</p>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-2 gap-8 items-start">
      <!-- Left: Add + Search -->
      <div class="space-y-6">
        <div class="bg-white border border-gray-200 rounded-xl shadow-sm p-6 space-y-4">
          <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest">添加知识</h3>
          <textarea v-model="newContent" rows="6" placeholder="输入知识内容..."
            class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all resize-none" />
          <button @click="addEntry" :disabled="adding || !newContent.trim()"
            class="w-full py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-bold transition-all shadow-md shadow-blue-600/10 flex items-center justify-center gap-2">
            <Plus :size="16" />
            {{ adding ? '添加中...' : '添加条目' }}
          </button>
        </div>

        <div class="bg-white border border-gray-200 rounded-xl shadow-sm p-6 space-y-4">
          <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest">搜索知识</h3>
          <div class="relative">
            <Search :size="16" class="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input v-model="searchQuery" @input="doSearch" placeholder="搜索知识内容..."
              class="w-full pl-9 pr-8 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all" />
            <button v-if="searchQuery" @click="clearSearch"
              class="absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded hover:bg-gray-200 text-gray-400">
              <X :size="14" />
            </button>
          </div>
          <div v-if="searchResults.length" class="space-y-2">
            <div class="text-[10px] font-bold text-gray-400 uppercase">找到 {{ searchResults.length }} 条结果</div>
            <div v-for="r in searchResults" :key="r.id" class="p-3 bg-blue-50 border border-blue-100 rounded-lg">
              <p class="text-xs text-gray-700 line-clamp-3">{{ r.content }}</p>
              <div class="text-[10px] text-gray-400 mt-1">{{ r.created_at }}</div>
            </div>
          </div>
          <p v-else-if="searchQuery && !searching" class="text-xs text-gray-400 text-center py-4">没有匹配的结果</p>
        </div>
      </div>

      <!-- Right: Knowledge list -->
      <div class="space-y-4">
        <div class="flex items-center justify-between">
          <h3 class="text-sm font-bold text-gray-900">知识条目</h3>
          <span class="text-xs text-gray-400 font-mono">{{ items.length }} 条</span>
        </div>
        <LoadingSpinner v-if="loading" text="加载中..." />
        <EmptyState v-else-if="!items.length" :icon="BookOpen" title="暂无知识条目"
          description="添加知识条目来构建你的测试知识库" />
        <div v-else class="space-y-3">
          <div v-for="item in items" :key="item.id"
            class="bg-white border border-gray-200 rounded-xl p-5 shadow-sm hover:border-blue-200 transition-all group">
            <div class="flex items-start justify-between mb-2">
              <div class="flex items-center gap-2">
                <BookOpen :size="14" class="text-blue-500" />
                <span class="text-[10px] font-mono text-gray-400">{{ item.id.slice(0, 8) }}</span>
              </div>
              <button @click="deleteTarget = item"
                class="opacity-0 group-hover:opacity-100 p-1 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded transition-all">
                <Trash2 :size="14" />
              </button>
            </div>
            <p class="text-sm text-gray-700 whitespace-pre-wrap">{{ item.content }}</p>
            <div class="text-[10px] text-gray-400 mt-2">{{ item.created_at }}</div>
          </div>
        </div>
      </div>
    </div>

    <ConfirmDialog :show="!!deleteTarget" title="删除知识条目"
      :message="`确定要删除这条知识条目吗？`" confirm-text="删除" cancel-text="取消" :danger="true"
      @confirm="doDelete" @cancel="deleteTarget = null" />
  </div>
</template>
