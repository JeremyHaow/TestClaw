<script setup lang="ts">
import { AlertTriangle, Send } from 'lucide-vue-next'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Textarea } from '@/components/ui/textarea'

defineProps<{
  question?: string
  modelValue: string
  submitting?: boolean
}>()

const emit = defineEmits<{
  (event: 'update:modelValue', value: string): void
  (event: 'submit'): void
}>()
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
      <Button class="bg-[#2563EB] text-white hover:bg-[#1D4ED8]" :disabled="submitting" @click="emit('submit')">
        <Send :size="16" />
        Submit
      </Button>
    </CardContent>
  </Card>
</template>
