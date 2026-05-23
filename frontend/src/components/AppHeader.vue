<script setup lang="ts">
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { History, LogOut, Menu, Play, ShieldCheck } from 'lucide-vue-next'
import { computed } from 'vue'
import { useSidebar } from '../composables/useSidebar'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const { isMobile, toggleMobile } = useSidebar()

const sectionLabel = computed(() => {
  if (route.path.startsWith('/runs/')) return '运行监督'
  if (route.path === '/run') return '任务委派'
  if (route.path === '/history' || route.path === '/quality-memory') return '质量记忆'
  if (['/providers', '/documents', '/environments', '/test-cases', '/knowledge'].some((path) => route.path.startsWith(path))) return '资产与配置'
  return '工作区'
})

const showRunAction = computed(() => route.path !== '/run')
const showHistoryAction = computed(() => route.path !== '/history')

function goTo(path: string) {
  router.push(path)
}

function logout() {
  auth.logout()
  router.push('/login')
}
</script>

<template>
  <header class="h-14 border-b border-gray-200 bg-white px-4 lg:px-6 flex items-center justify-between gap-3 shrink-0 z-20">
    <div class="flex min-w-0 items-center gap-3">
      <button
        v-if="isMobile"
        type="button"
        aria-label="打开导航菜单"
        @click="toggleMobile"
        class="p-2 -ml-2 text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
      >
        <Menu :size="20" />
      </button>
      <div class="flex min-w-0 items-center gap-2 text-xs text-gray-500">
        <span class="hidden font-bold text-gray-900 sm:inline">TestClaw</span>
        <span class="hidden h-4 w-px bg-gray-200 sm:inline-block" />
        <span class="truncate font-semibold">{{ sectionLabel }}</span>
      </div>
    </div>
    <div class="flex shrink-0 items-center gap-2">
      <button
        v-if="showRunAction"
        type="button"
        @click="goTo('/run')"
        class="hidden items-center justify-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-bold text-gray-700 transition-colors hover:border-blue-200 hover:bg-blue-50 hover:text-blue-700 sm:inline-flex"
      >
        <Play :size="14" /> 新建运行
      </button>
      <button
        v-if="showHistoryAction"
        type="button"
        @click="goTo('/history')"
        class="hidden items-center justify-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-bold text-gray-700 transition-colors hover:border-blue-200 hover:bg-blue-50 hover:text-blue-700 md:inline-flex"
      >
        <History :size="14" /> 历史
      </button>
      <div class="hidden h-6 w-px bg-gray-200 md:block" />
      <div class="flex items-center gap-2 min-w-0">
        <span class="hidden items-center gap-1.5 rounded-lg border border-emerald-100 bg-emerald-50 px-2.5 py-1 text-[11px] font-bold text-emerald-700 sm:flex">
          <ShieldCheck :size="14" /> 已登录
        </span>
        <span v-if="auth.user" class="hidden max-w-32 truncate text-xs font-bold text-gray-500 md:inline">{{ auth.user.username }}</span>
        <button
          type="button"
          aria-label="退出登录"
          @click="logout"
          class="inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg bg-gray-900 px-3 py-1.5 text-xs font-bold text-white transition-colors hover:bg-black lg:px-4"
        >
          <LogOut :size="14" /> 退出
        </button>
      </div>
    </div>
  </header>
</template>
