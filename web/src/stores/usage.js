import { reactive } from 'vue'
import { api } from '../api'

/** 用量 store：区间、模型筛选、聚合数据。 */

export const state = reactive({
  loading: false,
  error: '',
  range: localStorage.getItem('whut-2api-range') || '7d',
  model: '',
  data: null,
  updatedAt: '',
})

export async function loadUsage() {
  state.loading = true
  const resp = await api.usage(state.range, state.model)
  state.loading = false
  if (!resp.ok) {
    state.error = resp.error
    return
  }
  state.error = ''
  state.data = resp.data
  state.updatedAt = new Date().toLocaleTimeString()
}

export function setRange(range) {
  state.range = range
  localStorage.setItem('whut-2api-range', range)
  return loadUsage()
}

export function setModel(model) {
  state.model = model
  return loadUsage()
}

export function useUsageStore() {
  return { state, reload: loadUsage }
}
