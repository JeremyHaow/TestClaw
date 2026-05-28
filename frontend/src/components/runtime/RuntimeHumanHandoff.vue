<script setup lang="ts">
import { AlertTriangle, Send } from 'lucide-vue-next'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Textarea } from '@/components/ui/textarea'

defineProps<{
  question?: string
  modelValue: string
  cancelCurrent?: boolean
  canCancelCurrent?: boolean
  submitting?: boolean
}>()

const emit = defineEmits<{
  (event: 'update:modelValue', value: string): void
  (event: 'update:cancelCurrent', value: boolean): void
  (event: 'submit'): void
}>()

function onCancelCurrentChange(event: Event) {
  emit('update:cancelCurrent', Boolean((event.target as HTMLInputElement | null)?.checked))
}
</script>

<template>
  <Card v-if="question" class="border-[#F59E0B]/40 bg-[#FFFBEB] shadow-sm">
    <CardContent class="space-y-3 p-4">
      <Alert class="border-[#F59E0B]/30 bg-[#FFFFFF]">
        <AlertTriangle :size="16" class="text-[#F59E0B]" />
        <AlertTitle class="text-[#92400E]">Human Handoff</AlertTitle>
        <AlertDescription class="text-[#92400E]">{{ question }}</AlertDescription>
      </Alert>
      <Textarea
        :model-value="modelValue"
        class="min-h-24 border-[#E5EAF3] bg-[#FFFFFF]"
        placeholder="补充 token、登录步骤、验证码处理方式或测试环境说明"
        @update:model-value="emit('update:modelValue', String($event))"
      />
      <label
        v-if="canCancelCurrent"
        class="flex items-start gap-2 rounded-md border border-[#F59E0B]/30 bg-[#FFFFFF] px-3 py-2 text-xs leading-5 text-[#92400E]"
      >
        <input
          type="checkbox"
          class="mt-1 h-4 w-4 rounded border-[#E5EAF3] text-[#2563EB]"
          :checked="cancelCurrent"
          @change="onCancelCurrentChange"
        />
        <span>取消当前等待中的运行，并用补充上下文创建 continuation run。</span>
      </label>
      <Button class="bg-[#2563EB] text-white hover:bg-[#1D4ED8]" :disabled="submitting" @click="emit('submit')">
        <Send :size="16" />
        Submit
      </Button>
    </CardContent>
  </Card>
</template>
