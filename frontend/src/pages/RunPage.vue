<script setup lang="ts">
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import api from '../lib/api'
import { useToast } from '../composables/useToast'
import { Play, Zap, Globe, FileJson } from 'lucide-vue-next'

const router = useRouter()
const toast = useToast()
const submitting = ref(false)

const form = reactive({
  source: '',
  test_type: 'auto',
  objective: '',
})

function detectInputType(source: string): string {
  const s = source.trim()
  if (s.startsWith('{') || s.startsWith('[')) return 'Swagger JSON'
  if (s.startsWith('openapi:') || s.startsWith('swagger:')) return 'Swagger YAML'
  if (/https?:\/\//.test(s)) {
    if (/swagger|openapi|api-docs/i.test(s)) return 'Swagger URL'
    return '网页 URL'
  }
  return '文本输入'
}

const inputTypeLabel = ref('')

function onInputChange() {
  inputTypeLabel.value = form.source.trim() ? detectInputType(form.source) : ''
}

async function submit() {
  if (!form.source.trim()) {
    toast.warning('请输入 URL 或 Swagger 文档')
    return
  }
  submitting.value = true
  try {
    const { data } = await api.post('/runs', {
      source: form.source.trim(),
      test_type: form.test_type,
      objective: form.objective || undefined,
    })
    toast.success('测试运行已创建')
    router.push(`/runs/${data.id}`)
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '创建运行失败')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="max-w-3xl mx-auto py-12 space-y-8">
    <!-- Header -->
    <div class="text-center space-y-2">
      <h1 class="text-3xl font-bold tracking-tight text-gray-900">AI 自动化测试</h1>
      <p class="text-gray-500">输入 URL 或 Swagger 文档，Agent 自动完成测试计划、用例生成、API/UI 测试与报告</p>
    </div>

    <!-- Input Card -->
    <div class="bg-white border border-gray-200 rounded-2xl shadow-sm p-8 space-y-6">
      <!-- Source Input -->
      <div>
        <label class="text-xs font-bold text-gray-400 uppercase tracking-widest block mb-2">输入源</label>
        <textarea
          v-model="form.source"
          @input="onInputChange"
          rows="4"
          placeholder="粘贴 URL、Swagger URL 或 Swagger JSON/YAML..."
          class="w-full px-4 py-3 bg-gray-50 border border-gray-200 rounded-xl text-sm font-mono outline-none focus:border-blue-500 focus:bg-white transition-all resize-none"
        />
        <div v-if="inputTypeLabel" class="mt-2 flex items-center gap-2">
          <span class="px-2 py-0.5 bg-blue-50 text-blue-600 rounded text-[10px] font-bold">{{ inputTypeLabel }}</span>
        </div>
      </div>

      <!-- Quick Examples -->
      <div class="flex flex-wrap gap-2">
        <span class="text-[10px] font-bold text-gray-400 uppercase tracking-widest self-center mr-1">示例：</span>
        <button
          @click="form.source = 'https://petstore.swagger.io/v2/swagger.json'; onInputChange()"
          class="px-3 py-1 bg-gray-100 hover:bg-gray-200 text-gray-600 rounded-lg text-xs font-medium transition-all flex items-center gap-1"
        >
          <FileJson :size="12" /> Petstore Swagger
        </button>
        <button
          @click="form.source = 'https://httpbin.org'; onInputChange()"
          class="px-3 py-1 bg-gray-100 hover:bg-gray-200 text-gray-600 rounded-lg text-xs font-medium transition-all flex items-center gap-1"
        >
          <Globe :size="12" /> httpbin.org
        </button>
      </div>

      <!-- Test Type -->
      <div>
        <label class="text-xs font-bold text-gray-400 uppercase tracking-widest block mb-2">测试模式</label>
        <div class="flex gap-3">
          <button
            v-for="mode in [
              { value: 'auto', label: '自动', desc: '根据输入类型自动选择', icon: Zap },
              { value: 'api', label: 'API', desc: '仅 API 接口测试', icon: Zap },
              { value: 'ui', label: 'UI', desc: '仅 UI 自动化测试', icon: Globe },
            ]"
            :key="mode.value"
            @click="form.test_type = mode.value"
            class="flex-1 p-4 rounded-xl border-2 transition-all text-left"
            :class="form.test_type === mode.value
              ? 'border-blue-500 bg-blue-50'
              : 'border-gray-200 hover:border-gray-300 bg-white'"
          >
            <div class="flex items-center gap-2 mb-1">
              <component :is="mode.icon" :size="16" :class="form.test_type === mode.value ? 'text-blue-600' : 'text-gray-400'" />
              <span class="font-bold text-sm" :class="form.test_type === mode.value ? 'text-blue-700' : 'text-gray-700'">{{ mode.label }}</span>
            </div>
            <p class="text-[11px] text-gray-500">{{ mode.desc }}</p>
          </button>
        </div>
      </div>

      <!-- Objective (optional) -->
      <div>
        <label class="text-xs font-bold text-gray-400 uppercase tracking-widest block mb-2">测试目标（可选）</label>
        <input
          v-model="form.objective"
          placeholder="例如：验证登录流程的完整性和安全性"
          class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-xl text-sm outline-none focus:border-blue-500 focus:bg-white transition-all"
        />
      </div>

      <!-- Submit -->
      <button
        @click="submit"
        :disabled="submitting || !form.source.trim()"
        class="w-full py-3.5 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed text-white rounded-xl text-sm font-bold transition-all shadow-md shadow-blue-600/10 active:scale-[0.98] flex items-center justify-center gap-2"
      >
        <Play :size="16" />
        {{ submitting ? '正在创建...' : '开始自动测试' }}
      </button>
    </div>

    <!-- How it works -->
    <div class="grid grid-cols-4 gap-4">
      <div v-for="(step, i) in [
        { title: '输入', desc: 'URL 或 Swagger 文档' },
        { title: '解析', desc: '自动识别并解析接口' },
        { title: '测试', desc: 'API + UI 自动化执行' },
        { title: '报告', desc: '结构化结果与分析' },
      ]" :key="i"
        class="text-center p-4 bg-white border border-gray-200 rounded-xl"
      >
        <div class="w-8 h-8 mx-auto mb-2 bg-blue-100 text-blue-600 rounded-full flex items-center justify-center text-sm font-bold">{{ i + 1 }}</div>
        <div class="font-bold text-sm text-gray-900">{{ step.title }}</div>
        <div class="text-[11px] text-gray-500 mt-0.5">{{ step.desc }}</div>
      </div>
    </div>
  </div>
</template>
