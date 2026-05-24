<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../lib/api'
import { useToast } from '../composables/useToast'
import LoadingSpinner from '../components/LoadingSpinner.vue'
import { Settings2, Save, Plus } from 'lucide-vue-next'

const router = useRouter()
const toast = useToast()
const loading = ref(false)
const providers = ref<any[]>([])
const selected = ref<any>(null)
const form = reactive({
  system_prompt: '',
  agent_type: 'explorer',
  temperature: 0.2,
  max_tokens: 4096,
})

const agentTypes = [
  { value: 'explorer', label: '探索型', desc: '广泛覆盖，发现更多潜在问题' },
  { value: 'boundary', label: '边界型', desc: '聚焦边界值和异常场景' },
  { value: 'performance', label: '性能型', desc: '关注响应时间和资源消耗' },
]

async function fetchProviders() {
  loading.value = true
  try {
    const { data } = await api.get('/providers')
    providers.value = data
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '加载模型列表失败')
  } finally {
    loading.value = false
  }
}

function selectProvider(p: any) {
  selected.value = p
  form.system_prompt = p.system_prompt || ''
  form.agent_type = p.agent_type || 'explorer'
  form.temperature = p.temperature ?? 0.2
  form.max_tokens = p.max_tokens ?? 4096
}

async function saveConfig() {
  if (!selected.value) return
  try {
    await api.put(`/providers/${selected.value.id}/config`, form)
    toast.success('Agent 配置保存成功')
    await fetchProviders()
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '保存 Agent 配置失败')
  }
}

onMounted(fetchProviders)
</script>

<template>
  <div class="space-y-8 pb-12">
    <div class="flex flex-col gap-1">
      <h2 class="text-2xl font-bold tracking-tight text-gray-900">AI Agent 配置</h2>
      <p class="text-gray-500 text-sm">配置大模型参数、System Prompt 和 Agent 策略。</p>
    </div>

    <LoadingSpinner v-if="loading" text="加载模型列表中..." />

    <div v-else class="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
      <!-- Provider List -->
      <div class="lg:col-span-4 space-y-4">
        <div class="flex items-center justify-between">
          <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest">选择模型</h3>
          <button @click="router.push('/providers')" class="flex items-center gap-1.5 px-3 py-1.5 bg-gray-950 hover:bg-gray-800 text-white rounded-lg text-xs font-bold transition-all">
            <Plus :size="12" /> 添加模型
          </button>
        </div>
        <div class="space-y-3">
          <button
            v-for="p in providers" :key="p.id"
            @click="selectProvider(p)"
            class="w-full text-left bg-white border rounded-lg p-4 shadow-sm transition-all"
            :class="selected?.id === p.id ? 'border-blue-300 bg-blue-50' : 'border-gray-200 hover:border-blue-200'"
          >
            <div class="font-bold text-gray-900 text-sm">{{ p.name }}</div>
            <div class="text-xs text-gray-500 mt-0.5">{{ p.type }} / {{ p.model_name }}</div>
            <div class="flex flex-wrap gap-1.5 mt-2">
              <span v-if="p.is_default_coder" class="px-2 py-0.5 bg-blue-50 text-blue-600 rounded text-[10px] font-bold border border-blue-100">Coder</span>
              <span v-if="p.is_default_planner" class="px-2 py-0.5 bg-emerald-50 text-emerald-600 rounded text-[10px] font-bold border border-emerald-100">Planner</span>
              <span v-if="p.agent_type" class="px-2 py-0.5 bg-purple-50 text-purple-600 rounded text-[10px] font-bold border border-purple-100">{{ p.agent_type }}</span>
            </div>
          </button>
          <p v-if="!providers.length" class="text-center text-gray-400 text-sm py-8">暂无模型，请先在模型配置中添加</p>
        </div>
      </div>

      <!-- Config Panel -->
      <div class="lg:col-span-8 space-y-6">
        <div v-if="selected" class="bg-white border border-gray-200 rounded-lg shadow-sm p-6 space-y-6">
          <div class="flex items-center justify-between">
            <h3 class="text-xs font-bold text-gray-400 uppercase tracking-widest">配置: {{ selected.name }}</h3>
            <button @click="saveConfig" class="px-4 py-2 bg-gray-950 hover:bg-gray-800 text-white rounded-lg text-sm font-bold transition-all flex items-center gap-2">
              <Save :size="14" /> 保存配置
            </button>
          </div>

          <!-- Agent Type -->
          <div>
            <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-3">Agent 类型</label>
            <div class="grid grid-cols-3 gap-3">
              <button
                v-for="at in agentTypes" :key="at.value"
                @click="form.agent_type = at.value"
                class="p-4 border rounded-lg text-left transition-all"
                :class="form.agent_type === at.value ? 'border-blue-300 bg-blue-50' : 'border-gray-200 hover:border-blue-200'"
              >
                <div class="font-bold text-sm" :class="form.agent_type === at.value ? 'text-blue-700' : 'text-gray-900'">{{ at.label }}</div>
                <div class="text-xs text-gray-500 mt-1">{{ at.desc }}</div>
              </button>
            </div>
          </div>

          <!-- System Prompt -->
          <div>
            <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">System Prompt</label>
            <textarea v-model="form.system_prompt" rows="8" placeholder="定义 Agent 的行为策略和测试风格..."
              class="w-full px-4 py-3 bg-gray-50 border border-gray-200 rounded-lg text-sm font-mono outline-none focus:border-blue-500 focus:bg-white transition-all resize-none" />
          </div>

          <!-- Hyperparameters -->
          <div class="grid grid-cols-2 gap-6">
            <div>
              <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">
                Temperature: {{ form.temperature }}
              </label>
              <input type="range" v-model.number="form.temperature" min="0" max="1" step="0.1"
                class="w-full accent-blue-600" />
              <div class="flex justify-between text-[10px] text-gray-400 mt-1">
                <span>精确</span><span>创意</span>
              </div>
            </div>
            <div>
              <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-1.5">Max Tokens</label>
              <input v-model.number="form.max_tokens" type="number" min="256" max="32768"
                class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all" />
            </div>
          </div>
        </div>

        <div v-else class="bg-white border border-gray-200 rounded-lg shadow-sm p-12 text-center">
          <Settings2 :size="48" class="mx-auto text-gray-300 mb-4" />
          <p class="text-gray-400 text-sm">请从左侧选择一个模型进行配置</p>
        </div>
      </div>
    </div>
  </div>
</template>
