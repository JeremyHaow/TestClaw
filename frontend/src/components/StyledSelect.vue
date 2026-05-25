<script setup lang="ts">
import {
  Comment,
  Fragment,
  computed,
  getCurrentInstance,
  isVNode,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  Teleport,
  useAttrs,
  useSlots,
  watch,
  type VNode,
} from 'vue'
import { Check, ChevronDown } from 'lucide-vue-next'

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
const slots = useSlots()
const instance = getCurrentInstance()
const uid = instance?.uid ?? Math.round(Math.random() * 100000)
const listboxId = `tc-select-${uid}-listbox`

type SelectOption = {
  id: string
  value: string
  label: string
  disabled: boolean
}

const rootClass = computed(() => attrs.class)
const controlId = computed(() => attrs.id != null ? String(attrs.id) : `tc-select-${uid}-button`)
const placeholderText = computed(() => attrs.placeholder != null ? String(attrs.placeholder) : '')
const modelValueString = computed(() => props.modelValue == null ? '' : String(props.modelValue))
const isRequired = computed(() => attrs.required != null && attrs.required !== false)
const controlAttrs = computed(() => {
  const {
    class: _class,
    form: _form,
    id: _id,
    name: _name,
    placeholder: _placeholder,
    required: _required,
    ...rest
  } = attrs
  return rest
})
const hiddenInputAttrs = computed(() => {
  const inputAttrs: Record<string, string | boolean> = {}
  if (attrs.name != null) inputAttrs.name = String(attrs.name)
  if (attrs.form != null) inputAttrs.form = String(attrs.form)
  if (isRequired.value) inputAttrs.required = true
  return inputAttrs
})

const options = computed<SelectOption[]>(() => (
  collectOptionNodes(slots.default?.() || []).map((vnode, index) => {
    const vnodeProps = (vnode.props || {}) as Record<string, unknown>
    const label = normalizeOptionLabel(textFromChildren(vnode.children))
    const rawValue = hasOwn(vnodeProps, 'value') ? vnodeProps.value : label
    const value = rawValue == null ? '' : String(rawValue)

    return {
      id: `${listboxId}-option-${index}`,
      value,
      label: label || value,
      disabled: isDisabledOption(vnodeProps.disabled),
    }
  })
))

const selectedIndex = computed(() => options.value.findIndex((option) => option.value === modelValueString.value))
const selectedOption = computed(() => selectedIndex.value >= 0 ? options.value[selectedIndex.value] : null)
const displayLabel = computed(() => selectedOption.value?.label || placeholderText.value)
const displayIsPlaceholder = computed(() => !selectedOption.value || (selectedOption.value.disabled && selectedOption.value.value === ''))
const activeDescendant = computed(() => (
  isOpen.value && activeIndex.value >= 0 ? options.value[activeIndex.value]?.id : undefined
))

const isOpen = ref(false)
const activeIndex = ref(-1)
const rootRef = ref<HTMLElement | null>(null)
const buttonRef = ref<HTMLButtonElement | null>(null)
const menuRef = ref<HTMLElement | null>(null)
const hiddenInputRef = ref<HTMLInputElement | null>(null)
const menuStyle = ref<Record<string, string>>({})
const committedModelValueString = ref(modelValueString.value)

watch(activeIndex, () => {
  if (isOpen.value) void nextTick(scrollActiveOptionIntoView)
})

watch(modelValueString, (value) => {
  committedModelValueString.value = value
  syncHiddenInputValue(value)
})

watch(options, () => {
  if (!isOpen.value) return
  if (!options.value.length) {
    closeListbox()
    return
  }
  if (!isSelectableIndex(activeIndex.value)) {
    setActiveToSelectedOrFirst()
  }
  void nextTick(updateMenuGeometry)
})

watch(isOpen, (open) => {
  if (open) {
    addViewportListeners()
    updateMenuGeometry()
    void nextTick(() => {
      updateMenuGeometry()
      scrollActiveOptionIntoView()
    })
  } else {
    removeViewportListeners()
  }
})

watch(() => props.disabled, (disabled) => {
  if (disabled) closeListbox()
})

function hasOwn(source: Record<string, unknown>, key: string) {
  return Object.prototype.hasOwnProperty.call(source, key)
}

function isDisabledOption(value: unknown) {
  return value === true || value === '' || value === 'true'
}

function normalizeOptionLabel(value: string) {
  return value.replace(/\s+/g, ' ').trim()
}

