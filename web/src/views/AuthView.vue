<template>
  <main class="main">
    <div class="page-head">
      <div>
        <h1>认证</h1>
        <p>用学号一键换取 token，自动续期，无需密码</p>
      </div>
      <span class="pill dot" :class="state.token?.present ? 'ok' : 'bad'">
        {{ state.token?.present ? `有效 · 剩 ${state.token.days_left ?? '?'} 天` : '未认证' }}
      </span>
    </div>

    <div v-if="state.auth?.cooldown?.blocked" class="notice warn">
      <span>
        认证连续失败 {{ state.auth.cooldown.failures }} 次，已进入冷却，
        {{ state.auth.cooldown.remaining_seconds }} 秒后可再试（避免把学号撞学校风控）。
      </span>
    </div>

    <section class="grid-2">
      <div class="panel">
        <div class="panel-head">
          <h2>一键认证</h2>
          <span class="hint">调用学校 signin，不校验密码</span>
        </div>
        <div class="panel-body">
          <div class="field">
            <label>学号（网页版登录名）</label>
            <div class="row">
              <input
                v-model="email"
                class="input mono grow"
                placeholder="例如 1023005562"
                @keyup.enter="login"
              />
              <button class="btn primary" :disabled="busy || !email" @click="login">
                {{ busy ? '认证中…' : '认证' }}
              </button>
            </div>
          </div>
          <div class="row">
            <a class="btn ghost sm" :href="schoolUrl" target="_blank" rel="noreferrer">打开学校页面 ↗</a>
            <span class="hint-text">用学号换 token 后，会写入 config.json 与 data/token</span>
          </div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <h2>凭证状态</h2>
          <span class="hint">指纹用于确认当前用的是哪一份</span>
        </div>
        <div class="panel-body">
          <div class="kv" style="grid-template-columns: 96px 1fr">
            <div class="k">有效性</div>
            <div class="v plain" :class="state.token?.present ? 'ok' : 'bad'">
              {{ state.token?.present ? '有效' : '无效' }}
            </div>
            <div class="k">指纹</div>
            <div class="v">{{ state.auth?.fingerprint || '—' }}</div>
            <div class="k">剩余天数</div>
            <div class="v">{{ state.token?.days_left ?? '—' }}</div>
            <div class="k">到期时间</div>
            <div class="v">{{ (state.token?.expires_at || '—').replace('T', ' ').replace('Z', '') }}</div>
            <div class="k">当前用户</div>
            <div class="v">{{ userText }}</div>
            <div class="k">绑定学号</div>
            <div class="v">{{ state.auth?.email || '未设置' }}</div>
          </div>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2>自动续期</h2>
        <span class="hint">剩余不足 {{ state.auth?.refresh_before_days }} 天自动换新</span>
        <span class="grow" />
        <button class="btn primary sm" :disabled="busy || !state.auth?.enabled" @click="refresh">立即续期</button>
      </div>
      <div class="panel-body">
        <div class="kv" style="grid-template-columns: 96px 1fr">
          <div class="k">开关</div>
          <div class="v plain" :class="state.auth?.auto_refresh ? 'ok' : 'warn'">
            {{ state.auth?.auto_refresh ? '已开启' : '已关闭' }}
          </div>
          <div class="k">检查间隔</div>
          <div class="v">每 {{ state.auth?.check_interval_minutes }} 分钟</div>
          <div class="k">已续期</div>
          <div class="v">{{ state.auth?.refresh_count ?? 0 }} 次</div>
          <div class="k">上次成功</div>
          <div class="v">{{ formatTime(state.auth?.last_success) }}</div>
        </div>
        <div v-if="state.auth?.last_error" class="notice bad" style="margin-top: 12px">
          {{ state.auth.last_error }}
        </div>
      </div>
    </section>

    <section class="grid-2">
      <div class="panel">
        <div class="panel-head">
          <h2>手动粘贴 token</h2>
          <span class="hint">学校接口变动时的兜底</span>
        </div>
        <div class="panel-body">
          <div class="field">
            <label>F12 → Application → Local Storage → 键 <code>token</code> 的值</label>
            <input v-model="token" class="input mono" placeholder="eyJhbGciOi..." @keyup.enter="saveToken" />
          </div>
          <div class="row">
            <button class="btn" :disabled="busy || !token" @click="saveToken">保存并验证</button>
            <span class="hint-text">保存前会调一次 /api/v1/auths/ 验证是否可用</span>
          </div>
          <div v-if="mismatch" class="notice warn" style="margin-top: 12px">
            这份 token 属于 <b>{{ state.auth?.email }}</b> 之外的账号，请确认是否贴错。
          </div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <h2>认证历史</h2>
          <span class="hint">最近 15 条</span>
          <span class="grow" />
          <button class="btn sm ghost" @click="clearEvents">清空</button>
        </div>
        <div class="panel-body tight">
          <div class="scroll thin">
            <table class="tbl">
              <thead>
                <tr><th>时间</th><th>动作</th><th>结果</th><th>说明</th></tr>
              </thead>
              <tbody>
                <tr v-if="!events.length"><td colspan="4" class="empty">还没有记录</td></tr>
                <tr v-for="event in events" :key="event.id">
                  <td class="mono">{{ event.time }}</td>
                  <td>{{ event.kind }}</td>
                  <td :class="event.ok ? 'ok' : 'bad'">{{ event.ok ? '成功' : '失败' }}</td>
                  <td class="muted">{{ event.detail || event.email || '—' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </section>
  </main>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { loadSystem, useSystemStore } from '../stores/system'
import { useUi } from '../stores/ui'

const { state } = useSystemStore()
const { toast } = useUi()

const email = ref(state.auth?.email || '')
const token = ref('')
const busy = ref(false)
const events = ref([])
const mismatch = ref(false)

const schoolUrl = computed(() => state.auth?.school_url || '/auth/school')
const userText = computed(() => {
  const user = state.info?.user
  if (!user) return '—'
  return `${user.email || user.id || '—'}${user.name ? ` (${user.name})` : ''}`
})

const formatTime = (value) => (value ? String(value).replace('T', ' ').replace('Z', '') : '—')

async function loadEvents() {
  const resp = await api.authEvents(15)
  if (resp.ok) events.value = resp.data.items
}

async function login() {
  busy.value = true
  const resp = await api.login(email.value.trim())
  busy.value = false
  if (!resp.ok) {
    toast(`认证失败：${resp.error}`, 'error')
    await Promise.all([loadSystem(), loadEvents()])
    return
  }
  toast(`认证成功，token 剩 ${resp.data.token?.days_left ?? '?'} 天`, 'success')
  await Promise.all([loadSystem(), loadEvents()])
}

async function saveToken() {
  busy.value = true
  const resp = await api.saveToken(token.value.trim())
  busy.value = false
  if (!resp.ok) {
    toast(`保存失败：${resp.error}`, 'error')
    await loadEvents()
    return
  }
  mismatch.value = Boolean(resp.data.email_mismatch)
  token.value = ''
  toast('token 已保存并验证通过', 'success')
  await Promise.all([loadSystem(), loadEvents()])
}

async function refresh() {
  busy.value = true
  const resp = await api.refresh()
  busy.value = false
  if (!resp.ok) {
    toast(`续期失败：${resp.error}`, 'error')
    await loadEvents()
    return
  }
  toast(`续期成功，剩 ${resp.data.token?.days_left ?? '?'} 天`, 'success')
  await Promise.all([loadSystem(), loadEvents()])
}

async function clearEvents() {
  if (!window.confirm('清空认证历史？')) return
  await api.clearAuthEvents()
  events.value = []
  toast('已清空', 'success')
}

onMounted(loadEvents)
</script>
