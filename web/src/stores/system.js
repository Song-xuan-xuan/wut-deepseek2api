import { computed, onMounted, onUnmounted, reactive } from 'vue'
import { api } from '../api'

/**
 * 系统状态 store：服务信息、认证状态、模型列表。
 * 单例 —— 多个视图共享同一份数据，避免重复轮询。
 */

const state = reactive({
  loading: true,
  error: '',
  info: null,
  auth: null,
  token: null,
  circuit: null,
  models: [],
  updatedAt: '',
})

let timer = null
let subscribers = 0

export async function loadSystem() {
  const [infoResp, authResp] = await Promise.all([api.info(), api.authStatus()])
  if (!infoResp.ok) {
    state.error = infoResp.error
    state.loading = false
    return
  }
  state.error = ''
  state.info = infoResp.data.service
  state.models = infoResp.data.models || []
  state.circuit = infoResp.data.circuit
  state.usage = infoResp.data.usage
  state.token = authResp.ok ? authResp.data.token : infoResp.data.token
  state.auth = authResp.ok ? authResp.data.auth : infoResp.data.auth
  state.updatedAt = new Date().toLocaleTimeString()
  state.loading = false
}

export function useSystemStore() {
  onMounted(() => {
    subscribers += 1
    if (!timer) {
      loadSystem()
      timer = setInterval(loadSystem, 5000)
    }
  })
  onUnmounted(() => {
    subscribers -= 1
    if (subscribers <= 0 && timer) {
      clearInterval(timer)
      timer = null
    }
  })

  const healthy = computed(() => {
    const tokenOk = state.token?.present
    const modelsOk = state.models.length > 0
    return Boolean(tokenOk && modelsOk && !state.circuit?.open)
  })

  return { state, healthy, reload: loadSystem }
}
