<script setup lang="ts">
import { KeyRound } from 'lucide-vue-next'
</script>

<template>
  <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
    <div class="mb-4 flex items-start gap-3">
      <div class="flex items-center gap-2">
        <KeyRound :size="17" class="text-gray-500" />
        <div>
          <h3 class="text-sm font-bold text-gray-900">鉴权预检</h3>
          <p class="mt-1 text-xs leading-5 text-gray-500">接口测试和 UI 测试都会先确认鉴权路径，再启动任务。</p>
        </div>
      </div>
    </div>

    <slot />
  </div>
</template>
