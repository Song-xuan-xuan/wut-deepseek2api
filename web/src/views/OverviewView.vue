<template>
  <main class="main">
    <div class="page-head">
      <div>
        <h1>仪表盘</h1>
        <p>服务状态、凭证有效期与今日用量一览</p>
      </div>
      <div class="row">
        <span class="pill dot" :class="state.circuit?.open ? 'warn' : healthy ? 'ok' : 'bad'">
          {{ state.circuit?.open ? '熔断冷却' : healthy ? '运行正常' : '需要处理' }}
        </span>
        <span class="hint-text">更新于 {{ state.updatedAt }}</span>
      </div>
    </div>

    <div v-if="state.error" class="notice bad">
      <span>连不上本地服务：{{ state.error }}。确认 <code>python -m app</code> 还在跑。</span>
    </div>

    <template v-else>
      <section class="cards">
        <StatCard
          label="今日请求"
          icon="▤"
          accent
          :value="today.requests || 0"
          :sub="`流式 ${today.streams || 0} · 工具调用 ${today.tool_calls || 0}`"
          :spark="spark"
        />
        <StatCard
          label="今日 Token"
          icon="◈"
          :value="fmtTokens((today.prompt_tokens || 0) + (today.completion_tokens || 0))"
          :sub="`入 ${fmtTokens(today.prompt_tokens)} / 出 ${fmtTokens(today.completion_tokens)}`"
          :spark="tokenSpark"
          :color-index="2"
        />
        <StatCard
          label="错误"
          icon="⚠"
          :value="today.errors || 0"
          :tone="today.errors ? 'warn' : 'ok'"
          :sub="`思维链 ${fmtTokens(today.reasoning_tokens)} tokens`"
        />
        <StatCard
          label="Token 剩余"
          icon="⚿"
          :value="tokenValue"
          :tone="tokenTone"
          :sub="tokenSub"
        />
        <StatCard
          label="模型"
          icon="◈"
          :value="state.models.length"
          :sub="state.models.join(' · ') || '获取失败'"
          small
        />
        <StatCard
          label="自动续期"
          icon="↻"
          :value="state.auth?.auto_refresh && state.auth?.email ? '已开启' : '未开启'"
          :tone="state.auth?.auto_refresh && state.auth?.email ? 'ok' : 'warn'"
          :sub="state.auth?.email ? `学号 ${state.auth.email}` : '去「认证」页设置'"
          small
        />
      </section>

      <section class="grid-2">
        <div class="panel">
          <div class="panel-head">
            <h2>客户端配置</h2>
            <span class="hint">复制到任何 OpenAI 兼容客户端</span>
            <span class="grow" />
            <RouterLink to="/access" class="btn sm ghost">更多模板 →</RouterLink>
          </div>
          <div class="panel-body">
            <CopyRows :rows="clientRows" />
            <p class="hint-text" style="margin: 12px 0 0">
              输出上限建议 ≤ {{ state.info?.max_tokens_cap }}；Cline / Roo 记得勾「Supports Tools」。
            </p>
          </div>
        </div>

        <div class="panel">
          <div class="panel-head">
            <h2>近 24 小时</h2>
            <span class="hint">按小时请求量</span>
          </div>
          <div class="panel-body">
            <AreaChart
              :series="hourlySeries"
              :labels="hourlyLabels"
              :height="196"
              :format="(value) => String(Math.round(value))"
            />
          </div>
        </div>
      </section>

      <section class="grid-2">
        <div class="panel">
          <div class="panel-head">
            <h2>模型用量占比</h2>
            <span class="hint">近 7 天 token</span>
          </div>
          <div class="panel-body">
            <HBarList :data="modelBars" />
          </div>
        </div>

        <div class="panel">
          <div class="panel-head">
            <h2>可用模型</h2>
            <span class="hint">{{ state.models.length }} 个</span>
          </div>
          <div class="panel-body">
            <div class="chips">
              <span v-for="model in state.models" :key="model" class="chip">{{ model }}</span>
            </div>
            <div class="divider" style="margin: 14px 0" />
            <div class="kv" style="grid-template-columns: 96px 1fr">
              <div class="k">默认模型</div>
              <div class="v">{{ state.info?.default_model }}</div>
              <div class="k">上游</div>
              <div class="v">{{ (state.info?.upstream || '').replace(/^https?:\/\//, '') }}</div>
              <div class="k">用量库</div>
              <div class="v">{{ state.usage?.db_path }}</div>
            </div>
          </div>
        </div>
      </section>

      <DataTable title="最近请求" hint="来自持久化用量库" :columns="recentColumns" :rows="recent">
        <template #actions>
          <RouterLink to="/usage" class="btn sm ghost">查看明细 →</RouterLink>
        </template>
      </DataTable>
    </template>
  </main>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import AreaChart from '../components/AreaChart.vue'
import CopyRows from '../components/CopyRows.vue'
import DataTable from '../components/DataTable.vue'
import HBarList from '../components/HBarList.vue'
import StatCard from '../components/StatCard.vue'
import { useSystemStore } from '../stores/system'

const { state, healthy } = useSystemStore()
const recent = ref([])
const overview = ref(null)

const today = computed(() => state.usage?.today || {})
const hourly = computed(
  () => state.usage?.series_24h?.points || overview.value?.series?.points || [],
)
const spark = computed(() => hourly.value.map((point) => point.requests || 0))
const hourlyLabels = computed(() => hourly.value.map((point) => point.bucket))
const hourlySeries = computed(() => [
  { key: 'requests', label: '请求', values: hourly.value.map((point) => point.requests || 0) },
  {
    key: 'tokens',
    label: 'Token',
    values: hourly.value.map((point) => (point.prompt_tokens || 0) + (point.completion_tokens || 0)),
  },
])
const tokenSpark = computed(() =>
  hourly.value.map((point) => (point.prompt_tokens || 0) + (point.completion_tokens || 0)),
)
const modelBars = computed(() =>
  (overview.value?.by_model || []).slice(0, 6).map((row) => ({
    name: row.model,
    value: (row.prompt_tokens || 0) + (row.completion_tokens || 0),
    display: fmtTokens((row.prompt_tokens || 0) + (row.completion_tokens || 0)),
  })),
)

const fmtTokens = (value) => {
  const n = Number(value || 0)
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (n >= 10_000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}

const tokenValue = computed(() => {
  const token = state.token
  if (!token?.present) return '未配置'
  if (token.days_left === null || token.days_left === undefined) return '长期'
  return `${token.days_left} 天`
})
const tokenTone = computed(() => {
  if (!state.token?.present) return 'bad'
  const days = state.token?.days_left
  return days !== null && days !== undefined && days < 2 ? 'warn' : 'ok'
})
const tokenSub = computed(() =>
  state.token?.expires_at ? `到期 ${state.token.expires_at.replace('T', ' ').replace('Z', '')}` : '—',
)

const clientRows = computed(() => {
  const host = location.hostname || '127.0.0.1'
  const port = (state.info?.listen || '').split(':')[1] || location.port || '8787'
  return [
    { key: 'Base URL', value: `${location.protocol}//${host}:${port}/v1`, copyable: true },
    { key: 'API Key', value: state.info?.client_api_key || 'sk-whut-local', copyable: true },
    { key: '模型', value: state.info?.default_model || state.models[0] || '—', copyable: true },
  ]
})

const recentColumns = [
  { key: 'time', label: '时间', mono: true },
  { key: 'model', label: '模型', mono: true },
  { key: 'stream', label: '类型', format: (row) => (row.stream ? '流式' : '普通') },
  { key: 'status', label: '状态', align: 'right', tone: (row) => (row.status >= 500 ? 'bad' : row.status >= 400 ? 'warn' : 'ok') },
  { key: 'ms', label: '耗时', align: 'right', format: (row) => `${row.ms} ms` },
  { key: 'tokens', label: '入/出', align: 'right', format: (row) => `${row.prompt_tokens}/${row.completion_tokens}` },
  { key: 'note', label: '备注', format: (row) => row.error || (row.notes || []).join(', ') },
]

async function load() {
  // 最近请求 + 7 天模型占比（KPI 与 24h 折线已经由 system/info 提供）
  const [recentResp, usageResp] = await Promise.all([api.recent(10), api.usage('7d')])
  if (recentResp.ok) recent.value = recentResp.data.items
  if (usageResp.ok) overview.value = usageResp.data
}

onMounted(load)
</script>
