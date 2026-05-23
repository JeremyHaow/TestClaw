<script setup lang="ts">
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import {
  Bot, BrainCircuit, History, Cpu, FileCode, Globe, Layers, BookOpen, ChevronLeft, ChevronRight
} from 'lucide-vue-next'
import { useSidebar } from '../composables/useSidebar'

const router = useRouter()
const route = useRoute()
const collapsed = ref(false)
const { mobileOpen, isMobile, closeMobile } = useSidebar()

const navItems = [
  { path: '/run', label: '测试智能体', icon: Bot },
  { path: '/history', label: '运行历史', icon: History },
  { path: '/quality-memory', label: '质量记忆', icon: BrainCircuit },
  { path: '/providers', label: '模型与 Agent', icon: Cpu },
  { path: '/documents', label: '接口文档', icon: FileCode },
  { path: '/environments', label: '测试环境', icon: Globe },
  { path: '/test-cases', label: '用例资产', icon: Layers },
  { path: '/knowledge', label: 'RAG 知识库', icon: BookOpen },
]

function isActive(path: string) {
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
    class="h-screen bg-white border-r border-gray-200 flex flex-col sticky top-0 z-40 transition-all duration-200"
    :class="[
      isMobile
        ? (mobileOpen ? 'fixed inset-y-0 left-0 w-64 shadow-xl' : 'hidden')
        : (collapsed ? 'w-20' : 'w-64')
    ]"
  >
    <!-- Logo -->
    <div class="p-6 border-b border-gray-100 flex items-center justify-between h-16">
      <div v-if="!collapsed || isMobile" class="flex items-center gap-2 overflow-hidden">
        <div class="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center shrink-0">
          <div class="w-4 h-4 border-2 border-white rounded-sm"></div>
        </div>
        <div class="min-w-0">
          <span class="block truncate text-lg font-bold tracking-tight text-gray-900">TestClaw</span>
          <span class="block truncate text-[10px] font-bold uppercase tracking-widest text-gray-400">Testing Agent</span>
        </div>
      </div>
      <div v-if="collapsed && !isMobile" class="mx-auto w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center shrink-0">
        <div class="w-4 h-4 border-2 border-white rounded-sm"></div>
      </div>
      <button
        v-if="!collapsed && !isMobile"
        @click="collapsed = true"
        class="p-1.5 hover:bg-gray-100 rounded-md transition-colors text-gray-400 hover:text-gray-600"
      >
        <ChevronLeft :size="18" />
      </button>
    </div>

    <!-- Main Nav -->
    <nav class="flex-1 px-3 py-4 space-y-1 overflow-y-auto overflow-x-hidden">
      <button
        v-for="item in navItems"
        :key="item.path"
        @click="navigate(item.path)"
        class="w-full flex items-center rounded-lg transition-all duration-200 group relative"
        :class="[
          isActive(item.path) ? 'bg-blue-50 text-blue-700 font-medium' : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900',
          collapsed && !isMobile ? 'justify-center px-0 py-2.5' : 'gap-3 px-3 py-2.5',
        ]"
      >
        <component
          :is="item.icon"
          :size="20"
          class="shrink-0"
          :class="isActive(item.path) ? 'text-blue-600' : 'text-gray-400 group-hover:text-gray-600'"
        />
        <span v-if="!collapsed || isMobile" class="truncate">{{ item.label }}</span>
        <div
          v-if="collapsed && !isMobile"
          class="pointer-events-none fixed left-20 z-50 rounded bg-gray-900 px-2 py-1 text-[10px] text-white opacity-0 transition-opacity group-hover:opacity-100"
        >
          {{ item.label }}
        </div>
      </button>
    </nav>

    <!-- Collapse toggle (desktop only) -->
    <button
      v-if="collapsed && !isMobile"
      @click="collapsed = false"
      class="mx-auto mb-4 p-2 hover:bg-gray-100 rounded-md transition-colors text-gray-400"
    >
      <ChevronRight :size="20" />
    </button>
  </aside>
</template>
