<script setup lang="ts">
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import {
  Bot,
  BrainCircuit,
  History,
  Cpu,
  FileCode,
  Globe,
  Layers,
  BookOpen,
  ChevronLeft,
  ChevronRight,
  SlidersHorizontal,
} from 'lucide-vue-next'
import { useSidebar } from '../composables/useSidebar'

const router = useRouter()
const route = useRoute()
const collapsed = ref(false)
const { mobileOpen, isMobile, closeMobile } = useSidebar()

const navGroups = [
  {
    label: 'Workspace',
    items: [
      { path: '/agent-plan', label: '智能计划', icon: Bot },
      { path: '/run', label: '任务委派', icon: SlidersHorizontal },
      { path: '/history', label: '运行历史', icon: History },
      { path: '/quality-memory', label: '质量记忆', icon: BrainCircuit },
    ],
  },
  {
    label: 'Assets',
    items: [
      { path: '/documents', label: '接口文档', icon: FileCode },
      { path: '/environments', label: '测试环境', icon: Globe },
      { path: '/test-cases', label: '用例资产', icon: Layers },
    ],
  },
  {
    label: 'Settings',
    items: [
      { path: '/providers', label: '模型与 Agent', icon: Cpu },
      { path: '/knowledge', label: 'RAG 知识库', icon: BookOpen },
    ],
  },
]

function isActive(path: string) {
  if (path === '/history' && route.path.startsWith('/runs/')) return true
  return route.path === path || route.path.startsWith(path + '/')
}

function navigate(path: string) {
  router.push(path)
  if (isMobile.value) closeMobile()
}
</script>

<template>
  <!-- Mobile backdrop -->
  <div
    v-if="isMobile && mobileOpen"
    @click="closeMobile"
    class="fixed inset-0 bg-black/40 z-30 lg:hidden"
  />

  <aside
    class="h-screen bg-white/95 border-r border-[#E5EAF3] flex flex-col z-40 transition-all duration-200"
    :class="[
      isMobile
        ? (mobileOpen ? 'fixed inset-y-0 left-0 w-64 shadow-xl' : 'hidden')
        : (collapsed ? 'sticky top-0 w-20' : 'sticky top-0 w-64')
    ]"
  >
    <!-- Logo -->
    <div class="h-16 border-b border-[#EEF2F7] px-4 flex items-center justify-between">
      <div v-if="!collapsed || isMobile" class="flex items-center gap-2 overflow-hidden">
        <div class="w-9 h-9 bg-blue-600 rounded-lg flex items-center justify-center shrink-0 text-white shadow-[0_8px_24px_rgba(37,99,235,0.18)]">
          <Bot :size="19" />
        </div>
        <div class="min-w-0">
          <span class="block truncate text-base font-semibold tracking-tight text-slate-950">TestClaw</span>
          <span class="block truncate text-[10px] font-bold uppercase text-slate-400">Testing Agent</span>
        </div>
      </div>
      <div v-if="collapsed && !isMobile" class="mx-auto w-9 h-9 bg-blue-600 rounded-lg flex items-center justify-center shrink-0 text-white shadow-[0_8px_24px_rgba(37,99,235,0.18)]">
        <Bot :size="19" />
      </div>
      <button
        v-if="!collapsed && !isMobile"
        type="button"
        aria-label="收起导航栏"
        title="收起导航栏"
        @click="collapsed = true"
        class="p-1.5 hover:bg-slate-100 rounded-lg transition-colors text-slate-400 hover:text-slate-700"
      >
        <ChevronLeft :size="18" />
      </button>
    </div>

    <!-- Main Nav -->
    <nav class="flex-1 px-3 py-4 space-y-5 overflow-y-auto overflow-x-hidden">
      <div v-for="group in navGroups" :key="group.label" class="space-y-1.5">
        <div v-if="!collapsed || isMobile" class="px-3 text-[10px] font-bold uppercase text-slate-400">
          {{ group.label }}
        </div>
        <button
          v-for="item in group.items"
          :key="item.path"
          type="button"
          :aria-label="item.label"
          :title="collapsed && !isMobile ? item.label : undefined"
          @click="navigate(item.path)"
          class="w-full flex items-center rounded-lg transition-all duration-200 group relative"
          :class="[
            isActive(item.path)
              ? 'bg-blue-600 text-white shadow-[0_8px_24px_rgba(37,99,235,0.18)]'
              : 'text-slate-600 hover:bg-blue-50 hover:text-slate-950',
            collapsed && !isMobile ? 'justify-center px-0 py-2.5' : 'gap-3 px-3 py-2.5',
          ]"
        >
          <component
            :is="item.icon"
            :size="19"
            class="shrink-0"
            :class="isActive(item.path) ? 'text-white' : 'text-slate-400 group-hover:text-blue-600'"
          />
          <span v-if="!collapsed || isMobile" class="truncate text-sm font-semibold">{{ item.label }}</span>
          <span
            v-if="isActive(item.path) && (!collapsed || isMobile)"
            class="ml-auto h-1.5 w-1.5 rounded-full bg-white/90"
          />
          <div
            v-if="collapsed && !isMobile"
            class="pointer-events-none fixed left-20 z-50 rounded-lg bg-slate-950 px-2 py-1 text-[10px] font-bold text-white opacity-0 shadow-md transition-opacity group-hover:opacity-100"
          >
            {{ item.label }}
          </div>
        </button>
      </div>
    </nav>

    <!-- Collapse toggle (desktop only) -->
    <button
      v-if="collapsed && !isMobile"
      type="button"
      aria-label="展开导航栏"
      title="展开导航栏"
      @click="collapsed = false"
      class="mx-auto mb-4 p-2 hover:bg-slate-100 rounded-lg transition-colors text-slate-400 hover:text-slate-700"
    >
      <ChevronRight :size="20" />
    </button>
  </aside>
</template>
