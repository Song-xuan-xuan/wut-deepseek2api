<template>
  <main class="main">
    <div class="page-head">
      <div>
        <h1>用量统计</h1>
        <p>
          数据存于 <span class="mono">{{ state.data?.db_path }}</span>，重启不清零
        </p>
      </div>
      <div class="row">
        <button class="btn sm" :disabled="state.loading" @click="reload">刷新</button>
        <button class="btn sm" @click="exportCsv">导出 CSV</button>
        <button class="btn sm danger" @click="clearAll">清空</button>
      </div>
    </div>

    <div class="row">
      <SegControl v-model="rangeModel" :options="rangeOptions" label="区间" />
      <select class="select" style="width: 210px" :value="state.model" @change="setModel($event.target.value)">
        <option value="">全部模型</option>
        <option v-for="model in state.data?.models || []" :key="model" :value="model">{{ model }}</option>
      </select>
      <span class="grow" />
      <span v-if="state.updatedAt" class="hint-text">更新于 {{ state.updatedAt }}</span>
    </div>

    <div v-if="state.error" class="notice bad">{{ state.error }}</div>
    <div v-else-if="state.data && !state.data.available" class="notice warn">
      用量统计不可用：{{ state.data.last_error || '数据库未初始化' }}
    </div>

    <template v-if="state.data">
      <!-- 顶部指标：axonhub 风格（最后一格是强调色卡） -->
      <section class="grid-4">
        <StatCard
          label="总请求数"
          icon="▤"
          :value="totals.requests || 0"
          :sub="`流式 ${totals.streams || 0} · 工具调用 ${totals.tool_calls || 0}`"
          :spark="requestSpark"
          :color-index="2"
        />
        <StatCard
          label="成功率"
          icon="✓"
          :value="successRate"
          :sub="`失败 ${totals.errors || 0} 次`"
          :tone="totals.errors ? 'warn' : 'ok'"
        />
        <StatCard
          label="Token 用量"
          icon="◈"
          :value="fmt(totalTokens)"
          :sub="`入 ${fmt(totals.prompt_tokens)} · 出 ${fmt(totals.completion_tokens)}`"
          :spark="tokenSpark"
          :color-index="5"
        />
        <StatCard
          label="平均耗时"
          icon="◷"
          accent
          :value="`${latency.p50} ms`"
          :sub="`p95 ${latency.p95} ms · 最慢 ${latency.max} ms`"
        />
      </section>

      <!-- 主图 + 右侧模型成功率 -->
      <section class="grid-main">
        <div class="panel">
          <div class="panel-head">
            <h2>{{ state.data.series.bucket === 'hour' ? '分时概览' : '每日概览' }}</h2>
            <span class="hint">Token / 请求双轴趋势，点击图例可隐藏</span>
          </div>
          <div class="panel-body">
            <AreaChart
              :series="chartSeries"
              :labels="chartLabels"
              :format="(value) => fmt(value)"
              :height="230"
            />
          </div>
        </div>

        <div class="panel">
          <div class="panel-head">
            <h2>模型成功率</h2>
            <span class="hint">
              {{ modelRows.length }} 个可用模型{{
                failedOnlyModels.length ? ` · ${failedOnlyModels.length} 个全部失败` : ''
              }}
            </span>
          </div>
          <div class="panel-body" style="display: flex; flex-direction: column; gap: 16px">
            <div v-for="row in modelRows" :key="row.model">
              <div class="row" style="justify-content: space-between; margin-bottom: 6px">
                <span class="mono small">{{ row.model }}</span>
                <span class="small" :class="row.rate >= 99 ? 'ok' : row.rate >= 90 ? 'warn' : 'bad'">
                  {{ row.rate.toFixed(1) }}%
                </span>
              </div>
              <div class="meter"><i :style="{ width: `${row.rate}%` }" /></div>
              <div class="row small muted" style="margin-top: 5px; gap: 12px">
                <span class="ok">● {{ row.requests - row.errors }} 成功</span>
                <span :class="row.errors ? 'bad' : 'muted'">● {{ row.errors }} 失败</span>
                <span class="grow" />
                <span>{{ fmt(row.tokens) }} tok</span>
              </div>
            </div>
            <div v-if="failedOnlyModels.length" class="hint-text" style="margin-top: 4px">
              全部失败（上游不可用，已被自动隔离）：
              <span class="mono">{{ failedOnlyModels.map((r) => r.model).join('、') }}</span>
            </div>
            <div v-if="!modelRows.length" class="empty-state">
              <div class="big">▤</div>
              这个区间还没有请求
            </div>
          </div>
        </div>
      </section>

      <!-- 折叠详情：按模型 / 状态码 / 按 key -->
      <SectionRow title="数据详情分析" desc="按模型、状态码、分发 key 的多维拆解" icon="◫">
        <div class="grid-3">
          <DataTable title="按模型请求" :columns="modelColumns" :rows="state.data.by_model" thin />
          <DataTable title="状态码" :columns="statusColumns" :rows="state.data.by_status" thin />
          <DataTable
            title="按 Key（分发）"
            :columns="keyColumns"
            :rows="state.data.by_key || []"
            thin
          />
        </div>
      </SectionRow>

      <SectionRow title="按模型 Token 占比" desc="柱状占比，悬停看明细" icon="◧">
        <div class="panel">
          <div class="panel-body">
            <HBarList :data="modelBars" />
          </div>
        </div>
      </SectionRow>

      <DataTable title="最近请求" hint="最多 500 条" :columns="recentColumns" :rows="state.data.recent" />
    </template>
  </main>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { api, download } from '../api'
