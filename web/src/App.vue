<template>
  <div class="shell" :class="{ collapsed }">
    <!-- 侧边栏 -->
    <aside class="sidebar" :class="{ open: mobileOpen }" @click="mobileOpen = false">
      <div class="brand">
        <div class="brand-logo">
          <img src="/logo.svg" alt="武汉理工大学" />
        </div>
        <div class="brand-text">
          <strong>武汉理工 · 2api</strong>
          <span>v{{ state.info?.version || '…' }} · {{ healthy ? '运行中' : '需处理' }}</span>
        </div>
      </div>

      <template v-for="group in nav" :key="group.title">
        <div class="nav-group">
          <div class="nav-group-title">{{ group.title }}</div>
          <RouterLink
            v-for="item in group.items"
            :key="item.path"
            :to="item.path"
            class="nav-item"
            :class="{ active: $route.path === item.path }"
          >
            <span class="icon">{{ item.icon }}</span>
            <span class="label">{{ item.label }}</span>
            <span v-if="item.badge" class="badge">{{ item.badge }}</span>
          </RouterLink>
        </div>
      </template>

      <div class="sidebar-footer">
        <div class="mono">{{ state.info?.listen || '—' }}</div>
        <div>
          <span v-if="state.token?.present">token 剩 {{ state.token.days_left ?? '?' }} 天</span>
          <span v-else class="warn">未认证</span>
        </div>
        <div v-if="state.auth?.email" class="mono">学号 {{ state.auth.email }}</div>
        <a
          v-if="state.info?.repo_url"
          class="repo-mini"
          :href="state.info.repo_url"
          target="_blank"
          rel="noreferrer"
          title="GitHub 仓库"
        >
          <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" fill="currentColor">
            <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38
                     0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13
                     -.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66
                     .07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15
                     -.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.4 7.4 0 0 1 2-.27c.68 0
                     1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82
                     2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01
                     2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z"/>
          </svg>
          <span>GitHub</span>
        </a>
      </div>
    </aside>

    <!-- 右侧 -->
    <div class="main-wrap">
      <header class="topbar">
        <button class="btn ghost icon" title="收起/展开侧边栏" @click="toggleSidebar">
          {{ collapsed ? '»' : '«' }}
        </button>
        <div class="crumb">
          <span class="muted">whut-2api</span> / <b>{{ currentLabel }}</b>
        </div>
        <div class="grow" />
        <span class="pill dot" :class="state.circuit?.open ? 'warn' : healthy ? 'ok' : 'bad'">
          {{ state.circuit?.open ? `熔断 ${state.circuit.remaining_seconds}s` : healthy ? '正常' : '异常' }}
        </span>
        <ThemeToggle />
        <button class="btn sm" :disabled="state.loading" @click="reload">刷新</button>
      </header>

      <RouterView />
    </div>

    <ToastHost />
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import ThemeToggle from './components/ThemeToggle.vue'
import ToastHost from './components/ToastHost.vue'
import { useSystemStore } from './stores/system'

const { state, healthy, reload } = useSystemStore()
const route = useRoute()

const collapsed = ref(localStorage.getItem('whut-2api-sidebar') === 'collapsed')
const mobileOpen = ref(false)

function toggleSidebar() {
  collapsed.value = !collapsed.value
  localStorage.setItem('whut-2api-sidebar', collapsed.value ? 'collapsed' : 'open')
}

const nav = computed(() => [
  {
    title: '概览',
    items: [
      { path: '/', label: '仪表盘', icon: '◧' },
      { path: '/usage', label: '用量统计', icon: '▤' },
    ],
  },
  {
    title: '接入配置',
    items: [
      { path: '/auth', label: '认证', icon: '⚿', badge: state.token?.present ? '' : '待配置' },
      { path: '/access', label: '分发 / Key', icon: '⇄' },
    ],
  },
  {
    title: '运行',
    items: [{ path: '/system', label: '系统信息', icon: '⚙' }],
  },
])

const labels = { '/': '仪表盘', '/usage': '用量统计', '/auth': '认证', '/access': '分发 / Key', '/system': '系统信息' }
const currentLabel = computed(() => labels[route.path] || '仪表盘')
</script>
