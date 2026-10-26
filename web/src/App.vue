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
