import { reactive, readonly } from 'vue'

/** 全局轻量 store：消息提示 + 本地 key。 */

const state = reactive({
  toasts: [],
  apiKey: localStorage.getItem('whut-2api-key') || 'sk-whut-local',
})

let seq = 0

export function useUi() {
  function toast(message, type = 'info') {
    const id = ++seq
    state.toasts.push({ id, message, type })
    setTimeout(() => {
      const index = state.toasts.findIndex((item) => item.id === id)
      if (index >= 0) state.toasts.splice(index, 1)
    }, 2600)
  }

  function setApiKey(key) {
    state.apiKey = key
    localStorage.setItem('whut-2api-key', key)
  }

  return { state: readonly(state), toast, setApiKey }
}

export const ui = state
