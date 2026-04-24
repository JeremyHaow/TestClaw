<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import api from '../lib/api'
import StatusBadge from '../components/StatusBadge.vue'
import LoadingSpinner from '../components/LoadingSpinner.vue'
import EmptyState from '../components/EmptyState.vue'
import Pagination from '../components/Pagination.vue'
import { useToast } from '../composables/useToast'
import { Filter, Eye, Trash2 } from 'lucide-vue-next'

const router = useRouter()
const toast = useToast()
const runs = ref<any[]>([])
const loading = ref(false)
const filterStatus = ref('')
const filterType = ref('')
const page = ref(1)
const pageSize = 20
const total = ref(0)

async function fetchRuns() {
  loading.value = true
  try {
    const params: any = { page: page.value, page_size: pageSize }
    if (filterStatus.value) params.status = filterStatus.value
    if (filterType.value) params.test_type = filterType.value
    const { data, headers } = await api.get('/runs', { params })
    runs.value = Array.isArray(data) ? data : data.items || []
    total.value = parseInt(headers?.['x-total-count'] || String(runs.value.length))
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '加载历史记录失败')
  } finally {
    loading.value = false
  }
}

async function deleteRun(id: string, e: Event) {
  e.stopPropagation()
  if (!confirm('确定删除此运行记录？')) return
  try {
    await api.delete(`/runs/${id}`)
    toast.success('已删除')
    fetchRuns()
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '删除失败')
  }
}

onMounted(fetchRuns)
</script>

<template>
  <div class="space-y-6 pb-12">
    <div class="flex flex-col gap-1">
      <h2 class="text-2xl font-bold tracking-tight text-gray-900">历史记录</h2>
      <p class="text-gray-500 text-sm">查看所有测试运行记录。</p>
    </div>

    <!-- Filters -->
    <div class="bg-white border border-gray-200 rounded-xl shadow-sm p-4 flex flex-wrap gap-4 items-center">
      <div class="flex items-center gap-2">
        <Filter :size="14" class="text-gray-400" />
        <span class="text-xs text-gray-500 font-bold">筛选</span>
      </div>
      <select v-model="filterStatus" @change="page = 1; fetchRuns()"
        class="px-3 py-1.5 bg-gray-50 border border-gray-200 rounded-lg text-xs outline-none focus:border-blue-500">
        <option value="">全部状态</option>
        <option value="succeeded">通过</option>
        <option value="failed">失败</option>
        <option value="bug_found">缺陷</option>
        <option value="queued">排队中</option>
        <option value="running">运行中</option>
      </select>
      <select v-model="filterType" @change="page = 1; fetchRuns()"
        class="px-3 py-1.5 bg-gray-50 border border-gray-200 rounded-lg text-xs outline-none focus:border-blue-500">
        <option value="">全部类型</option>
        <option value="auto">自动</option>
        <option value="api">API</option>
        <option value="ui">UI</option>
      </select>
      <span class="text-xs text-gray-400 ml-auto">{{ total }} 条记录</span>
    </div>

    <!-- Run List -->
    <LoadingSpinner v-if="loading" text="加载中..." />
    <EmptyState
      v-else-if="!runs.length"
      :icon="Filter"
      :title="total ? '没有匹配的记录' : '暂无运行记录'"
      :description="total ? '请尝试调整筛选条件' : '点击开始测试创建第一次运行'"
    />
    <div v-else class="space-y-3">
      <div v-for="run in runs" :key="run.id"
        @click="router.push(`/runs/${run.id}`)"
        class="bg-white border border-gray-200 rounded-xl p-5 shadow-sm hover:border-blue-200 transition-all cursor-pointer group">
        <div class="flex items-start justify-between">
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-3 mb-2">
              <StatusBadge :status="run.status" />
              <span class="px-2 py-0.5 bg-gray-100 text-gray-600 rounded text-[10px] font-bold uppercase">{{ run.test_type }}</span>
            </div>
            <div class="font-bold text-gray-900 text-sm truncate">{{ run.objective }}</div>
            <div class="text-xs font-mono text-gray-400 mt-0.5 truncate">{{ run.target_url }}</div>
            <div class="text-xs text-gray-400 mt-1">{{ run.created_at ? new Date(run.created_at).toLocaleString('zh-CN') : '' }}</div>
          </div>
          <div class="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            <button @click="deleteRun(run.id, $event)" class="p-2 text-gray-400 hover:text-red-600 transition-colors">
              <Trash2 :size="14" />
            </button>
            <button class="p-2 text-gray-400 group-hover:text-blue-600 transition-colors">
              <Eye :size="16" />
            </button>
          </div>
        </div>
      </div>
    </div>

    <Pagination :page="page" :page-size="pageSize" :total="total" @update:page="page = $event; fetchRuns()" />
  </div>
</template>
