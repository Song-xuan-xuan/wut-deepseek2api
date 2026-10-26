import { createRouter, createWebHashHistory } from 'vue-router'
import OverviewView from './views/OverviewView.vue'
import UsageView from './views/UsageView.vue'
import AuthView from './views/AuthView.vue'
import AccessView from './views/AccessView.vue'
import SystemView from './views/SystemView.vue'

// hash 模式：后端只需要托管 index.html，不用为每个路由配 fallback
export default createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'overview', component: OverviewView },
    { path: '/usage', name: 'usage', component: UsageView },
    { path: '/auth', name: 'auth', component: AuthView },
    { path: '/access', name: 'access', component: AccessView },
    { path: '/system', name: 'system', component: SystemView },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  scrollBehavior: () => ({ top: 0 }),
})
