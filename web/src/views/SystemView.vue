<template>
  <main class="main">
    <div class="page-head">
      <div>
        <h1>系统信息</h1>
        <p>版本、监听地址、熔断状态与本地 key</p>
      </div>
      <span class="hint-text">v{{ state.info?.version }}</span>
    </div>

    <section class="grid-2">
      <div class="panel">
        <div class="panel-head"><h2>服务</h2></div>
        <div class="panel-body">
          <div class="kv" style="grid-template-columns: 96px 1fr">
            <div class="k">版本</div>
            <div class="v">{{ state.info?.version }}</div>
            <div class="k">监听</div>
            <div class="v">{{ state.info?.listen }}</div>
            <div class="k">上游</div>
            <div class="v">{{ state.info?.upstream }}</div>
            <div class="k">本地 key</div>
            <div class="v">{{ state.info?.client_api_key }}</div>
            <div class="k">输出上限</div>
            <div class="v">{{ state.info?.max_tokens_cap }}</div>
            <div class="k">配置文件</div>
            <div class="v">{{ state.info?.config_path }}</div>
            <div class="k">数据库</div>
            <div class="v">{{ state.info?.usage?.db_path }}</div>
            <div class="k">仓库</div>
            <div class="v">
              <a class="repo-link" :href="state.info?.repo_url" target="_blank" rel="noreferrer">
                <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true" fill="currentColor">
                  <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38
                           0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13
                           -.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66
                           .07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15
                           -.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.4 7.4 0 0 1 2-.27c.68 0
                           1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82
                           2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01
                           2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z"/>
                </svg>
                Song-xuan-xuan/wut-deepseek2api
              </a>
            </div>
          </div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head"><h2>熔断与网络</h2></div>
        <div class="panel-body">
          <div class="kv" style="grid-template-columns: 96px 1fr">
            <div class="k">状态</div>
            <div class="v plain" :class="state.circuit?.open ? 'warn' : 'ok'">
              {{ state.circuit?.open ? `冷却 ${state.circuit.remaining_seconds}s` : '正常' }}
            </div>
            <div class="k">连续失败</div>
            <div class="v">{{ state.circuit?.consecutive_failures ?? 0 }}</div>
            <div class="k">触发阈值</div>
            <div class="v">{{ state.circuit?.threshold }} 次</div>
            <div class="k">冷却时长</div>
            <div class="v">{{ state.circuit?.cooldown_seconds }}s</div>
            <div class="k">已隔离模型</div>
            <div class="v">
              <template v-if="blockedModels.length">
                <span v-for="(item, i) in blockedModels" :key="item.model" class="warn">
                  {{ item.model }}（{{ Math.round(item.seconds / 60) }} 分钟后恢复）<template v-if="i < blockedModels.length - 1">、</template>
                </span>
              </template>
              <span v-else class="muted">无</span>
            </div>
            <div class="k">健康检查</div>
            <div class="v"><a href="/health" target="_blank" rel="noreferrer">/health</a></div>
            <div class="k">接口文档</div>
            <div class="v"><a href="/docs" target="_blank" rel="noreferrer">/docs</a></div>
          </div>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2>浏览器端 API Key</h2>
        <span class="hint">只是本浏览器调用面板接口时带的凭证</span>
      </div>
      <div class="panel-body">
        <p class="hint-text" style="margin: 0 0 10px">
          面板的请求要带 <code>Authorization</code> 头。从本机打开（{{
            state.info?.listen
          }}）时服务端直接放行，<b>这一栏用不到</b>；
          只有你从<b>别的设备</b>（手机 / 另一台电脑，用局域网 IP 或域名访问）打开面板时，
          才需要填这里，值必须是 <code>config.json</code> 里的 <code>client_api_key</code>。
        </p>
        <div class="row">
          <input v-model="key" class="input mono" style="max-width: 360px" />
          <button class="btn" @click="saveKey">保存到浏览器</button>
          <span class="hint-text">
            状态：<b>{{ state.info?.panel_key_needed ? '当前不是本机访问，需要填' : '当前是本机访问，不需要填' }}</b>
          </span>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2>常用命令</h2>
        <span class="hint">点右侧复制</span>
      </div>
      <div class="panel-body">
        <div class="kv" style="grid-template-columns: 140px 1fr auto">
          <template v-for="cmd in commands" :key="cmd.key">
            <div class="k">{{ cmd.key }}</div>
            <div class="v">{{ cmd.value }}</div>
            <button class="btn sm ghost" @click="copy(cmd.value)">复制</button>
          </template>
        </div>
      </div>
    </section>
  </main>
</template>

<script setup>
import { computed, ref } from 'vue'
import { getApiKey } from '../api'
import { useSystemStore } from '../stores/system'
import { useUi } from '../stores/ui'

const { state } = useSystemStore()
const { toast, setApiKey } = useUi()
const key = ref(getApiKey())
const blockedModels = computed(() =>
  Object.entries(state.info?.model_guard?.blocked || {}).map(([model, seconds]) => ({ model, seconds })),
)

const commands = [
  { key: '启动', value: '.venv/bin/python -m app' },
  { key: '停止', value: 'lsof -ti:8787 | xargs kill' },
  { key: '自检', value: "curl --noproxy '*' -s 127.0.0.1:8787/health" },
  { key: '手动续期', value: "curl -X POST 127.0.0.1:8787/api/auth/refresh" },
  { key: '重建前端', value: 'cd web && npm run build' },
  { key: 'Docker 启动', value: 'docker compose up -d --build' },
]

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text)
    toast('已复制', 'success')
  } catch {
    toast('复制失败', 'error')
  }
}

function saveKey() {
  setApiKey(key.value.trim())
  toast('已保存到浏览器', 'success')
}
</script>
