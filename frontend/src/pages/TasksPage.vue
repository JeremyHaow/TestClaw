<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import api from '../lib/api'
import StatusBadge from '../components/StatusBadge.vue'
import LoadingSpinner from '../components/LoadingSpinner.vue'
import EmptyState from '../components/EmptyState.vue'
import SearchInput from '../components/SearchInput.vue'
import Pagination from '../components/Pagination.vue'
import StyledSelect from '../components/StyledSelect.vue'
import { useTaskStore } from '../stores/tasks'
import { useToast } from '../composables/useToast'
import { Plus, Beaker } from 'lucide-vue-next'

const router = useRouter()
const tasks = useTaskStore()
const toast = useToast()
const documents = ref<any[]>([])
const environments = ref<any[]>([])
const showCreate = ref(false)
const loading = ref(false)
const search = ref('')
const page = ref(1)
const pageSize = 15
const total = ref(0)

const form = reactive({
  objective: '',
  target_url: '',
  test_type: 'full',
  api_doc_id: '',
  environment_id: '',
})

async function fetchItems() {
  loading.value = true
  try {
    const params: any = { page: page.value, page_size: pageSize }
    if (search.value) params.search = search.value
    const { data } = await api.get('/tasks', { params })
    tasks.items = Array.isArray(data) ? data : data.items || []
    total.value = Array.isArray(data) ? data.length : data.total || tasks.items.length
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '加载任务列表失败')
  } finally {
    loading.value = false
  }
}

watch([search], () => {
  page.value = 1
  fetchItems()
})

watch(page, () => {
  fetchItems()
})

onMounted(async () => {
  fetchItems()
  try {
    const [docs, envs] = await Promise.all([api.get('/documents'), api.get('/environments')])
    documents.value = docs.data
    environments.value = envs.data
  } catch {
    toast.warning('加载关联数据失败')
  }
})

async function submit() {
  const payload: any = { objective: form.objective, target_url: form.target_url, test_type: form.test_type }
  if (form.api_doc_id) payload.api_doc_id = form.api_doc_id
  if (form.environment_id) payload.environment_id = form.environment_id
  try {
    const created = await tasks.createTask(payload)
    form.objective = ''
    form.target_url = ''
    form.test_type = 'full'
    form.api_doc_id = ''
    form.environment_id = ''
    showCreate.value = false
    toast.success('任务创建成功')
    router.push(`/tasks/${created.id}`)
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '创建任务失败')
  }
}
</script>

<template>
  <div class="space-y-8 pb-12">
    <div class="flex items-center justify-between">
      <div class="flex flex-col gap-1">
        <h2 class="text-2xl font-bold tracking-tight text-gray-900">任务中心</h2>
        <p class="text-gray-500 text-sm">创建和管理 AI 测试任务，查看执行状态与结果。</p>
      </div>
      <div class="flex gap-2">
        <button
          @click="showCreate = !showCreate"
          class="flex items-center gap-2 px-5 py-2 bg-gray-950 hover:bg-gray-800 text-white rounded-lg text-xs font-bold transition-all shadow-md shadow-blue-600/10 active:scale-95"
        >
          <Plus :size="14" /> 新建任务
        </button>
      </div>
    </div>

    <!-- Create Form -->
    <div v-if="showCreate" class="bg-white border border-gray-200 rounded-lg shadow-sm p-6">
      <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4">创建新任务</h3>
      <form class="grid grid-cols-1 md:grid-cols-2 gap-4" @submit.prevent="submit">
        <div class="md:col-span-2">
          <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">测试目标</label>
          <input v-model="form.objective" placeholder="例如：校验登录流程" required
            class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all" />
        </div>
        <div>
          <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">目标地址</label>
          <input v-model="form.target_url" placeholder="https://example.com" required
            class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all" />
        </div>
        <div>
          <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">测试类型</label>
          <StyledSelect v-model="form.test_type">
            <option value="full">full</option>
            <option value="ui">ui</option>
            <option value="api">api</option>
            <option value="functional">functional</option>
          </StyledSelect>
        </div>
        <div>
          <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">关联文档</label>
          <StyledSelect v-model="form.api_doc_id">
            <option value="">不关联</option>
            <option v-for="doc in documents" :key="doc.id" :value="doc.id">{{ doc.name || `Document-${doc.format}` }}</option>
          </StyledSelect>
        </div>
        <div>
          <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">执行环境</label>
          <StyledSelect v-model="form.environment_id">
            <option value="">不关联</option>
            <option v-for="env in environments" :key="env.id" :value="env.id">{{ env.name }}</option>
          </StyledSelect>
        </div>
        <div class="md:col-span-2 flex gap-3">
          <button type="submit" class="px-6 py-2.5 bg-gray-950 hover:bg-gray-800 text-white rounded-lg text-sm font-bold transition-all shadow-md shadow-blue-600/10">
            创建任务
          </button>
          <button type="button" @click="showCreate = false" class="px-6 py-2.5 bg-white border border-gray-200 hover:bg-gray-50 text-gray-700 rounded-lg text-sm font-bold transition-all">
            取消
          </button>
        </div>
      </form>
    </div>

    <!-- Task List -->
    <div class="bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden">
      <div class="px-6 py-4 border-b border-gray-100 flex items-center justify-between">
        <h3 class="font-semibold text-gray-900">任务列表</h3>
        <span class="text-xs text-gray-400 font-mono">{{ total }} 条记录</span>
      </div>
      <div class="px-6 py-4 border-b border-gray-100">
        <SearchInput v-model="search" placeholder="搜索任务..." />
      </div>
      <LoadingSpinner v-if="loading" text="加载中..." />
      <template v-else>
        <EmptyState v-if="!tasks.items.length" title="暂无任务" description="还没有创建任何测试任务，点击右上角新建任务开始吧。" />
        <template v-else>
          <div class="overflow-x-auto">
            <table class="w-full text-left text-sm">
              <thead>
                <tr class="bg-gray-50 text-gray-500 border-b border-gray-100">
                  <th class="px-6 py-3 font-semibold uppercase tracking-wider text-[10px]">测试目标</th>
                  <th class="px-6 py-3 font-semibold uppercase tracking-wider text-[10px]">类型</th>
                  <th class="px-6 py-3 font-semibold uppercase tracking-wider text-[10px]">目标地址</th>
                  <th class="px-6 py-3 font-semibold uppercase tracking-wider text-[10px]">状态</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-gray-100">
                <tr
                  v-for="task in tasks.items"
                  :key="task.id"
                  @click="router.push(`/tasks/${task.id}`)"
                  class="hover:bg-gray-50 transition-colors cursor-pointer"
                >
                  <td class="px-6 py-4">
                    <div class="flex items-center gap-3">
                      <div class="p-1.5 rounded-lg bg-blue-50 text-blue-600">
                        <Beaker :size="14" />
                      </div>
                      <span class="font-medium text-gray-900">{{ task.objective }}</span>
                    </div>
                  </td>
                  <td class="px-6 py-4 text-gray-500 text-xs font-medium uppercase">{{ task.test_type }}</td>
                  <td class="px-6 py-4 text-gray-400 font-mono text-xs truncate max-w-xs">{{ task.target_url }}</td>
                  <td class="px-6 py-4"><StatusBadge :status="task.status" /></td>
                </tr>
              </tbody>
            </table>
          </div>
          <Pagination :page="page" :page-size="pageSize" :total="total" @update:page="page = $event" />
        </template>
      </template>
    </div>
  </div>
</template>
