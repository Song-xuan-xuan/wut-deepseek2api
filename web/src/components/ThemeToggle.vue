<template>
  <div class="theme-toggle" :title="`当前：${modeLabel}（点击切换）`">
    <button :class="{ active: mode === 'light' }" @click="setMode('light')" aria-label="浅色">☀</button>
    <button :class="{ active: mode === 'dark' }" @click="setMode('dark')" aria-label="深色">☾</button>
    <button :class="{ active: mode === 'system' }" @click="setMode('system')" aria-label="跟随系统">A</button>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useTheme } from '../stores/theme'

const { mode, setMode } = useTheme()
const modeLabel = computed(() => ({ light: '浅色', dark: '深色', system: '跟随系统' })[mode.value])
</script>
