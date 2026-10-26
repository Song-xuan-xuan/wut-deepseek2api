<template>
  <div class="row">
    <span v-if="label" class="hint-text">{{ label }}</span>
    <div class="seg">
      <button
        v-for="option in options"
        :key="option.value"
        :class="{ active: modelValue === option.value }"
        @click="$emit('update:modelValue', option.value)"
      >
        {{ option.label }}
      </button>
    </div>
  </div>
</template>

<script setup>
defineProps({
  modelValue: { type: String, default: '' },
  options: { type: Array, required: true },
  label: { type: String, default: '' },
})
defineEmits(['update:modelValue'])
</script>
