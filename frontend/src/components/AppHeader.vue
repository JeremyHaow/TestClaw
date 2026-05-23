<script setup lang="ts">
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { Bot, Menu } from 'lucide-vue-next'
import { computed } from 'vue'
import { useSidebar } from '../composables/useSidebar'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const { isMobile, toggleMobile } = useSidebar()

const pageTitle = computed(() => {
  const titles: Record<string, string> = {
    '/run': 'Testing Agent Workspace',
    '/history': '运行历史',
    '/quality-memory': '质量记忆',
    '/settings': '系统设置',
    '/providers': '模型管理',
    '/documents': '文档管理',
    '/environments': '环境管理',
    '/test-cases': '用例库',
    '/knowledge': '知识库',
  }
  for (const [path, title] of Object.entries(titles)) {
    if (route.path === path || route.path.startsWith(path + '/')) return title
  }
  if (route.path.startsWith('/runs/')) return 'Agent Cockpit'
  return 'TestClaw'
})

function logout() {
  auth.logout()
  router.push('/login')
}
</script>

<template>
  <header class="h-16 border-b border-gray-200 bg-white px-4 lg:px-8 flex items-center justify-between gap-3 shrink-0 z-20">
    <div class="flex min-w-0 items-center gap-3">
      <button
        v-if="isMobile"
        @click="toggleMobile"
        class="p-2 -ml-2 text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
      >
        <Menu :size="20" />
      </button>
      <h1 class="truncate text-lg font-semibold text-gray-900">{{ pageTitle }}</h1>
    </div>
    <div class="flex shrink-0 items-center gap-3 lg:gap-4">
      <div class="flex items-center gap-2">
        <span class="hidden items-center gap-1.5 rounded-lg border border-gray-200 bg-gray-50 px-2.5 py-1 text-[11px] font-bold text-gray-600 sm:flex">
          <Bot :size="14" class="text-blue-600" /> Testing Agent
        </span>
        <div class="h-6 w-px bg-gray-200 mx-1 lg:mx-2" />
        <span v-if="auth.user" class="hidden text-xs font-bold text-gray-500 md:inline">{{ auth.user.username }}</span>
        <button
          @click="logout"
          class="shrink-0 whitespace-nowrap px-3 py-1.5 bg-gray-900 hover:bg-black text-white text-xs font-bold rounded-md shadow-sm transition-all lg:px-4"
        >
          退出
        </button>
      </div>
    </div>
  </header>
</template>
