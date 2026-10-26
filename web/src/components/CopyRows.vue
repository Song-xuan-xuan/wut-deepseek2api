<template>
  <div class="kv">
    <template v-for="row in rows" :key="row.key">
      <div class="k">{{ row.key }}</div>
      <div class="v" :class="{ plain: row.plain }">{{ row.value }}</div>
      <button v-if="row.copyable" class="btn sm ghost" @click="copy(row)">复制</button>
      <span v-else />
    </template>
  </div>
</template>

<script setup>
import { useUi } from '../stores/ui'

defineProps({
  rows: { type: Array, default: () => [] },
})
const { toast } = useUi()

async function copy(row) {
  try {
    await navigator.clipboard.writeText(String(row.value))
    toast(`已复制：${row.key}`, 'success')
  } catch {
    toast('复制失败，请手动选择', 'error')
  }
}
</script>
