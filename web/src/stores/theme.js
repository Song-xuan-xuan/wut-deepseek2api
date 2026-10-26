import { computed, ref } from 'vue'

/** 主题：light / dark / system，持久化到 localStorage，和 axonhub 一样切 class。 */

const STORAGE_KEY = 'whut-2api-theme'
const MODES = ['light', 'dark', 'system']

const mode = ref(localStorage.getItem(STORAGE_KEY) || 'system')
const media = window.matchMedia('(prefers-color-scheme: dark)')
const systemDark = ref(media.matches)

media.addEventListener('change', (event) => {
  systemDark.value = event.matches
  if (mode.value === 'system') apply()
})

const resolved = computed(() => (mode.value === 'system' ? (systemDark.value ? 'dark' : 'light') : mode.value))

function apply() {
  const root = document.documentElement
  root.classList.toggle('dark', resolved.value === 'dark')
  root.style.colorScheme = resolved.value
  document.querySelector("meta[name='theme-color']")?.setAttribute(
    'content',
    resolved.value === 'dark' ? '#12161c' : '#f7f7f5',
  )
}

function setMode(next) {
  mode.value = MODES.includes(next) ? next : 'system'
  localStorage.setItem(STORAGE_KEY, mode.value)
  apply()
}

function cycle() {
  const index = MODES.indexOf(mode.value)
  setMode(MODES[(index + 1) % MODES.length])
}

apply()

export function useTheme() {
  return { mode, resolved, setMode, cycle, modes: MODES }
}
