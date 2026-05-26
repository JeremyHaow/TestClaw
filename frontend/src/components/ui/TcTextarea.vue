<script setup lang="ts">
import { computed, useAttrs } from 'vue'

defineOptions({ inheritAttrs: false })

type TextareaVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
type TextareaSize = 'sm' | 'md' | 'lg'
type TextareaResize = 'none' | 'vertical' | 'both'

const props = withDefaults(defineProps<{
  modelValue?: string
  label?: string
  hint?: string
  error?: string
  variant?: TextareaVariant
  size?: TextareaSize
  resize?: TextareaResize
  rows?: number
  disabled?: boolean
}>(), {
  modelValue: '',
  label: '',
  hint: '',
  error: '',
  variant: 'secondary',
  size: 'md',
  resize: 'vertical',
  rows: 4,
  disabled: false,
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
  input: [event: Event]
  focus: [event: FocusEvent]
  blur: [event: FocusEvent]
}>()

const attrs = useAttrs()

const rootClass = computed(() => attrs.class)

const textareaAttrs = computed(() => {
  const { class: _class, ...rest } = attrs
  return rest
})

const sizeClasses: Record<TextareaSize, string> = {
  sm: 'px-3 py-2 text-xs leading-5',
  md: 'px-3 py-2.5 text-sm leading-6',
  lg: 'px-4 py-3 text-sm leading-6',
}

const resizeClasses: Record<TextareaResize, string> = {
  none: 'resize-none',
  vertical: 'resize-y',
  both: 'resize',
}

const variantClasses: Record<TextareaVariant, string> = {
  primary: 'border-blue-200 bg-blue-50/40 focus:border-blue-500 focus:ring-blue-100',
  secondary: 'border-[#E5EAF3] bg-white focus:border-blue-500 focus:ring-blue-100',
  ghost: 'border-transparent bg-slate-50 focus:border-blue-500 focus:bg-white focus:ring-blue-100',
  danger: 'border-red-300 bg-red-50 focus:border-red-500 focus:ring-red-100',
}

const textareaClasses = computed(() => [
  'min-h-[88px] w-full rounded-lg border text-slate-900 outline-none transition placeholder:text-slate-400 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400',
  sizeClasses[props.size],
  resizeClasses[props.resize],
  props.error ? variantClasses.danger : variantClasses[props.variant],
  'focus:ring-4',
])

function onInput(event: Event) {
  emit('update:modelValue', (event.target as HTMLTextAreaElement).value)
  emit('input', event)
}
</script>

<template>
  <label class="block" :class="rootClass">
    <span v-if="label || $slots.label" class="mb-1.5 block text-sm font-semibold text-slate-700">
      <slot name="label">{{ label }}</slot>
    </span>
    <textarea
      v-bind="textareaAttrs"
      :value="modelValue"
      :rows="rows"
      :disabled="disabled"
      :class="textareaClasses"
      @input="onInput"
      @focus="emit('focus', $event)"
      @blur="emit('blur', $event)"
    />
    <span v-if="error || $slots.error" class="mt-1.5 block text-xs font-semibold text-red-600">
      <slot name="error">{{ error }}</slot>
    </span>
    <span v-else-if="hint || $slots.hint" class="mt-1.5 block text-xs leading-5 text-slate-500">
      <slot name="hint">{{ hint }}</slot>
    </span>
  </label>
</template>
