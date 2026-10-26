<template>
  <div class="panel">
    <div class="panel-head">
      <h2>{{ title }}</h2>
      <span v-if="hint" class="hint">{{ hint }}</span>
      <span class="grow" />
      <slot name="actions" />
    </div>
    <div class="panel-body tight">
      <div class="scroll" :class="{ thin }">
        <table class="tbl">
          <thead>
            <tr>
              <th v-for="col in columns" :key="col.key" :class="{ n: col.align === 'right' }">{{ col.label }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!rows.length">
              <td :colspan="columns.length" class="empty">{{ empty }}</td>
            </tr>
            <tr v-for="(row, index) in rows" :key="row.id ?? index">
              <td
                v-for="col in columns"
                :key="col.key"
                :class="[{ n: col.align === 'right', mono: col.mono }, col.tone ? col.tone(row) : '']"
              >
                <slot :name="`cell-${col.key}`" :row="row">
                  {{ col.format ? col.format(row) : row[col.key] }}
                </slot>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  title: { type: String, default: '' },
  hint: { type: String, default: '' },
  empty: { type: String, default: '暂无数据' },
  columns: { type: Array, required: true },
  rows: { type: Array, default: () => [] },
  thin: { type: Boolean, default: false },
})
</script>
