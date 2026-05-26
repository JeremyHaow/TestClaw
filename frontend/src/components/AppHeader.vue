<script setup lang="ts">
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { History, LogOut, Menu, ShieldCheck } from 'lucide-vue-next'
import { computed } from 'vue'
import { useSidebar } from '../composables/useSidebar'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const { isMobile, toggleMobile } = useSidebar()

const sectionLabel = computed(() => {
  if (route.path.startsWith('/runs/')) return '运行监督'
  if (route.path === '/agent-plan') return '智能计划'
  if (route.path === '/run') return '任务委派'
  if (route.path === '/history') return '运行历史'
  if (route.path === '/quality-memory') return '质量记忆'
  if (['/documents', '/environments', '/test-cases'].some((path) => route.path.startsWith(path))) return '资产'
  if (['/providers', '/knowledge'].some((path) => route.path.startsWith(path))) return '设置'
  return '工作区'
})

const accountLabel = computed(() => auth.user?.username || 'admin')

function goTo(path: string) {
  router.push(path)
}

function logout() {
  auth.logout()
  router.push('/login')
}
</script>

<template>
  <header class="h-16 border-b border-[#E5EAF3] bg-white/90 px-4 lg:px-6 flex items-center justify-between gap-3 shrink-0 z-20 backdrop-blur">
    <div class="flex min-w-0 items-center gap-3">
      <button
        v-if="isMobile"
        type="button"
        aria-label="打开导航菜单"
        @click="toggleMobile"
        class="p-2 -ml-2 text-slate-600 hover:bg-blue-50 hover:text-blue-700 rounded-lg transition-colors"
      >
        <Menu :size="20" />
      </button>
      <div class="flex min-w-0 flex-col">
        <div class="flex min-w-0 items-center gap-2 text-[11px] font-bold uppercase text-slate-400">
          <span class="hidden sm:inline">TestClaw</span>
          <span class="hidden h-3 w-px bg-slate-200 sm:inline-block" />
          <span>Workspace</span>
        </div>
        <div class="truncate text-sm font-semibold text-slate-950">{{ sectionLabel }}</div>
      </div>
    </div>
    <div class="flex shrink-0 items-center gap-2">
      <button
        type="button"
        @click="goTo('/history')"
        class="inline-flex h-9 items-center justify-center gap-1.5 rounded-lg border border-blue-100 bg-blue-50 px-2.5 text-xs font-bold text-blue-700 transition-colors hover:border-blue-200 hover:bg-blue-100 sm:px-3"
      >
        <History :size="14" />
        <span class="hidden sm:inline">历史</span>
      </button>
      <div class="hidden h-6 w-px bg-slate-200 sm:block" />
      <div class="flex items-center gap-2 min-w-0">
        <span class="hidden h-9 items-center gap-1.5 rounded-lg border border-emerald-200 bg-emerald-50 px-2.5 text-[11px] font-bold text-emerald-700 sm:flex">
          <ShieldCheck :size="14" /> 已认证
        </span>
        <span class="hidden max-w-36 truncate rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-xs font-semibold text-slate-700 md:inline">
          {{ accountLabel }}
        </span>
        <button
          type="button"
          aria-label="退出登录"
          @click="logout"
          class="inline-flex h-9 shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-bold text-slate-700 transition-colors hover:bg-slate-50 hover:text-slate-950 sm:px-3 lg:px-4"
        >
          <LogOut :size="14" />
          <span class="hidden sm:inline">退出</span>
        </button>
      </div>
    </div>
  </header>
</template>
