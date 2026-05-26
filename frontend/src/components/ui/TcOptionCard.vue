<script setup lang="ts">
import { computed } from 'vue'
import { Check } from 'lucide-vue-next'

type OptionVariant = 'primary' | 'secondary' | 'ghost' | 'danger'

const props = withDefaults(defineProps<{
  title?: string
  description?: string
  meta?: string
  variant?: OptionVariant
  selected?: boolean
  disabled?: boolean
  value?: string | number | boolean | null
}>(), {
  title: '',
  description: '',
  meta: '',
  variant: 'secondary',
  selected: false,
  disabled: false,
  value: null,
})

const emit = defineEmits<{
  select: [value: string | number | boolean | null]
}>()

const baseClasses = 'group flex min-h-[96px] w-full items-start gap-3 rounded-xl border p-3 text-left transition disabled:cursor-not-allowed disabled:opacity-60'

const variantClasses: Record<OptionVariant, string> = {
  primary: 'border-blue-200 bg-blue-50 text-slate-950 hover:border-blue-300',
  secondary: 'border-[#E5EAF3] bg-white text-slate-950 hover:border-blue-200 hover:bg-blue-50/40',
  ghost: 'border-transparent bg-transparent text-slate-700 hover:bg-slate-100',
  danger: 'border-red-200 bg-red-50 text-red-800 hover:border-red-300',
}

const classes = computed(() => [
  baseClasses,
  props.selected
    ? 'border-blue-600 bg-blue-50 ring-2 ring-blue-100'
    : variantClasses[props.variant],
])

function select() {
  if (!props.disabled) emit('select', props.value)
}
</script>

<template>
  <button
    type="button"
    :disabled="disabled"
    :aria-pressed="selected"
    :class="classes"
    @click="select"
  >
    <div class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500">
      <slot name="icon">
        <Check v-if="selected" :size="18" class="text-blue-600" />
      </slot>
    </div>
    <div class="min-w-0 flex-1">
      <div class="flex items-start justify-between gap-3">
        <div v-if="title || $slots.title" class="text-sm font-semibold leading-5 text-slate-950">
          <slot name="title">{{ title }}</slot>
        </div>
        <div v-if="meta || $slots.meta" class="shrink-0 text-xs font-semibold text-slate-400">
          <slot name="meta">{{ meta }}</slot>
        </div>
      </div>
      <div v-if="description || $slots.description" class="mt-1 text-xs leading-5 text-slate-500">
        <slot name="description">{{ description }}</slot>
      </div>
      <div v-if="$slots.default" class="mt-3">
        <slot />
      </div>
    </div>
  </button>
</template>
