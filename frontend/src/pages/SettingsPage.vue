<script setup lang="ts">
import { useRouter } from 'vue-router'
import { Cpu, Database, Globe, BookOpen, Layers, Settings2 } from 'lucide-vue-next'

const router = useRouter()

const settingsLinks = [
  { path: '/providers', label: '模型管理', desc: '配置 LLM 提供商和 API Key', icon: Cpu },
  { path: '/documents', label: '文档管理', desc: '管理导入的 API 文档', icon: Database },
  { path: '/environments', label: '环境管理', desc: '配置测试环境变量', icon: Globe },
  { path: '/test-cases', label: '用例库', desc: '查看所有生成的测试用例', icon: Layers },
  { path: '/knowledge', label: '知识库', desc: '管理 RAG 知识库', icon: BookOpen },
]
</script>

<template>
  <div class="max-w-3xl mx-auto py-8 space-y-6">
    <div class="flex items-center gap-3">
      <Settings2 :size="24" class="text-gray-400" />
      <div>
        <h2 class="text-2xl font-bold tracking-tight text-gray-900">系统设置</h2>
        <p class="text-gray-500 text-sm">管理模型、环境和文档配置。</p>
      </div>
    </div>

    <div class="space-y-3">
      <div
        v-for="link in settingsLinks"
        :key="link.path"
        @click="router.push(link.path)"
        class="bg-white border border-gray-200 rounded-lg p-5 shadow-sm hover:border-blue-200 transition-all cursor-pointer group flex items-center gap-4"
      >
        <div class="p-3 rounded-lg bg-gray-50 text-gray-400 group-hover:bg-blue-50 group-hover:text-blue-600 transition-all">
          <component :is="link.icon" :size="20" />
        </div>
        <div class="flex-1">
          <div class="font-bold text-gray-900 text-sm">{{ link.label }}</div>
          <div class="text-xs text-gray-500 mt-0.5">{{ link.desc }}</div>
        </div>
      </div>
    </div>
  </div>
</template>