import DataTable from '../components/DataTable.vue'
import HBarList from '../components/HBarList.vue'
import AreaChart from '../components/AreaChart.vue'
import SectionRow from '../components/SectionRow.vue'
import SegControl from '../components/SegControl.vue'
import StatCard from '../components/StatCard.vue'
import { setModel, setRange, state, useUsageStore } from '../stores/usage'
import { useUi } from '../stores/ui'

const { reload } = useUsageStore()
const { toast } = useUi()

const rangeOptions = [
  { value: 'today', label: '今天' },
  { value: '24h', label: '24 小时' },
  { value: '7d', label: '7 天' },
  { value: '30d', label: '30 天' },
  { value: 'all', label: '全部' },
]

const rangeModel = computed({
  get: () => state.range,
  set: (value) => setRange(value),
})

const totals = computed(() => state.data?.summary?.totals || {})
const latency = computed(() => state.data?.summary?.latency_ms || { p50: 0, p95: 0, max: 0 })
const totalTokens = computed(() => (totals.value.prompt_tokens || 0) + (totals.value.completion_tokens || 0))
const successRate = computed(() => {
  const requests = totals.value.requests || 0
  if (!requests) return '—'
  return `${(((requests - (totals.value.errors || 0)) / requests) * 100).toFixed(1)}%`
})

const points = computed(() => state.data?.series?.points || [])
const chartLabels = computed(() => points.value.map((point) => point.bucket))
const chartSeries = computed(() => [
  {
    key: 'tokens',
    label: 'Token（左轴）',
    axis: 'left',
    values: points.value.map((point) => (point.prompt_tokens || 0) + (point.completion_tokens || 0)),
  },
  { key: 'requests', label: '请求（右轴）', axis: 'right', values: points.value.map((point) => point.requests || 0) },
  { key: 'errors', label: '错误（右轴）', axis: 'right', values: points.value.map((point) => point.errors || 0) },
])
const requestSpark = computed(() => points.value.map((point) => point.requests || 0))
const tokenSpark = computed(() =>
  points.value.map((point) => (point.prompt_tokens || 0) + (point.completion_tokens || 0)),
)

