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
        <span class="hint">只影响这个面板的请求，不改服务端配置</span>
      </div>
      <div class="panel-body">
        <div class="row">
          <input v-model="key" class="input mono" style="max-width: 360px" />
          <button class="btn" @click="saveKey">保存到浏览器</button>
          <span class="hint-text">
            服务端真正的 key 在 config.json 的 <code>client_api_key</code>
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
import { ref } from 'vue'
import { getApiKey } from '../api'
import { useSystemStore } from '../stores/system'
import { useUi } from '../stores/ui'

const { state } = useSystemStore()
const { toast, setApiKey } = useUi()
const key = ref(getApiKey())

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
