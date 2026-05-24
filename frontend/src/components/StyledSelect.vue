<script setup lang="ts">
import { computed, useAttrs } from 'vue'
import { ChevronDown } from 'lucide-vue-next'

defineOptions({ inheritAttrs: false })

const props = withDefaults(defineProps<{
  modelValue?: string | number | null
  disabled?: boolean
  size?: 'sm' | 'md'
  modelModifiers?: Record<string, boolean>
}>(), {
  disabled: false,
  size: 'md',
  modelModifiers: () => ({}),
})

const emit = defineEmits<{
  'update:modelValue': [value: string | number]
  change: [event: Event]
}>()

const attrs = useAttrs()
const rootClass = computed(() => attrs.class)
const selectAttrs = computed(() => {
  const { class: _class, ...rest } = attrs
  return rest
})

function handleChange(event: Event) {
  const rawValue = (event.target as HTMLSelectElement).value
  const value = props.modelModifiers?.number && rawValue !== '' ? Number(rawValue) : rawValue
  emit('update:modelValue', value)
  emit('change', event)
}
</script>

<template>
  <div class="tc-select-wrap" :class="rootClass">
    <select
      v-bind="selectAttrs"
      :value="modelValue ?? ''"
      :disabled="disabled"
      class="tc-select"
      :class="size === 'sm' ? 'tc-select-sm' : 'tc-select-md'"
      @change="handleChange"
    >
      <slot />
    </select>
    <ChevronDown :size="15" class="tc-select-icon" aria-hidden="true" />
  </div>
</template>
