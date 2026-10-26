<template>
  <div class="chart" ref="wrap">
    <div class="legend" style="margin-bottom: 12px">
      <button
        v-for="item in legend"
        :key="item.key"
        :class="{ off: item.hidden }"
        @click="toggle(item.key)"
      >
        <i :style="{ background: `var(--chart-${item.colorIndex})` }" />{{ item.label }}
      </button>
    </div>

    <svg :viewBox="`0 0 ${W} ${H}`" preserveAspectRatio="none" :style="{ height: `${height}px` }">
      <defs>
        <linearGradient
          v-for="item in visible"
          :key="`g-${item.key}`"
          :id="`grad-${uid}-${item.key}`"
          x1="0"
          y1="0"
          x2="0"
          y2="1"
        >
          <stop offset="0%" :stop-color="`var(--chart-${item.colorIndex})`" stop-opacity="0.3" />
          <stop offset="100%" :stop-color="`var(--chart-${item.colorIndex})`" stop-opacity="0" />
        </linearGradient>
      </defs>

      <g>
        <template v-for="(tick, index) in ticks" :key="`t-${index}`">
          <line class="grid-line" :x1="pad.left" :x2="W - pad.right" :y1="tick.y" :y2="tick.y" />
          <text class="axis-label" :x="pad.left - 8" :y="tick.y + 3" text-anchor="end">{{ tick.left }}</text>
          <text
            v-if="hasRightAxis"
            class="axis-label"
            :x="W - pad.right + 8"
            :y="tick.y + 3"
            text-anchor="start"
          >
            {{ tick.right }}
          </text>
        </template>
      </g>

      <g v-for="item in visible" :key="item.key">
        <template v-if="item.points.length > 1">
          <path :d="areaPath(item)" :fill="`url(#grad-${uid}-${item.key})`" stroke="none" />
          <path
            :d="linePath(item)"
            fill="none"
            :stroke="`var(--chart-${item.colorIndex})`"
            stroke-width="2"
            stroke-linejoin="round"
            stroke-linecap="round"
            vector-effect="non-scaling-stroke"
          />
        </template>
        <!-- 只有一个数据点时，画一个点 + 一条虚线基准，避免"空白图" -->
        <template v-else-if="item.points.length === 1">
          <line
            :x1="pad.left"
            :x2="W - pad.right"
            :y1="item.points[0].y"
            :y2="item.points[0].y"
            :stroke="`var(--chart-${item.colorIndex})`"
            stroke-width="1.5"
            stroke-dasharray="4 4"
            opacity="0.55"
            vector-effect="non-scaling-stroke"
          />
          <circle
            :cx="item.points[0].x"
            :cy="item.points[0].y"
            r="4.5"
            :fill="`var(--chart-${item.colorIndex})`"
            stroke="var(--card)"
            stroke-width="2"
            vector-effect="non-scaling-stroke"
          />
        </template>
      </g>

      <g>
        <text
          v-for="(label, index) in xLabels"
          :key="`x-${index}`"
          class="axis-label"
          :x="label.x"
          :y="H - 6"
          :text-anchor="label.anchor"
        >
          {{ label.text }}
        </text>
      </g>

      <g v-if="hover !== null && visible.length">
        <line class="cursor-line" :x1="hoverX" :x2="hoverX" :y1="pad.top" :y2="H - pad.bottom" />
        <circle
          v-for="item in visible.filter((s) => s.points.length > 1)"
          :key="`h-${item.key}`"
          :cx="hoverX"
          :cy="item.points[hover].y"
          r="3.6"
          :fill="`var(--chart-${item.colorIndex})`"
          stroke="var(--card)"
          stroke-width="1.5"
          vector-effect="non-scaling-stroke"
        />
      </g>

      <rect
        :x="pad.left"
        :y="pad.top"
        :width="W - pad.left - pad.right"
        :height="H - pad.top - pad.bottom"
        fill="transparent"
        @mousemove="onMove"
        @mouseleave="hover = null"
      />
    </svg>

    <div v-if="hover !== null && visible.length" class="chart-tip" :style="tipStyle">
      <div class="t">{{ labels[hover] }}</div>
      <div v-for="item in visible" :key="`tip-${item.key}`" class="l">
        <i :style="{ background: `var(--chart-${item.colorIndex})` }" />
        <span>{{ item.label }}</span>
        <b style="margin-left: auto">{{ format(item.values[hover] || 0) }}</b>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  /** [{ key, label, values: number[] }] */
  series: { type: Array, default: () => [] },
  labels: { type: Array, default: () => [] },
  height: { type: Number, default: 220 },
  format: { type: Function, default: (value) => String(Math.round(value)) },
})

