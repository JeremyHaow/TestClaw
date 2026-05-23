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
  <div class="flex min-h-screen bg-gray-50 text-gray-900 font-sans">
    <AppSidebar />
    <main class="flex-1 flex flex-col min-w-0 h-screen overflow-hidden">
      <AppHeader />
      <div class="flex-1 overflow-y-auto p-4 lg:p-6">
        <router-view />
      </div>
    </main>
  </div>
</template>
