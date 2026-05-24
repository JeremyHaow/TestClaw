<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../lib/api'
import StatusBadge from '../components/StatusBadge.vue'
import LoadingSpinner from '../components/LoadingSpinner.vue'
import EmptyState from '../components/EmptyState.vue'
import Pagination from '../components/Pagination.vue'
import StyledSelect from '../components/StyledSelect.vue'
import { useToast } from '../composables/useToast'
import { Eye, Filter, Trash2 } from 'lucide-vue-next'

const router = useRouter()
const toast = useToast()
const runs = ref<any[]>([])
const loading = ref(false)
const hasLoadedRuns = ref(false)
const filterStatus = ref('')
const filterType = ref('')
const page = ref(1)
const pageSize = ref(5)
const pageSizeOptions = [5, 10, 15]
const total = ref(0)

function formatTime(value: string | null | undefined) {
  if (!value) return ''
  return new Date(value).toLocaleString('zh-CN')
}

async function fetchRuns() {
  loading.value = true
  try {
    const params: any = { page: page.value, page_size: pageSize.value }
    if (filterStatus.value) params.status = filterStatus.value
    if (filterType.value) params.test_type = filterType.value
    const { data, headers } = await api.get('/runs', { params })
    runs.value = Array.isArray(data) ? data : data.items || []
    const headerTotal = Number.parseInt(headers?.['x-total-count'] || '', 10)
    total.value = Number.isNaN(headerTotal) ? runs.value.length : headerTotal
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '加载历史记录失败')
  } finally {
    loading.value = false
    hasLoadedRuns.value = true
  }
}

function resetAndFetchRuns() {
  page.value = 1
  fetchRuns()
}

function changePage(nextPage: number) {
  page.value = nextPage
  fetchRuns()
}

async function deleteRun(id: string, e: Event) {
  e.stopPropagation()
  if (!confirm('确定删除此运行记录？')) return
  try {
    await api.delete(`/runs/${id}`)
    toast.success('已删除')
    await fetchRuns()
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '删除失败')
  }
}

let pollTimer: ReturnType<typeof setInterval> | null = null

function startPolling() {
  stopPolling()
  pollTimer = setInterval(() => {
    const hasActive = runs.value.some((r: any) => ['queued', 'running'].includes(r.status))
    if (hasActive) {
      fetchRuns()
    }
  }, 5000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

onMounted(() => {
  fetchRuns()
  startPolling()
})
onUnmounted(() => stopPolling())
</script>

<template>
  <div class="mx-auto max-w-7xl space-y-5 pb-10">
    <div class="flex flex-col gap-1 border-b border-gray-200/80 pb-5">
      <div class="tc-page-kicker">Runs</div>
      <h2 class="text-xl font-semibold tracking-tight text-gray-950">运行历史</h2>
      <p class="text-sm text-gray-500">筛选、查看和管理测试智能体的历史运行。</p>
    </div>

    <!-- Filters -->
    <div class="grid gap-3 rounded-lg border border-gray-200 bg-white p-3 shadow-sm lg:grid-cols-[auto_minmax(0,1fr)_auto] lg:items-end">
      <div class="flex items-center gap-2 self-start lg:self-center">
        <Filter :size="14" class="text-gray-400" />
        <span class="text-xs text-gray-500 font-bold">筛选</span>
      </div>
      <div class="grid min-w-0 grid-cols-1 gap-2 sm:grid-cols-[minmax(132px,168px)_minmax(124px,160px)_minmax(104px,132px)] lg:w-fit">
        <label class="grid min-w-0 gap-1">
          <span class="whitespace-nowrap text-[10px] font-bold uppercase tracking-widest text-gray-400">状态</span>
          <StyledSelect
            v-model="filterStatus"
            @change="resetAndFetchRuns"
            class="w-full"
            size="sm"
          >
            <option value="">全部状态</option>
            <option value="succeeded">通过</option>
            <option value="failed">失败</option>
            <option value="bug_found">缺陷</option>
            <option value="queued">排队中</option>
            <option value="running">运行中</option>
          </StyledSelect>
        </label>
        <label class="grid min-w-0 gap-1">
          <span class="whitespace-nowrap text-[10px] font-bold uppercase tracking-widest text-gray-400">类型</span>
          <StyledSelect
            v-model="filterType"
            @change="resetAndFetchRuns"
            class="w-full"
            size="sm"
          >
            <option value="">全部类型</option>
            <option value="AUTO">自动</option>
            <option value="API">API</option>
            <option value="UI">UI</option>
          </StyledSelect>
        </label>
        <label class="grid min-w-0 gap-1">
          <span class="whitespace-nowrap text-[10px] font-bold uppercase tracking-widest text-gray-400">每页</span>
          <StyledSelect
            v-model.number="pageSize"
            @change="resetAndFetchRuns"
            class="w-full"
            size="sm"
          >
            <option v-for="size in pageSizeOptions" :key="size" :value="size">{{ size }} 条</option>
          </StyledSelect>
        </label>
      </div>
      <span class="justify-self-start whitespace-nowrap rounded-lg bg-gray-50 px-2.5 py-1.5 text-xs font-medium text-gray-500 lg:justify-self-end">
        {{ loading ? '正在加载记录...' : `${total} 条记录` }}
      </span>
    </div>

    <!-- Run List -->
    <LoadingSpinner v-if="loading || !hasLoadedRuns" text="加载运行记录中..." />
    <EmptyState
      v-else-if="!runs.length"
      :icon="Filter"
      :title="total ? '没有匹配的记录' : '暂无运行记录'"
      :description="total ? '请尝试调整筛选条件' : '点击开始测试创建第一次运行'"
    />
    <div v-else class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <div
        v-for="run in runs"
        :key="run.id"
        @click="router.push(`/runs/${run.id}`)"
        class="group cursor-pointer border-b border-gray-100 p-4 transition-colors last:border-b-0 hover:bg-gray-50"
      >
        <div class="flex items-start justify-between gap-4">
          <div class="min-w-0 flex-1">
            <div class="mb-2 flex flex-wrap items-center gap-2">
              <StatusBadge :status="run.status" />
              <span class="rounded-md bg-gray-100 px-2 py-0.5 text-[10px] font-bold uppercase text-gray-600">{{ run.test_type }}</span>
              <span class="text-[11px] text-gray-400">{{ formatTime(run.created_at) }}</span>
            </div>
            <div class="font-bold text-gray-900 text-sm truncate">{{ run.objective }}</div>
            <div class="text-xs font-mono text-gray-400 mt-0.5 truncate">{{ run.target_url }}</div>
          </div>
          <div class="flex shrink-0 items-center gap-1 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100">
            <button
              type="button"
              :aria-label="`删除运行 ${run.objective || run.id}`"
              @click="deleteRun(run.id, $event)"
              class="p-2 text-gray-400 hover:text-red-600 transition-colors"
            >
              <Trash2 :size="14" />
            </button>
            <button
              type="button"
              :aria-label="`查看运行 ${run.objective || run.id}`"
              @click.stop="router.push(`/runs/${run.id}`)"
              class="p-2 text-gray-400 group-hover:text-blue-600 transition-colors"
            >
              <Eye :size="16" />
            </button>
          </div>
        </div>
      </div>
    </div>

    <Pagination
      :page="page"
      :page-size="pageSize"
      :total="total"
      @update:page="changePage"
    />
  </div>
</template>
