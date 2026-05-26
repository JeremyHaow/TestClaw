<script setup lang="ts">
import { computed } from 'vue'

defineOptions({ inheritAttrs: false })

type CardVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
type CardPadding = 'none' | 'sm' | 'md' | 'lg'

const props = withDefaults(defineProps<{
  as?: string
  variant?: CardVariant
  padding?: CardPadding
  title?: string
  subtitle?: string
  hover?: boolean
}>(), {
  as: 'section',
  variant: 'secondary',
  padding: 'md',
  title: '',
  subtitle: '',
  hover: false,
})

const baseClasses = 'rounded-xl border transition'

const variantClasses: Record<CardVariant, string> = {
  primary: 'border-blue-100 bg-white shadow-[0_8px_24px_rgba(15,23,42,0.04)]',
  secondary: 'border-[#E5EAF3] bg-white shadow-[0_8px_24px_rgba(15,23,42,0.04)]',
  ghost: 'border-transparent bg-transparent shadow-none',
  danger: 'border-red-200 bg-red-50/70 shadow-[0_8px_24px_rgba(239,68,68,0.06)]',
}

const paddingClasses: Record<CardPadding, string> = {
  none: '',
  sm: 'p-3',
  md: 'p-4',
  lg: 'p-5',
}

const classes = computed(() => [
  baseClasses,
  variantClasses[props.variant],
  paddingClasses[props.padding],
  props.hover ? 'hover:border-blue-200 hover:shadow-[0_16px_40px_rgba(15,23,42,0.08)]' : '',
])
</script>

<template>
  <component :is="as" v-bind="$attrs" :class="classes">
    <slot name="header">
      <div v-if="title || subtitle || $slots.actions" class="mb-4 flex items-start justify-between gap-3">
        <div class="min-w-0">
          <h3 v-if="title" class="truncate text-base font-semibold text-slate-950">{{ title }}</h3>
          <p v-if="subtitle" class="mt-1 text-sm leading-5 text-slate-500">{{ subtitle }}</p>
        </div>
        <div v-if="$slots.actions" class="shrink-0">
          <slot name="actions" />
        </div>
      </div>
    </slot>
    <slot />
    <div v-if="$slots.footer" class="mt-4 border-t border-slate-100 pt-4">
      <slot name="footer" />
    </div>
  </component>
</template>