const W = 720
// 画布高度：等于组件高度，坐标系统一后 viewBox 才有效
const H = computed(() => Math.max(props.height, 80))
const pad = { top: 14, right: 52, bottom: 26, left: 48 }
const uid = Math.random().toString(36).slice(2, 8)
const hidden = ref(new Set())
const hover = ref(null)
const wrap = ref(null)

const legend = computed(() =>
  props.series.map((item, index) => ({
    key: item.key,
    label: item.label,
    colorIndex: (index % 6) + 1,
    hidden: hidden.value.has(item.key),
  })),
)

const hasRightAxis = computed(() => props.series.some((item) => item.axis === 'right'))

function niceMax(values) {
  const max = Math.max(...values, 1)
  const magnitude = 10 ** Math.floor(Math.log10(max))
  return Math.ceil(max / magnitude) * magnitude
}

const maxLeft = computed(() =>
  niceMax(
    props.series
      .filter((item) => !hidden.value.has(item.key) && item.axis !== 'right')
      .flatMap((item) => item.values),
  ),
)
const maxRight = computed(() =>
  niceMax(
    props.series
      .filter((item) => !hidden.value.has(item.key) && item.axis === 'right')
      .flatMap((item) => item.values),
  ),
)

const innerW = W - pad.left - pad.right
const innerH = computed(() => H.value - pad.top - pad.bottom)
const count = computed(() => props.labels.length)

function xAt(index) {
  if (count.value <= 1) return pad.left + innerW / 2
  return pad.left + (index / (count.value - 1)) * innerW
}
function yAt(value, axis = 'left') {
  const max = axis === 'right' ? maxRight.value : maxLeft.value
  const ratio = max ? value / max : 0
  return pad.top + innerH.value * (1 - Math.min(Math.max(ratio, 0), 1))
}

const visible = computed(() =>
  props.series
    .map((item, index) => ({
      key: item.key,
      label: item.label,
      values: item.values,
      colorIndex: (index % 6) + 1,
      axis: item.axis === 'right' ? 'right' : 'left',
      points: item.values.map((value, i) => ({ x: xAt(i), y: yAt(value, item.axis), value })),
    }))
    .filter((item) => !hidden.value.has(item.key)),
)

const ticks = computed(() => {
  const steps = 4
  return Array.from({ length: steps + 1 }, (_, index) => {
    const ratio = (steps - index) / steps
    return {
      y: pad.top + innerH.value * (1 - ratio),
      left: shortNumber(maxLeft.value * ratio),
      right: shortNumber(maxRight.value * ratio),
    }
  })
})

const xLabels = computed(() => {
  const labels = props.labels
  if (!labels.length) return []
  const step = Math.max(1, Math.ceil(labels.length / 6))
  const result = []
  for (let index = 0; index < labels.length; index += step) {
    result.push({ x: xAt(index), text: String(labels[index]).slice(-5), anchor: index === 0 ? 'start' : 'middle' })
  }
  const last = labels.length - 1
  if (!result.some((item) => Math.abs(item.x - xAt(last)) < 2)) {
    result.push({ x: xAt(last), text: String(labels[last]).slice(-5), anchor: 'end' })
  }
  return result
})

function linePath(item) {
  return item.points.map((point, index) => `${index === 0 ? 'M' : 'L'}${point.x},${point.y}`).join(' ')
}
function areaPath(item) {
  if (!item.points.length) return ''
  const bottom = pad.top + innerH.value
  const first = item.points[0]
  const last = item.points.at(-1)
  return `${linePath(item)} L${last.x},${bottom} L${first.x},${bottom} Z`
}

function toggle(key) {
  const next = new Set(hidden.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  hidden.value = next
}

function shortNumber(value) {
  const n = Math.round(value)
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1000) return `${Math.round(n / 1000)}k`
  return String(n)
}

const hoverX = computed(() => (hover.value === null ? 0 : xAt(hover.value)))
const tipStyle = computed(() => {
  if (hover.value === null) return { display: 'none' }
  const ratio = hoverX.value / W
  return { left: `${Math.min(Math.max(ratio * 100, 12), 88)}%`, top: '24px' }
})

function onMove(event) {
  const rect = event.currentTarget.getBoundingClientRect()
  const ratio = (event.clientX - rect.left) / rect.width
  const index = Math.round(ratio * Math.max(count.value - 1, 1))
  hover.value = Math.min(Math.max(index, 0), Math.max(count.value - 1, 0))
}
</script>
