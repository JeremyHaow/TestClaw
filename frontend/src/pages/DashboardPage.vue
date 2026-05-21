<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../lib/api'
import StatusBadge from '../components/StatusBadge.vue'
import LoadingSpinner from '../components/LoadingSpinner.vue'
import { useToast } from '../composables/useToast'
import { CheckCircle2, FileCode, Activity, Clock, Beaker, MonitorPlay, Zap, TrendingUp } from 'lucide-vue-next'

const router = useRouter()
const toast = useToast()
const loading = ref(false)
const stats = ref<any>({
  total_tasks: 0,
  total_cases: 0,
  total_envs: 0,
  total_docs: 0,
  pass_rate: 0,
  ai_cases: 0,
  succeeded: 0,
  failed: 0,
  bug_found: 0,
  queued: 0,
  running: 0,
  tasks_by_status: {},
  trend: [],
  recent_tasks: [],
})

onMounted(async () => {
  loading.value = true
  try {
    const { data } = await api.get('/dashboard/stats')
    stats.value = data
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '加载仪表盘数据失败')
  } finally {
    loading.value = false
  }
})

const statCards = [
  { key: 'total_tasks', label: '总任务数', icon: Beaker, color: 'text-blue-600', bg: 'bg-blue-50' },
  { key: 'pass_rate', label: '通过率 (%)', icon: CheckCircle2, color: 'text-emerald-600', bg: 'bg-emerald-50', suffix: '%' },
  { key: 'ai_cases', label: 'AI 生成用例', icon: Zap, color: 'text-purple-600', bg: 'bg-purple-50' },
  { key: 'total_envs', label: '活跃环境', icon: MonitorPlay, color: 'text-amber-600', bg: 'bg-amber-50' },
]

</script>

<template>
  <div class="space-y-8 pb-12">
    <div class="flex flex-col gap-1">
      <h2 class="text-2xl font-bold tracking-tight text-gray-900">仪表盘</h2>
      <p class="text-gray-500 text-sm">实时测试数据与 AI 执行指标。</p>
    </div>

    <LoadingSpinner v-if="loading" text="加载仪表盘数据中..." />
    <template v-else>

    <!-- Stats Grid -->
    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
      <div
        v-for="stat in statCards"
        :key="stat.key"
        class="bg-white p-6 rounded-xl border border-gray-200 shadow-sm transition-all hover:shadow-md hover:border-blue-200 group"
      >
        <div class="flex items-center justify-between mb-4">
          <div class="p-2.5 rounded-lg transition-transform group-hover:scale-110" :class="[stat.bg, stat.color]">
            <component :is="stat.icon" :size="20" />
          </div>
        </div>
        <div>
          <p class="text-[10px] font-bold text-gray-400 uppercase tracking-widest">{{ stat.label }}</p>
          <h3 class="text-3xl font-light text-gray-900 mt-1">
            {{ stats[stat.key] || 0 }}{{ stat.suffix || '' }}
          </h3>
        </div>
      </div>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <!-- Trend Chart -->
      <div class="lg:col-span-2 bg-white border border-gray-200 rounded-xl shadow-sm p-6">
        <div class="flex items-center justify-between mb-6">
          <h3 class="font-semibold text-gray-900">近 7 天任务趋势</h3>
          <TrendingUp :size="16" class="text-gray-400" />
        </div>
        <div v-if="stats.trend?.length" class="flex items-end gap-2 h-40">
          <div v-for="day in stats.trend" :key="day.date" class="flex-1 flex flex-col items-center gap-2">
            <span class="text-[10px] font-bold text-gray-400">{{ day.count }}</span>
            <div class="w-full bg-blue-100 rounded-t-md transition-all"
              :style="{ height: Math.max((day.count / Math.max(...stats.trend.map((t:any)=>t.count), 1)) * 100, 4) + '%' }">
              <div class="w-full h-full bg-blue-500 rounded-t-md opacity-80"></div>
            </div>
            <span class="text-[10px] text-gray-400 font-mono">{{ day.date }}</span>
          </div>
        </div>
        <div v-else class="flex items-center justify-center h-40 text-gray-400 text-sm">
          暂无趋势数据
        </div>
      </div>

      <!-- Status Distribution -->
      <div class="bg-white border border-gray-200 rounded-xl shadow-sm p-6">
        <h3 class="font-semibold text-gray-900 mb-4">状态分布</h3>
        <div class="space-y-3">
          <div v-for="(count, status) in stats.tasks_by_status" :key="status"
            class="flex items-center justify-between p-3 bg-gray-50 rounded-lg border border-gray-100">
            <StatusBadge :status="String(status)" />
            <span class="text-lg font-bold text-gray-900">{{ count }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Recent Activity -->
    <div class="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">
      <div class="px-6 py-4 border-b border-gray-100 flex items-center justify-between">
        <h3 class="font-semibold text-gray-900">最近执行记录</h3>
        <button @click="router.push('/history')" class="text-xs font-bold text-blue-600 hover:underline px-3 py-1">
          查看全部
        </button>
      </div>
      <div class="overflow-x-auto">
        <table class="w-full text-left text-sm">
          <thead>
            <tr class="bg-gray-50 text-gray-500 border-b border-gray-100">
              <th class="px-6 py-3 font-semibold uppercase tracking-wider text-[10px]">任务</th>
              <th class="px-6 py-3 font-semibold uppercase tracking-wider text-[10px]">类型</th>
              <th class="px-6 py-3 font-semibold uppercase tracking-wider text-[10px]">状态</th>
              <th class="px-6 py-3 font-semibold uppercase tracking-wider text-[10px]">时间</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-100">
            <tr
              v-for="task in stats.recent_tasks"
              :key="task.id"
              @click="router.push(`/runs/${task.id}`)"
              class="hover:bg-gray-50 transition-colors cursor-pointer"
            >
              <td class="px-6 py-4">
                <div class="flex items-center gap-3">
                  <div class="p-1.5 rounded-lg bg-blue-50 text-blue-600">
                    <Beaker :size="14" />
                  </div>
                  <span class="font-medium text-gray-900 truncate max-w-xs block">{{ task.objective }}</span>
                </div>
              </td>
              <td class="px-6 py-4 text-gray-500 text-xs font-medium uppercase">{{ task.test_type }}</td>
              <td class="px-6 py-4"><StatusBadge :status="task.status" /></td>
              <td class="px-6 py-4 text-gray-400 text-xs">{{ task.created_at ? new Date(task.created_at).toLocaleString('zh-CN') : '' }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="!stats.recent_tasks?.length" class="text-center text-gray-400 text-sm py-12">暂无任务记录</p>
      </div>
    </div>
    </template>
  </div>
</template>
