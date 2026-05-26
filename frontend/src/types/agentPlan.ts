import type { Component } from 'vue'

export type PlanMessage = {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  plan?: PlannerMessagePlan | null
  created_at?: string | null
}

export type PlannerQuestionChoice = {
  label: string
  message: string
  title?: string | null
  description?: string | null
  field?: string | null
  value?: string | null
  step?: string | null
  allows_defer?: boolean
  allows_skip?: boolean
  optional?: boolean
}

export type PlannerQuestionOption = {
  question: string
  step?: string | null
  required?: boolean
  options: PlannerQuestionChoice[]
}

export type PlannerMessagePlan = {
  status?: string
  questions?: string[]
  question_options?: PlannerQuestionOption[]
  ready_to_execute?: boolean
  plan?: Record<string, any> | null
  run_payload?: Record<string, any> | null
}

export type PlanningSession = {
  id: string
  title: string
  status: string
  ready_to_execute: boolean
  current_plan?: Record<string, any> | null
  current_run_payload?: Record<string, any> | null
  rejection_reason?: string | null
  executed_run_id?: string | null
  created_at?: string | null
  updated_at?: string | null
  messages?: PlanMessage[]
  question_options?: PlannerQuestionOption[]
}

export type PlannerProcessEvent = {
  code: string
  label: string
  status?: string
}

export type IntakeStepId =
  | 'target_kind'
  | 'coverage_scope'
  | 'auth_boundary'
  | 'safety_boundary'
  | 'success_criteria'

export type IntakeStep = {
  id: IntakeStepId
  label: string
  icon: Component
}

export type PlanDraftItem = {
  id: IntakeStepId
  label: string
  status: string
  value: unknown
}
