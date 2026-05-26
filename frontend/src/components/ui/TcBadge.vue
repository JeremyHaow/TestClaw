<script setup lang="ts">
import { computed } from 'vue'

type BadgeTone =
  | 'primary'
  | 'secondary'
  | 'ghost'
  | 'danger'
  | 'gray'
  | 'blue'
  | 'green'
  | 'orange'
  | 'red'
  | 'purple'
  | 'draft'
  | 'pending'
  | 'ready'
  | 'running'
  | 'blocked'
  | 'warning'
  | 'passed'
  | 'failed'
  | 'skipped'
  | 'bug_found'
  | 'cancelled'
  | 'success'

type BadgeSize = 'sm' | 'md'

const props = withDefaults(defineProps<{
  tone?: BadgeTone
  size?: BadgeSize
  label?: string
  dot?: boolean
}>(), {
  tone: 'gray',
  size: 'sm',
  label: '',
  dot: false,
})

const toneClasses: Record<BadgeTone, string> = {
  primary: 'border-blue-200 bg-blue-50 text-blue-700',
  secondary: 'border-slate-200 bg-slate-50 text-slate-600',
  ghost: 'border-transparent bg-transparent text-slate-500',
  danger: 'border-red-200 bg-red-50 text-red-700',
  gray: 'border-slate-200 bg-slate-50 text-slate-600',
  blue: 'border-blue-200 bg-blue-50 text-blue-700',
  green: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  orange: 'border-amber-200 bg-amber-50 text-amber-700',
  red: 'border-red-200 bg-red-50 text-red-700',
  purple: 'border-violet-200 bg-violet-50 text-violet-700',
  draft: 'border-slate-200 bg-slate-50 text-slate-600',
  pending: 'border-blue-200 bg-white text-blue-700',
  ready: 'border-blue-200 bg-blue-50 text-blue-700',
  running: 'border-blue-200 bg-blue-50 text-blue-700',
  blocked: 'border-red-200 bg-red-50 text-red-700',
  warning: 'border-amber-200 bg-amber-50 text-amber-700',
  passed: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  failed: 'border-red-200 bg-red-50 text-red-700',
  skipped: 'border-slate-200 bg-slate-50 text-slate-500',
  bug_found: 'border-violet-200 bg-violet-50 text-violet-700',
  cancelled: 'border-slate-200 bg-slate-50 text-slate-500',
  success: 'border-emerald-200 bg-emerald-50 text-emerald-700',
}

const sizeClasses: Record<BadgeSize, string> = {
  sm: 'px-2 py-0.5 text-[11px]',
  md: 'px-2.5 py-1 text-xs',
}

const dotClasses = computed(() => {
  if (['green', 'passed', 'success'].includes(props.tone)) return 'bg-emerald-500'
  if (['orange', 'warning', 'running'].includes(props.tone)) return 'bg-amber-500'
  if (['red', 'danger', 'failed', 'blocked'].includes(props.tone)) return 'bg-red-500'
  if (['purple', 'bug_found'].includes(props.tone)) return 'bg-violet-500'
  if (['blue', 'primary', 'ready', 'pending'].includes(props.tone)) return 'bg-blue-500'
  return 'bg-slate-400'
})

const classes = computed(() => [
  'inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border font-semibold',
  toneClasses[props.tone],
  sizeClasses[props.size],
])
</script>

<template>
  <span :class="classes">
    <span v-if="dot" class="h-1.5 w-1.5 rounded-full" :class="dotClasses" />
    <slot>{{ label }}</slot>
  </span>
</template>
