<template>
  <main class="main">
    <div class="page-head">
      <div>
        <h1>分发 / Key</h1>
        <p>给不同设备发独立 key，可单独限速、限额，并分开统计</p>
      </div>
      <button class="btn primary" @click="showCreate = !showCreate">
        {{ showCreate ? '取消' : '新建 Key' }}
      </button>
    </div>

    <div v-if="data && data.single_key_mode" class="notice">
      <span>
        当前只有 config.json 里的主 key（<code>{{ data.master_key || '未设置' }}</code>）。
        想给手机 / 平板 / 别的电脑单独发 key，用下面的「新建 Key」。
      </span>
    </div>

    <!-- 新建 -->
    <section v-if="showCreate" class="panel">
      <div class="panel-head"><h2>新建 Key</h2><span class="hint">每台设备一把，方便随时吊销</span></div>
      <div class="panel-body">
        <div class="row" style="align-items: flex-end">
          <div class="field" style="flex: 1; min-width: 180px">
            <label>名称</label>
            <input v-model="form.name" class="input" placeholder="例如 手机 / 宿舍台式机" />
          </div>
          <div class="field" style="width: 160px">
            <label>每分钟请求上限（0 = 跟随默认）</label>
            <input v-model.number="form.rpm_limit" class="input" type="number" min="0" />
          </div>
          <div class="field" style="width: 180px">
            <label>每日 token 上限（0 = 不限）</label>
            <input v-model.number="form.daily_token_limit" class="input" type="number" min="0" />
          </div>
          <button class="btn primary" :disabled="busy" @click="create">创建</button>
        </div>
        <p class="hint-text" style="margin: 10px 0 0">
          默认限速 {{ data?.limits?.default_rpm_limit }} 次/分钟；限额按自然日统计输入+输出 token。
        </p>
      </div>
    </section>

    <!-- Key 列表 -->
    <section class="panel">
      <div class="panel-head">
        <h2>Key 列表</h2>
        <span class="hint">{{ data?.keys?.length || 0 }} 把</span>
        <span class="grow" />
        <button class="btn sm ghost" @click="reload">刷新</button>
      </div>
      <div class="panel-body tight">
        <div class="scroll">
          <table class="tbl">
            <thead>
              <tr>
                <th>名称</th>
                <th>Key</th>
                <th class="n">今日请求</th>
                <th class="n">今日 token</th>
                <th class="n">配额</th>
                <th class="n">限速</th>
                <th>最近使用</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!data?.keys?.length">
                <td colspan="8" class="empty">还没有子 key，服务只用主 key</td>
              </tr>
              <tr v-for="item in data?.keys || []" :key="item.id">
                <td>
                  <div class="row" style="gap: 8px">
                    <span class="switch" :class="{ on: item.enabled }" @click="toggle(item)"></span>
                    <span>{{ item.name }}</span>
                  </div>
                  <div v-if="item.note" class="hint-text">{{ item.note }}</div>
                </td>
                <td class="mono">
                  {{ item.masked }}
                  <button class="btn sm ghost" @click="copyKey(item)">复制</button>
                </td>
                <td class="n">{{ item.requests ?? 0 }}</td>
                <td class="n">{{ fmt(item.tokens_used_today) }}</td>
                <td class="n" :class="{ warn: item.exceeded }">
                  {{ item.daily_token_limit ? `${fmt(item.tokens_used_today)}/${fmt(item.daily_token_limit)}` : '不限' }}
                </td>
                <td class="n">{{ item.rpm_limit ? `${item.rpm_limit}/分` : '默认' }}</td>
                <td class="muted small">{{ relTime(item.last_used_at) }}</td>
                <td class="n">
                  <button class="btn sm ghost" @click="rotate(item)">换 key</button>
                  <button class="btn sm ghost danger" @click="remove(item)">删除</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <!-- 客户端配置 -->
    <section class="panel">
      <div class="panel-head">
        <h2>客户端配置</h2>
        <span class="hint">选一个客户端，复制或下载</span>
        <span class="grow" />
        <div class="seg">
          <button
            v-for="kind in kinds"
            :key="kind.value"
            :class="{ active: kindValue === kind.value }"
            @click="selectKind(kind.value)"
          >
            {{ kind.label }}
          </button>
        </div>
      </div>
      <div class="panel-body">
        <div class="row" style="margin-bottom: 12px">
          <span class="hint-text">使用 key：</span>
          <select v-model="selectedKey" class="select mono" style="min-width: 220px">
            <option value="">{{ data?.master_key || '主 key' }}</option>
            <option v-for="item in data?.keys || []" :key="item.id" :value="item.key">
              {{ item.name }}（{{ item.masked }}）
            </option>
          </select>
          <span class="grow" />
          <button class="btn sm" @click="copyConfig">复制</button>
          <a class="btn sm" :href="downloadHref">下载</a>
        </div>
        <pre class="code">{{ configContent }}</pre>
      </div>
    </section>

    <!-- 网络 -->
    <section class="grid-2">
      <div class="panel">
        <div class="panel-head"><h2>本机访问地址</h2></div>
        <div class="panel-body">
          <CopyRows :rows="localRows" />
        </div>
      </div>
      <div class="panel">
        <div class="panel-head">
          <h2>局域网分发</h2>
          <span class="hint">{{ data?.network?.exposed ? '已开放' : '未开放' }}</span>
        </div>
        <div class="panel-body">
          <div v-if="!data?.network?.exposed" class="notice warn">
            <span>
              服务只监听 <code>{{ data?.network?.listen_host }}</code>，别的设备连不上。
              想给手机/平板用：把 config.json 的 <code>listen.host</code> 改成 <code>0.0.0.0</code> 后重启。
            </span>
          </div>
          <div v-else>
            <CopyRows :rows="lanRows" />
            <p class="hint-text" style="margin: 12px 0 0">
              只在自己家里的网络这么用，不要把端口映射到公网。
            </p>
          </div>
        </div>
      </div>
    </section>
  </main>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api, getApiKey } from '../api'
