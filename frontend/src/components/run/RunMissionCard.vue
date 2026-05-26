<script setup lang="ts">
import { computed } from 'vue'
import type { PropType } from 'vue'
import { Globe } from 'lucide-vue-next'
import StyledSelect from '../StyledSelect.vue'

type RunForm = {
  source: string
  objective: string
}

type ApiDocument = {
  id: string
  name?: string | null
  source_url?: string | null
  raw_content?: string | null
  format?: string | null
  parsed_endpoints?: Record<string, any>[] | null
}

const props = defineProps({
  form: {
    type: Object as PropType<RunForm>,
    required: true,
  },
  isApiMode: {
    type: Boolean,
    required: true,
  },
  localInputType: {
    type: String,
    required: true,
  },
  documents: {
    type: Array as PropType<ApiDocument[]>,
    required: true,
  },
  documentsLoading: {
    type: Boolean,
    required: true,
  },
  selectedDocumentId: {
    type: String,
    required: true,
  },
  selectedDocument: {
    type: Object as PropType<ApiDocument | null>,
    default: null,
  },
  documentDisplayName: {
    type: Function as PropType<(doc: ApiDocument) => string>,
    required: true,
  },
  documentEndpointCount: {
    type: Function as PropType<(doc: ApiDocument) => number>,
    required: true,
  },
})

const emit = defineEmits<{
  (event: 'update:selectedDocumentId', value: string): void
  (event: 'reset-preflight'): void
  (event: 'document-selection'): void
  (event: 'source-input'): void
  (event: 'navigate-documents'): void
  (event: 'set-example', source: string, objective: string, mode: string): void
}>()

const selectedDocumentModel = computed({
  get: () => props.selectedDocumentId,
  set: (value: string) => emit('update:selectedDocumentId', value),
})
</script>

<template>
  <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
    <div class="mb-5 flex items-center justify-between gap-3">
      <div>
        <h3 class="text-sm font-bold text-gray-900">任务委派</h3>
        <p class="mt-1 text-xs text-gray-500">告诉智能体要测试什么，以及哪些行为被允许。</p>
      </div>
      <span class="rounded bg-gray-100 px-2 py-1 text-[10px] font-bold text-gray-500">{{ localInputType }}</span>
    </div>

    <div class="space-y-5">
      <div>
        <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-gray-400">测试任务</label>
        <textarea
          v-model="form.objective"
          rows="3"
          placeholder="例如：验证登录、核心导航、搜索筛选和异常输入，不要删除真实数据。"
          class="w-full resize-none rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-sm outline-none transition-all focus:border-blue-500 focus:bg-white"
          @input="emit('reset-preflight')"
        />
      </div>

      <div>
        <div v-if="isApiMode" class="rounded-lg border border-gray-200 bg-gray-50 p-3">
          <div class="mb-3 flex items-center justify-between gap-3">
            <label class="text-xs font-bold uppercase text-gray-500">API 文档</label>
            <button
              type="button"
              class="shrink-0 rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-[11px] font-bold text-gray-700 transition-all hover:border-gray-300 hover:bg-gray-50"
              @click="emit('navigate-documents')"
            >
              接口文档
            </button>
          </div>
          <template v-if="documents.length || documentsLoading">
            <StyledSelect
              v-model="selectedDocumentModel"
              :disabled="documentsLoading || !documents.length"
              @change="emit('document-selection')"
            >
              <option value="" disabled>{{ documentsLoading ? '加载已保存接口文档...' : '请选择已导入接口文档' }}</option>
              <option
                v-for="doc in documents"
                :key="doc.id"
                :value="doc.id"
              >
                {{ documentDisplayName(doc) }} · {{ documentEndpointCount(doc) }} endpoints
              </option>
            </StyledSelect>
            <div v-if="selectedDocument" class="mt-3 rounded-lg border border-emerald-200 bg-white px-3 py-2 text-xs">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <span class="font-bold text-gray-950">{{ documentDisplayName(selectedDocument) }}</span>
                <span class="font-bold text-emerald-700">{{ documentEndpointCount(selectedDocument) }} endpoints</span>
              </div>
              <div class="mt-2 flex flex-wrap gap-2 text-[11px] font-bold text-emerald-700">
                <span class="rounded border border-emerald-100 bg-emerald-50 px-2 py-1">来源：已保存接口文档</span>
                <span class="rounded border border-emerald-100 bg-emerald-50 px-2 py-1">格式：{{ selectedDocument.format || 'openapi' }}</span>
              </div>
            </div>
            <p v-else class="mt-2 text-xs leading-5 text-gray-600">
              API 测试只从已保存接口文档中选择；新增、粘贴 URL 或导入原文请到“接口文档”页面完成。
            </p>
          </template>
          <div v-else class="rounded-lg border border-dashed border-gray-300 bg-white px-4 py-4 text-sm text-gray-700">
            <div class="font-bold">暂无已保存接口文档</div>
            <p class="mt-1 text-xs leading-5 text-gray-500">请先在“接口文档”页面导入 OpenAPI/Swagger 文档，再回到这里选择运行。</p>
            <button
              type="button"
              class="mt-3 rounded-lg bg-gray-950 px-3 py-2 text-xs font-bold text-white transition-all hover:bg-gray-800"
              @click="emit('navigate-documents')"
            >
              去导入接口文档
            </button>
          </div>
        </div>

        <template v-else>
          <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-gray-400">目标入口 / 页面 URL</label>
          <textarea
            v-model="form.source"
            rows="5"
            placeholder="粘贴要巡检的网页 URL..."
            class="w-full resize-none rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 font-mono text-sm outline-none transition-all focus:border-blue-500 focus:bg-white"
            @input="emit('source-input')"
          />
          <div class="mt-3 flex flex-wrap gap-2">
            <button
              class="flex items-center gap-1.5 rounded-lg bg-gray-100 px-3 py-1.5 text-xs font-bold text-gray-600 transition-all hover:bg-gray-200"
              @click="emit('set-example', 'https://httpbin.org', '对公开页面做基础可达性和页面结构巡检。', 'ui')"
            >
              <Globe :size="13" /> UI 巡检示例
            </button>
          </div>
        </template>
      </div>

      <slot />
    </div>
  </div>
</template>