function collectOptionNodes(nodes: unknown, result: VNode[] = []) {
  if (!Array.isArray(nodes)) return result

  for (const node of nodes) {
    if (Array.isArray(node)) {
      collectOptionNodes(node, result)
      continue
    }

    if (!isVNode(node) || node.type === Comment) continue

    if (node.type === Fragment) {
      collectOptionNodes(node.children, result)
    } else if (node.type === 'option') {
      result.push(node)
    } else if (Array.isArray(node.children)) {
      collectOptionNodes(node.children, result)
    }
  }

  return result
}

function textFromChildren(children: unknown): string {
  if (children == null) return ''
  if (typeof children === 'string' || typeof children === 'number') return String(children)
  if (Array.isArray(children)) {
    return children.map((child) => {
      if (isVNode(child)) return textFromChildren(child.children)
      if (typeof child === 'string' || typeof child === 'number') return String(child)
      return ''
    }).join('')
  }
  if (typeof children === 'object') {
    const slot = (children as { default?: () => unknown }).default
    if (typeof slot === 'function') return textFromChildren(slot())
  }
  return ''
}

function isSelectableIndex(index: number) {
  const option = options.value[index]
  return Boolean(option && !option.disabled)
}

function findEnabledIndex(fromIndex: number, step: 1 | -1) {
  const itemCount = options.value.length
  if (!itemCount) return -1

  for (let offset = 1; offset <= itemCount; offset += 1) {
    const nextIndex = (fromIndex + (step * offset) + itemCount) % itemCount
    if (isSelectableIndex(nextIndex)) return nextIndex
  }

  return -1
}

function setActiveToSelectedOrFirst(preferredStep: 1 | -1 = 1) {
  if (isSelectableIndex(selectedIndex.value)) {
    activeIndex.value = selectedIndex.value
    return
  }

  activeIndex.value = preferredStep === 1
    ? findEnabledIndex(-1, 1)
    : findEnabledIndex(options.value.length, -1)
}

function openListbox(preferredStep: 1 | -1 = 1) {
  if (props.disabled || !options.value.length) return
  updateMenuGeometry()
  isOpen.value = true
  setActiveToSelectedOrFirst(preferredStep)
}

function closeListbox() {
  isOpen.value = false
  activeIndex.value = -1
}

function toggleListbox() {
  if (isOpen.value) closeListbox()
  else openListbox()
}

function moveActive(step: 1 | -1) {
  if (!isOpen.value) {
    openListbox(step)
    return
  }

  const startIndex = activeIndex.value >= 0
    ? activeIndex.value
    : step === 1 ? -1 : options.value.length
  const nextIndex = findEnabledIndex(startIndex, step)
  if (nextIndex >= 0) activeIndex.value = nextIndex
}

function selectOption(index: number) {
  if (props.disabled) return

  const option = options.value[index]
  if (!option || option.disabled) return

  if (option.value !== committedModelValueString.value) {
    const value = coerceModelValue(option.value)
    committedModelValueString.value = option.value
    syncHiddenInputValue(option.value)
    emit('update:modelValue', value)
    emit('change', createChangeEvent(option.value, value))
  }

  closeListbox()
  void nextTick(() => buttonRef.value?.focus())
}

function coerceModelValue(value: string) {
  return props.modelModifiers?.number && value !== '' ? Number(value) : value
}

function syncHiddenInputValue(value: string) {
  if (hiddenInputRef.value) hiddenInputRef.value.value = value
}

function createChangeEvent(rawValue: string, value: string | number) {
  const event = new CustomEvent('change', {
    bubbles: true,
    detail: { value },
  })
  const target = hiddenInputRef.value || buttonRef.value

  if (target) {
    target.value = rawValue
    try {
      Object.defineProperty(event, 'target', { configurable: true, value: target })
      Object.defineProperty(event, 'currentTarget', { configurable: true, value: target })
    } catch {
      // Some browsers keep Event target read-only; listeners can still read detail.value.
    }
  }

  return event
}

function scrollActiveOptionIntoView() {
  if (activeIndex.value < 0) return
  const activeOption = menuRef.value?.querySelector<HTMLElement>(`[data-option-index="${activeIndex.value}"]`)
  activeOption?.scrollIntoView({ block: 'nearest' })
}

