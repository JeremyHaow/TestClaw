<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()
const username = ref('admin')
const password = ref('testclaw123')
const error = ref('')

async function submit() {
  error.value = ''
  try {
    await auth.login(username.value, password.value)
    router.push('/run')
  } catch (err: any) {
    error.value = err?.response?.data?.detail || '登录失败'
  }
}
</script>

<template>
  <div class="min-h-screen bg-gray-50 flex items-center justify-center p-4">
    <div class="w-full max-w-md">
      <div class="text-center mb-8">
        <div class="inline-flex items-center gap-2 mb-4">
          <div class="w-10 h-10 bg-blue-600 rounded-lg flex items-center justify-center">
            <div class="w-5 h-5 border-2 border-white rounded-sm"></div>
          </div>
          <span class="font-bold text-2xl tracking-tight text-gray-900">TestClaw</span>
        </div>
        <p class="text-gray-500 text-sm">AI 驱动的全链路智能测试平台</p>
      </div>

      <div class="bg-white border border-gray-200 rounded-xl shadow-sm p-8">
        <h2 class="text-lg font-semibold text-gray-900 mb-6">登录控制台</h2>
        <form class="space-y-5" @submit.prevent="submit">
          <div>
            <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-2">用户名</label>
            <input
              v-model="username"
              placeholder="admin"
              class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all"
            />
          </div>
          <div>
            <label class="text-[10px] font-bold text-gray-400 uppercase tracking-widest block mb-2">密码</label>
            <input
              v-model="password"
              type="password"
              placeholder="请输入密码"
              class="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all"
            />
          </div>
          <p v-if="error" class="text-red-500 text-xs">{{ error }}</p>
          <button
            type="submit"
            :disabled="auth.loading"
            class="w-full py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-bold transition-all shadow-md shadow-blue-600/10"
          >
            {{ auth.loading ? '登录中...' : '登录' }}
          </button>
        </form>
        <div class="mt-4 text-center text-xs text-gray-400">默认账号：admin / testclaw123</div>
      </div>
    </div>
  </div>
</template>
