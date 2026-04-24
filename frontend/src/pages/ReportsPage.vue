<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import api from '../lib/api'
import StatusBadge from '../components/StatusBadge.vue'
import LoadingSpinner from '../components/LoadingSpinner.vue'
import EmptyState from '../components/EmptyState.vue'
import { useToast } from '../composables/useToast'
import { Filter, Eye } from 'lucide-vue-next'

const router = useRouter()
const toast = useToast()
const tasks = ref<any[]>([])
const loading = ref(false)
const filterStatus = ref('')
const filterType = ref('')

async function fetchTasks() {
  loading.value = true
  try {
    const { data } = await api.get('/tasks')
    tasks.value = data
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '加载测试报告失败')
  } finally {
    loading.value = false
  }
}

const filtered = computed(() => {
  return tasks.value.filter((t: any) => {
    if (filterStatus.value && t.status !== filterStatus.value) return false
    if (filterType.value && t.test_type !== filterType.value) return false
    return true
  })
})

const stats = computed(() => {
  const total = tasks.value.length
  const succeeded = tasks.value.filter((t: any) => t.status === 'succeeded').length
  const failed = tasks.value.filter((t: any) => t.status === 'failed').length
  const bugFound = tasks.value.filter((t: any) => t.status === 'bug_found').length
  return { total, succeeded, failed, bugFound }
})

onMounted(fetchTasks)
</script>

<template>
  <div class="space-y-8 pb-12">
    <div class="flex flex-col gap-1">
      <h2 class="text-2xl font-bold tracking-tight text-gray-900">测试报告</h2>
      <p class="text-gray-500 text-sm">查看执行记录、错误分析和 AI 辅助诊断。</p>
    </div>

    <!-- Stats -->
    <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
      <div class="bg-white border border-gray-200 rounded-xl p-5 shadow-sm">
        <div class="text-[10px] font-bold text-gray-400 uppercase tracking-widest">总记录</div>
        <div class="text-2xl font-bold text-gray-900 mt-1">{{ stats.total }}</div>
      </div>
      <div class="bg-white border border-emerald-200 rounded-xl p-5 shadow-sm">
        <div class="text-[10px] font-bold text-emerald-400 uppercase tracking-widest">通过</div>
        <div class="text-2xl font-bold text-emerald-600 mt-1">{{ stats.succeeded }}</div>
      </div>
      <div class="bg-white border border-red-200 rounded-xl p-5 shadow-sm">
        <div class="text-[10px] font-bold text-red-400 uppercase tracking-widest">失败</div>
        <div class="text-2xl font-bold text-red-600 mt-1">{{ stats.failed }}</div>
      </div>
      <div class="bg-white border border-amber-200 rounded-xl p-5 shadow-sm">
        <div class="text-[10px] font-bold text-amber-400 uppercase tracking-widest">发现缺陷</div>
        <div class="text-2xl font-bold text-amber-600 mt-1">{{ stats.bugFound }}</div>
      </div>
    </div>

    <!-- Filters -->
    <div class="bg-white border border-gray-200 rounded-xl shadow-sm p-4 flex flex-wrap gap-4 items-center">
      <div class="flex items-center gap-2">
        <Filter :size="14" class="text-gray-400" />
        <span class="text-xs text-gray-500 font-bold">筛选</span>
      </div>
      <select v-model="filterStatus"
        class="px-3 py-1.5 bg-gray-50 border border-gray-200 rounded-lg text-xs outline-none focus:border-blue-500">
        <option value="">全部状态</option>
        <option value="succeeded">通过</option>
        <option value="failed">失败</option>
        <option value="bug_found">缺陷</option>
        <option value="queued">排队中</option>
        <option value="running">运行中</option>
      </select>
      <select v-model="filterType"
        class="px-3 py-1.5 bg-gray-50 border border-gray-200 rounded-lg text-xs outline-none focus:border-blue-500">
        <option value="">全部类型</option>
        <option value="ui">UI</option>
        <option value="api">API</option>
        <option value="functional">Functional</option>
        <option value="full">Full</option>
      </select>
      <span class="text-xs text-gray-400 ml-auto">{{ filtered.length }} 条记录</span>
    </div>

    <!-- Task List -->
    <LoadingSpinner v-if="loading" text="加载测试报告中..." />
    <EmptyState
      v-else-if="!filtered.length"
      :icon="Filter"
      :title="tasks.length ? '没有匹配的记录' : '暂无执行记录'"
      :description="tasks.length ? '请尝试调整筛选条件' : '还没有任何测试执行记录'"
    />
    <div v-else class="space-y-3">
      <div v-for="task in filtered" :key="task.id"
        @click="router.push(`/tasks/${task.id}`)"
        class="bg-white border border-gray-200 rounded-xl p-5 shadow-sm hover:border-blue-200 transition-all cursor-pointer group">
        <div class="flex items-start justify-between">
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-3 mb-2">
              <StatusBadge :status="task.status" />
              <span class="px-2 py-0.5 bg-gray-100 text-gray-600 rounded text-[10px] font-bold">{{ task.test_type }}</span>
            </div>
            <div class="font-bold text-gray-900 text-sm truncate">{{ task.objective }}</div>
            <div class="text-xs font-mono text-gray-400 mt-0.5 truncate">{{ task.target_url }}</div>
            <div class="text-xs text-gray-400 mt-1">{{ task.created_at ? new Date(task.created_at).toLocaleString('zh-CN') : '' }}</div>
          </div>
          <button class="p-2 text-gray-400 group-hover:text-blue-600 transition-colors">
            <Eye :size="16" />
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
