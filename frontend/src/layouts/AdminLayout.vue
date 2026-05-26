<script setup lang="ts">
import { onMounted } from 'vue'
import AppHeader from '../components/AppHeader.vue'
import AppSidebar from '../components/AppSidebar.vue'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()

onMounted(async () => {
  if (auth.token && !auth.user) {
    await auth.fetchMe()
  }
})
</script>

<template>
  <div class="flex min-h-screen bg-[#F5F7FB] text-gray-900 font-sans">
    <AppSidebar />
    <main class="flex-1 flex min-w-0 flex-col h-screen overflow-hidden">
      <AppHeader />
      <div class="flex-1 overflow-y-auto px-4 py-4 sm:px-5 lg:px-6 lg:py-5">
        <router-view />
      </div>
    </main>
  </div>
</template>
