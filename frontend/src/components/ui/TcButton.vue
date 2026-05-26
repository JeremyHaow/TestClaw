<script setup lang="ts">
import { computed } from 'vue'
import { LoaderCircle } from 'lucide-vue-next'

defineOptions({ inheritAttrs: false })

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
type ButtonSize = 'sm' | 'md' | 'lg'

const props = withDefaults(defineProps<{
  variant?: ButtonVariant
  size?: ButtonSize
  type?: 'button' | 'submit' | 'reset'
  disabled?: boolean
  loading?: boolean
  block?: boolean
}>(), {
  variant: 'primary',
  size: 'md',
  type: 'button',
  disabled: false,
  loading: false,
  block: false,
})

const baseClasses = 'inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-lg font-semibold transition disabled:cursor-not-allowed disabled:opacity-60'

const sizeClasses: Record<ButtonSize, string> = {
  sm: 'min-h-8 px-3 py-1.5 text-xs',
  md: 'min-h-10 px-4 py-2 text-sm',
  lg: 'min-h-11 px-5 py-2.5 text-sm',
}

const variantClasses: Record<ButtonVariant, string> = {
  primary: 'bg-blue-600 text-white shadow-[0_8px_24px_rgba(37,99,235,0.18)] hover:bg-blue-700',
  secondary: 'border border-slate-200 bg-white text-slate-700 hover:border-slate-300 hover:bg-slate-50',
  ghost: 'bg-transparent text-slate-600 hover:bg-slate-100 hover:text-slate-950',
  danger: 'border border-red-200 bg-red-50 text-red-700 hover:border-red-300 hover:bg-red-100',
}

const classes = computed(() => [
  baseClasses,
  sizeClasses[props.size],
  variantClasses[props.variant],
  props.block ? 'w-full' : '',
])
</script>

<template>
  <button
    v-bind="$attrs"
    :type="type"
    :disabled="disabled || loading"
    :class="classes"
  >
    <LoaderCircle v-if="loading" :size="16" class="animate-spin" aria-hidden="true" />
    <slot name="leading" />
    <slot />
    <slot name="trailing" />
  </button>
</template>
