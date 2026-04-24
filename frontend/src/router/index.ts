import { createRouter, createWebHistory } from 'vue-router'
import AdminLayout from '../layouts/AdminLayout.vue'
import LoginPage from '../pages/LoginPage.vue'
import RunPage from '../pages/RunPage.vue'
import RunDetailPage from '../pages/RunDetailPage.vue'
import HistoryPage from '../pages/HistoryPage.vue'
import SettingsPage from '../pages/SettingsPage.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: LoginPage },
    {
      path: '/',
      component: AdminLayout,
      children: [
        { path: '', redirect: '/run' },
        { path: 'run', component: RunPage },
        { path: 'runs/:id', component: RunDetailPage },
        { path: 'history', component: HistoryPage },
        { path: 'settings', component: SettingsPage },
      ],
    },
  ],
})

router.beforeEach((to) => {
  const token = localStorage.getItem('testclaw_token')
  if (to.path !== '/login' && !token) {
    return '/login'
  }
  if (to.path === '/login' && token) {
    return '/run'
  }
})

export default router