const allModelRows = computed(() =>
  (state.data?.by_model || []).map((row) => ({
    model: row.model,
    requests: row.requests || 0,
    errors: row.errors || 0,
    tokens: (row.prompt_tokens || 0) + (row.completion_tokens || 0),
    rate: row.requests ? ((row.requests - (row.errors || 0)) / row.requests) * 100 : 0,
  })),
)
// 有成功记录的模型正常展示；从未成功的（上游挂了 / 模型名写错）折叠成一行提示，
// 免得整张卡片看起来像"多了几个奇怪的模型"
const modelRows = computed(() =>
  allModelRows.value
    .filter((row) => row.requests - row.errors > 0)
    .sort((a, b) => b.requests - a.requests)
    .slice(0, 6),
)
const failedOnlyModels = computed(() =>
  allModelRows.value.filter((row) => row.requests > 0 && row.requests - row.errors === 0),
)

const modelBars = computed(() =>
  (state.data?.by_model || []).slice(0, 8).map((row) => ({
    name: row.model,
    value: (row.prompt_tokens || 0) + (row.completion_tokens || 0),
    display: fmt((row.prompt_tokens || 0) + (row.completion_tokens || 0)),
  })),
)

const fmt = (value) => {
  const n = Number(value || 0)
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (n >= 10_000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}

const modelColumns = [
  { key: 'model', label: '模型', mono: true },
  { key: 'requests', label: '请求', align: 'right' },
  { key: 'errors', label: '错误', align: 'right', tone: (row) => (row.errors ? 'warn' : '') },
  { key: 'prompt_tokens', label: '入', align: 'right', format: (row) => fmt(row.prompt_tokens) },
  { key: 'completion_tokens', label: '出', align: 'right', format: (row) => fmt(row.completion_tokens) },
]

const statusColumns = [
  {
    key: 'status',
    label: '状态码',
    tone: (row) => (row.status >= 500 ? 'bad' : row.status >= 400 ? 'warn' : 'ok'),
  },
  { key: 'count', label: '次数', align: 'right' },
]

const keyColumns = [
  { key: 'key_name', label: 'Key' },
  { key: 'requests', label: '请求', align: 'right' },
  { key: 'errors', label: '错误', align: 'right', tone: (row) => (row.errors ? 'warn' : '') },
  { key: 'prompt_tokens', label: '入', align: 'right', format: (row) => fmt(row.prompt_tokens) },
]

const recentColumns = [
  { key: 'time', label: '时间', mono: true },
  { key: 'model', label: '模型', mono: true },
  { key: 'stream', label: '类型', format: (row) => (row.stream ? '流式' : '普通') },
  {
    key: 'status',
    label: '状态',
    align: 'right',
    tone: (row) => (row.status >= 500 ? 'bad' : row.status >= 400 ? 'warn' : 'ok'),
  },
  { key: 'ms', label: '耗时', align: 'right', format: (row) => `${row.ms} ms` },
  {
    key: 'tokens',
    label: '入/出',
    align: 'right',
    format: (row) => `${row.prompt_tokens}/${row.completion_tokens}`,
  },
  { key: 'tool_calls', label: '工具', align: 'right' },
  { key: 'note', label: '备注', format: (row) => row.error || (row.notes || []).join(', ') },
]

async function exportCsv() {
  const resp = await api.exportCsv(state.range)
  if (!resp.ok) {
    toast(`导出失败：${resp.error}`, 'error')
    return
  }
  download(`whut-usage-${state.range}.csv`, resp.data)
  toast('已导出 CSV', 'success')
}

async function clearAll() {
  if (!window.confirm('确定清空所有用量记录？不可恢复。')) return
  const resp = await api.clearUsage()
  if (!resp.ok) {
    toast(`清空失败：${resp.error}`, 'error')
    return
  }
  toast(`已清空 ${resp.data.removed} 条记录`, 'success')
  reload()
}

onMounted(reload)
</script>