function updateMenuGeometry() {
  const button = buttonRef.value
  if (!button || typeof window === 'undefined') return

  const rect = button.getBoundingClientRect()
  const viewportWidth = window.innerWidth
  const viewportHeight = window.innerHeight
  const viewportPadding = 8
  const gap = 6
  const preferredMaxHeight = 256
  const minUsableHeight = 112
  const availableWidth = Math.max(0, viewportWidth - (viewportPadding * 2))
  const menuWidth = Math.min(rect.width, availableWidth)
  const maxLeft = Math.max(viewportPadding, viewportWidth - menuWidth - viewportPadding)
  const left = Math.min(Math.max(rect.left, viewportPadding), maxLeft)
  const spaceBelow = viewportHeight - rect.bottom - viewportPadding - gap
  const spaceAbove = rect.top - viewportPadding - gap
  const opensBelow = spaceBelow >= minUsableHeight || spaceBelow >= spaceAbove
  const availableHeight = Math.max(0, opensBelow ? spaceBelow : spaceAbove)
  const maxHeight = Math.min(preferredMaxHeight, availableHeight)

  menuStyle.value = {
    left: `${left}px`,
    width: `${menuWidth}px`,
    maxHeight: `${maxHeight}px`,
    ...(opensBelow
      ? { top: `${rect.bottom + gap}px`, bottom: 'auto' }
      : { top: 'auto', bottom: `${viewportHeight - rect.top + gap}px` }),
  }
}

function addViewportListeners() {
  window.addEventListener('resize', updateMenuGeometry)
  window.addEventListener('scroll', updateMenuGeometry, true)
}

function removeViewportListeners() {
  window.removeEventListener('resize', updateMenuGeometry)
  window.removeEventListener('scroll', updateMenuGeometry, true)
}

function handleKeydown(event: KeyboardEvent) {
  if (props.disabled) return

  switch (event.key) {
    case 'ArrowDown':
      event.preventDefault()
      moveActive(1)
      break
    case 'ArrowUp':
      event.preventDefault()
      moveActive(-1)
      break
    case 'Home':
      event.preventDefault()
      if (!isOpen.value) openListbox(1)
      activeIndex.value = findEnabledIndex(-1, 1)
      break
    case 'End':
      event.preventDefault()
      if (!isOpen.value) openListbox(-1)
      activeIndex.value = findEnabledIndex(options.value.length, -1)
      break
    case 'Enter':
    case ' ':
      event.preventDefault()
      if (!isOpen.value) openListbox()
      else if (activeIndex.value >= 0) selectOption(activeIndex.value)
      break
    case 'Escape':
      if (isOpen.value) {
        event.preventDefault()
        closeListbox()
      }
      break
    case 'Tab':
      closeListbox()
      break
  }
}

function handleDocumentPointerDown(event: PointerEvent) {
  if (!isOpen.value) return
  const target = event.target
  if (target instanceof Node && rootRef.value?.contains(target)) return
  if (target instanceof Node && menuRef.value?.contains(target)) return
  closeListbox()
}

onMounted(() => {
  document.addEventListener('pointerdown', handleDocumentPointerDown)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', handleDocumentPointerDown)
  removeViewportListeners()
})
</script>

<template>
  <div
    ref="rootRef"
    class="tc-select-wrap"
    :class="rootClass"
    :data-open="isOpen ? 'true' : undefined"
  >
    <button
      v-bind="controlAttrs"
      :id="controlId"
      ref="buttonRef"
      type="button"
      role="combobox"
      aria-haspopup="listbox"
      :aria-expanded="isOpen"
      :aria-controls="listboxId"
      :aria-activedescendant="activeDescendant"
      :aria-required="isRequired || undefined"
      :disabled="disabled"
      class="tc-select"
      :class="size === 'sm' ? 'tc-select-sm' : 'tc-select-md'"
      @click="toggleListbox"
      @keydown="handleKeydown"
    >
      <span
        class="tc-select-label"
        :class="{ 'tc-select-placeholder': displayIsPlaceholder }"
      >
        {{ displayLabel }}
      </span>
      <ChevronDown :size="15" class="tc-select-icon" aria-hidden="true" />
    </button>
    <input
      v-bind="hiddenInputAttrs"
      ref="hiddenInputRef"
      type="hidden"
      :value="modelValueString"
      :disabled="disabled"
    />
    <Teleport to="body">
      <ul
        v-if="isOpen"
        :id="listboxId"
        ref="menuRef"
        class="tc-select-menu"
        role="listbox"
        :aria-labelledby="controlId"
        :style="menuStyle"
      >
        <li
          v-for="(option, index) in options"
          :id="option.id"
          :key="`${option.value}-${index}`"
          :data-option-index="index"
          role="option"
          :aria-selected="option.value === modelValueString"
          :aria-disabled="option.disabled || undefined"
          class="tc-select-option"
          :class="{
            'is-active': index === activeIndex,
            'is-selected': option.value === modelValueString,
            'is-disabled': option.disabled,
          }"
          @mousedown.prevent
          @click="selectOption(index)"
        >
          <span class="tc-select-option-label">{{ option.label }}</span>
          <Check :size="14" class="tc-select-check" aria-hidden="true" />
        </li>
      </ul>
    </Teleport>
  </div>
</template>
