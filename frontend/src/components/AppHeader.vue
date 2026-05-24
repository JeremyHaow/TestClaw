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
  if (route.path === '/history') return '运行历史'
  if (route.path === '/quality-memory') return '质量记忆'
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
  <header class="h-16 border-b border-gray-200/80 bg-white/90 px-4 lg:px-6 flex items-center justify-between gap-3 shrink-0 z-20 backdrop-blur">
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
      <div class="flex min-w-0 flex-col">
        <div class="flex min-w-0 items-center gap-2 text-[11px] font-bold uppercase text-gray-400">
          <span class="hidden sm:inline">TestClaw</span>
          <span class="hidden h-3 w-px bg-gray-200 sm:inline-block" />
          <span>Workspace</span>
        </div>
        <div class="truncate text-sm font-semibold text-gray-950">{{ sectionLabel }}</div>
      </div>
    </div>
    <div class="flex shrink-0 items-center gap-2">
      <button
        v-if="showRunAction"
        type="button"
        @click="goTo('/run')"
        class="hidden items-center justify-center gap-1.5 rounded-lg bg-gray-950 px-3 py-2 text-xs font-bold text-white transition-colors hover:bg-gray-800 sm:inline-flex"
      >
        <Play :size="14" /> 新建运行
      </button>
      <button
        v-if="showHistoryAction"
        type="button"
        @click="goTo('/history')"
        class="hidden items-center justify-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-bold text-gray-700 transition-colors hover:border-gray-300 hover:bg-gray-50 md:inline-flex"
      >
        <History :size="14" /> 历史
      </button>
      <div class="hidden h-6 w-px bg-gray-200 md:block" />
      <div class="flex items-center gap-2 min-w-0">
        <span class="hidden items-center gap-1.5 rounded-lg border border-emerald-200 bg-emerald-50 px-2.5 py-1.5 text-[11px] font-bold text-emerald-700 sm:flex">
          <ShieldCheck :size="14" /> 已认证
        </span>
        <span v-if="auth.user" class="hidden max-w-36 truncate text-xs font-semibold text-gray-600 md:inline">{{ auth.user.username }}</span>
        <button
          type="button"
          aria-label="退出登录"
          @click="logout"
          class="inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-bold text-gray-700 transition-colors hover:bg-gray-50 hover:text-gray-950 lg:px-4"
        >
          <LogOut :size="14" /> 退出
        </button>
      </div>
    </div>
  </header>
</template>
