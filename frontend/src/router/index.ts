import { createRouter, createWebHistory } from 'vue-router'
import AdminLayout from '../layouts/AdminLayout.vue'
import LoginPage from '../pages/LoginPage.vue'
import RunPage from '../pages/RunPage.vue'
import RunDetailPage from '../pages/RunDetailPage.vue'
import HistoryPage from '../pages/HistoryPage.vue'
import QualityMemoryPage from '../pages/QualityMemoryPage.vue'
import ProvidersPage from '../pages/ProvidersPage.vue'
import DocumentsPage from '../pages/DocumentsPage.vue'
import EnvironmentsPage from '../pages/EnvironmentsPage.vue'
import TestCasesPage from '../pages/TestCasesPage.vue'
import KnowledgePage from '../pages/KnowledgePage.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: LoginPage },
    {
      path: '/',
      component: AdminLayout,
      children: [
        { path: '', redirect: '/run' },
        { path: 'dashboard', redirect: '/run' },
        { path: 'run', component: RunPage },
        { path: 'runs/:id', component: RunDetailPage },
        { path: 'history', component: HistoryPage },
        { path: 'quality-memory', component: QualityMemoryPage },
        { path: 'settings', redirect: '/providers' },
        { path: 'providers', component: ProvidersPage },
        { path: 'documents', component: DocumentsPage },
        { path: 'environments', component: EnvironmentsPage },
        { path: 'test-cases', component: TestCasesPage },
        { path: 'knowledge', component: KnowledgePage },
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
