<script setup lang="ts">
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { Search, Bell, HelpCircle, Menu } from 'lucide-vue-next'
import { computed } from 'vue'
import { useSidebar } from '../composables/useSidebar'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const { isMobile, toggleMobile } = useSidebar()

const pageTitle = computed(() => {
  const titles: Record<string, string> = {
    '/run': '开始测试',
    '/history': '历史记录',
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
  if (route.path.startsWith('/runs/')) return '运行详情'
  return 'TestClaw'
})

function logout() {
  auth.logout()
  router.push('/login')
}
</script>

<template>
  <header class="h-16 border-b border-gray-200 bg-white px-4 lg:px-8 flex items-center justify-between shrink-0 z-20">
    <div class="flex items-center gap-3">
      <button
        v-if="isMobile"
        @click="toggleMobile"
        class="p-2 -ml-2 text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
      >
        <Menu :size="20" />
      </button>
      <h1 class="text-lg font-semibold text-gray-900">{{ pageTitle }}</h1>
    </div>
    <div class="flex items-center gap-4 lg:gap-6">
      <div class="hidden lg:flex items-center gap-3 bg-gray-100 px-4 py-1.5 rounded-lg border border-gray-200 w-80">
        <Search :size="16" class="text-gray-400" />
        <input type="text" placeholder="搜索资源..." class="bg-transparent border-none outline-none text-sm w-full placeholder:text-gray-500" />
      </div>
      <div class="flex items-center gap-2">
        <button class="p-2 text-gray-400 hover:bg-gray-100 hover:text-gray-900 rounded-lg transition-all relative">
          <Bell :size="18" />
          <span class="absolute top-2 right-2 w-1.5 h-1.5 bg-blue-600 rounded-full border-2 border-white" />
        </button>
        <button class="hidden sm:block p-2 text-gray-400 hover:bg-gray-100 hover:text-gray-900 rounded-lg transition-all">
          <HelpCircle :size="18" />
        </button>
        <div class="h-6 w-px bg-gray-200 mx-1 lg:mx-2" />
        <span class="hidden sm:inline text-[10px] font-mono bg-emerald-50 text-emerald-700 px-2 py-0.5 rounded border border-emerald-200 font-bold tracking-tight">AGENT ONLINE</span>
        <button
          @click="logout"
          class="px-3 lg:px-4 py-1.5 bg-gray-900 hover:bg-black text-white text-xs font-bold rounded-md shadow-sm transition-all"
        >
          退出
        </button>
      </div>
    </div>
  </header>
</template>
