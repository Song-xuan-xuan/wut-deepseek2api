<template>
  <div class="toasts">
    <div v-for="item in ui.toasts" :key="item.id" class="toast" :class="item.type">
      {{ item.message }}
    </div>
  </div>
</template>

<script setup>
import { ui } from '../stores/ui'
</script>