import CopyRows from '../components/CopyRows.vue'
import { useUi } from '../stores/ui'

const { toast } = useUi()

const data = ref(null)
const showCreate = ref(false)
const busy = ref(false)
const form = ref({ name: '', rpm_limit: 0, daily_token_limit: 0 })
const kinds = [
  { value: 'pi', label: 'pi' },
  { value: 'codex', label: 'Codex' },
  { value: 'claude-code', label: 'Claude Code' },
  { value: 'continue', label: 'Continue' },
  { value: 'curl', label: 'curl' },
  { value: 'python', label: 'Python' },
]
const kindValue = ref('pi')
const selectedKey = ref('')
const configContent = ref('')

const localRows = computed(() => [
  { key: '本机 Base URL', value: `${data.value?.network?.base_url || ''}/v1`, copyable: true },
  { key: '主 Key', value: data.value?.master_key || '(未设置)', copyable: true },
])

const lanRows = computed(() => {
  const network = data.value?.network
  return [
    { key: '局域网地址', value: network?.lan_base_url || '未检测到', copyable: Boolean(network?.lan_base_url) },
    ...(network?.lan_addresses || []).map((ip, index) => ({
      key: `网卡 ${index + 1}`,
      value: `http://${ip}:${network.port}/v1`,
      copyable: true,
    })),
  ]
})

const downloadHref = computed(
  () =>
    `/api/access/client-config/download?kind=${kindValue.value}&key=${encodeURIComponent(selectedKey.value)}&k=${encodeURIComponent(getApiKey())}`,
)

async function reload() {
  const resp = await api.accessOverview()
  if (resp.ok) data.value = resp.data
  else toast(`加载失败：${resp.error}`, 'error')
}

async function loadConfig() {
  const resp = await api.clientConfig(kindValue.value, selectedKey.value)
  if (resp.ok) configContent.value = resp.data.content
}

function selectKind(value) {
  kindValue.value = value
  loadConfig()
}

async function create() {
  busy.value = true
  const resp = await api.createKey({ ...form.value, name: form.value.name || '新设备' })
  busy.value = false
  if (!resp.ok) {
    toast(`创建失败：${resp.error}`, 'error')
    return
  }
  toast('已创建', 'success')
  showCreate.value = false
  form.value = { name: '', rpm_limit: 0, daily_token_limit: 0 }
  await reload()
}

async function toggle(item) {
  const resp = await api.updateKey(item.id, { enabled: !item.enabled })
  if (!resp.ok) toast(`操作失败：${resp.error}`, 'error')
  await reload()
}

async function rotate(item) {
  if (!window.confirm(`重新生成「${item.name}」的 key？旧 key 立即失效。`)) return
  const resp = await api.rotateKey(item.id)
  if (!resp.ok) toast(`操作失败：${resp.error}`, 'error')
  else toast('已换新 key', 'success')
  await reload()
}

async function remove(item) {
  if (!window.confirm(`删除「${item.name}」？该 key 立即失效，用量记录保留。`)) return
  const resp = await api.deleteKey(item.id)
  if (!resp.ok) toast(`删除失败：${resp.error}`, 'error')
  else toast('已删除', 'success')
  await reload()
}

async function copyKey(item) {
  try {
    const full = data.value.keys.find((k) => k.id === item.id)?.key || ''
    await navigator.clipboard.writeText(full)
    toast('已复制完整 key', 'success')
  } catch {
    toast('复制失败', 'error')
  }
}

async function copyConfig() {
  try {
    await navigator.clipboard.writeText(configContent.value)
    toast('已复制配置', 'success')
  } catch {
    toast('复制失败', 'error')
  }
}

function fmt(value) {
  const n = Number(value || 0)
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (n >= 10_000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}

function relTime(ts) {
  if (!ts) return '从未'
  const diff = Date.now() / 1000 - ts
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  return `${Math.floor(diff / 86400)} 天前`
}

watch(selectedKey, loadConfig)

onMounted(async () => {
  await reload()
  await loadConfig()
})
</script>
