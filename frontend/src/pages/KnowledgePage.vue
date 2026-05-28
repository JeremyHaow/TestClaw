<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import api from '../lib/api'
import { useToast } from '../composables/useToast'
import { Ban, BookOpen, Check, Edit3, Eye, FileText, Plus, RefreshCw, Search, Trash2, X } from 'lucide-vue-next'
import EmptyState from '../components/EmptyState.vue'
import LoadingSpinner from '../components/LoadingSpinner.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'

interface KnowledgeEntry {
  id: string
  content: string
  source_script_id?: string | null
  created_at?: string | null
  updated_at?: string | null
  last_updated?: string | null
  embedding_available?: boolean
  usage_count?: number | null
  used_count?: number | null
  chunk_count?: number | null
  fragment_count?: number | null
  chunks_count?: number | null
  fragments_count?: number | null
  chunks?: unknown[] | null
  fragments?: unknown[] | null
  is_active?: boolean | null
  disabled?: boolean | null
  type?: string | null
  knowledge_type?: string | null
  title?: string | null
  name?: string | null
}

type MemoryCandidate = {
  kind?: string | null
  target_hint?: string | null
  summary?: string | null
  planner_hint?: string | null
  reason?: string | null
  failure_type?: string | null
  final_verdict?: string | null
  facts?: Array<{ fact_type?: string | null; summary?: string | null; planner_hint?: string | null }>
}

const toast = useToast()
const items = ref<KnowledgeEntry[]>([])
const loading = ref(false)
const searchQuery = ref('')
const searchResults = ref<KnowledgeEntry[]>([])
const searching = ref(false)
const newContent = ref('')
const adding = ref(false)
const deleteTarget = ref<KnowledgeEntry | null>(null)
const selectedEntry = ref<KnowledgeEntry | null>(null)
const editing = ref(false)
const editContent = ref('')
const reindexingId = ref<string | null>(null)

const displayedItems = computed(() => {
  if (searchQuery.value.trim()) return searchResults.value
  return items.value
})

let searchTimer: ReturnType<typeof setTimeout> | null = null

function debouncedSearch() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(doSearch, 300)
}

onUnmounted(() => {
  if (searchTimer) clearTimeout(searchTimer)
})

async function fetchItems() {
  loading.value = true
  try {
    const { data } = await api.get('/knowledge')
    const list = Array.isArray(data) ? data : []
    items.value = list
    if (!selectedEntry.value && list.length) selectedEntry.value = list[0]
    if (selectedEntry.value) {
      selectedEntry.value = list.find((item: KnowledgeEntry) => item.id === selectedEntry.value?.id) || selectedEntry.value
    }
  } catch {
    toast.error('加载知识库失败')
  } finally {
    loading.value = false
  }
}

async function doSearch() {
  const query = searchQuery.value.trim()
  if (!query) {
    searchResults.value = []
    return
  }
  searching.value = true
  try {
    const { data } = await api.get('/knowledge/search', { params: { q: query } })
    searchResults.value = Array.isArray(data) ? data : []
  } catch {
    toast.error('搜索失败')
  } finally {
    searching.value = false
  }
}

function clearSearch() {
  searchQuery.value = ''
  searchResults.value = []
}

function selectEntry(entry: KnowledgeEntry) {
  selectedEntry.value = entry
  editing.value = false
  editContent.value = ''
}

async function addEntry() {
  const content = newContent.value.trim()
  if (!content) return
  adding.value = true
  try {
    const { data } = await api.post('/knowledge', { content })
    newContent.value = ''
    selectedEntry.value = data
    toast.success(data.embedding_available ? '知识条目已添加并生成向量' : '知识条目已添加，暂未生成向量')
    await fetchItems()
  } catch {
    toast.error('添加失败')
  } finally {
    adding.value = false
  }
}

function startEdit() {
  if (!selectedEntry.value) return
  editContent.value = selectedEntry.value.content || ''
  editing.value = true
}

async function saveEdit() {
  if (!selectedEntry.value) return
  const content = editContent.value.trim()
  if (!content) {
    toast.warning('知识内容不能为空')
    return
  }
  try {
    const { data } = await api.put(`/knowledge/${selectedEntry.value.id}`, { content })
    selectedEntry.value = data
    editing.value = false
    toast.success(data.embedding_available ? '知识条目已更新并重新生成向量' : '知识条目已更新，向量暂不可用')
    await fetchItems()
    if (searchQuery.value.trim()) await doSearch()
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '更新失败')
  }
}

async function doDelete() {
  if (!deleteTarget.value) return
  const target = deleteTarget.value
  try {
    await api.delete(`/knowledge/${target.id}`)
    toast.success('知识条目已删除')
    deleteTarget.value = null
    if (selectedEntry.value?.id === target.id) selectedEntry.value = null
    await fetchItems()
    if (searchQuery.value.trim()) await doSearch()
  } catch {
    toast.error('删除失败')
  }
}

function embeddingLabel(entry: KnowledgeEntry | null) {
  return entry?.embedding_available ? '向量已生成' : '无向量'
}

function sourceLabel(entry: KnowledgeEntry | null) {
  return entry?.source_script_id ? `run ${String(entry.source_script_id).slice(0, 8)}` : '手动知识'
}

function firstNumber(...values: unknown[]) {
  for (const value of values) {
    const numberValue = Number(value)
    if (Number.isFinite(numberValue) && numberValue >= 0) return numberValue
  }
  return null
}

function entryTitle(entry: KnowledgeEntry) {
  const direct = String(entry.title || entry.name || '').trim()
  if (direct) return direct
  const candidate = memoryCandidate(entry)
  if (candidate) {
    const kind = String(candidate.kind || '运行记忆')
    const target = String(candidate.target_hint || '').trim()
    return target ? `${kind} · ${target}` : kind
  }
  const firstLine = String(entry.content || '')
    .trim()
    .split(/\n+/)
    .map((line) => line.trim())
    .find(Boolean)
  if (!firstLine) return '未命名知识'
  return firstLine.length > 32 ? `${firstLine.slice(0, 32)}...` : firstLine
}

function memoryCandidate(entry: KnowledgeEntry | null): MemoryCandidate | null {
  const content = String(entry?.content || '')
  if (!content.includes('TESTCLAW_MEMORY_CANDIDATE_V1')) return null
  const start = content.indexOf('{')
  const end = content.lastIndexOf('}')
  if (start < 0 || end <= start) return null
  try {
    const parsed = JSON.parse(content.slice(start, end + 1))
    return parsed && typeof parsed === 'object' ? parsed as MemoryCandidate : null
  } catch {
    return null
  }
}

function compactText(value: unknown, limit = 220) {
  const text = String(value || '').replace(/\s+/g, ' ').trim()
  if (text.length <= limit) return text
  return `${text.slice(0, limit).trim()}...`
}

function knowledgeSummary(entry: KnowledgeEntry | null) {
  const candidate = memoryCandidate(entry)
  if (candidate) {
    return compactText(candidate.summary || candidate.planner_hint || candidate.reason || entry?.content, 260)
  }
  return compactText(entry?.content, 260)
}

function candidateFactSummaries(entry: KnowledgeEntry | null) {
  const facts = memoryCandidate(entry)?.facts
  return Array.isArray(facts)
    ? facts.map((fact) => compactText(fact.summary || fact.planner_hint, 180)).filter(Boolean).slice(0, 4)
    : []
}

function knowledgeType(entry: KnowledgeEntry) {
  const candidate = memoryCandidate(entry)
  if (candidate?.kind === 'successful_strategy') return '成功策略'
  if (candidate?.kind === 'failure_recovery') return '失败恢复'
  const explicitType = String(entry.knowledge_type || entry.type || '').trim()
  if (explicitType) return explicitType
  const content = String(entry.content || '').toLowerCase()
  if (/规范|标准|准则|policy|guideline|rule/.test(content)) return '测试规范'
  if (/缺陷|故障|失败|根因|bug|error|exception|incident/.test(content)) return '历史缺陷'
  if (/接口|端点|api|openapi|swagger|endpoint/.test(content)) return '接口说明'
  if (/业务|流程|权限|角色/.test(content)) return '业务规则'
  return entry.source_script_id ? '运行沉淀' : '手动知识'
}

function fragmentCount(entry: KnowledgeEntry) {
  const explicitCount = firstNumber(
    entry.fragment_count,
    entry.chunk_count,
    entry.fragments_count,
    entry.chunks_count,
  )
  if (explicitCount !== null) return explicitCount
  if (Array.isArray(entry.fragments)) return entry.fragments.length
  if (Array.isArray(entry.chunks)) return entry.chunks.length
  const content = String(entry.content || '').trim()
  if (!content) return 0
  const paragraphCount = content.split(/\n{2,}/).map((part) => part.trim()).filter(Boolean).length
  return Math.max(1, paragraphCount, Math.ceil(content.length / 500))
}

function usageCount(entry: KnowledgeEntry) {
  return firstNumber(entry.usage_count, entry.used_count) ?? 0
}

function lastUpdatedLabel(entry: KnowledgeEntry) {
  return entry.updated_at || entry.last_updated || entry.created_at || '未知'
}

function retrievalStatusLabel(entry: KnowledgeEntry) {
  if (entry.disabled || entry.is_active === false) return '已禁用'
  return entry.embedding_available ? '可检索' : '待索引'
}

function retrievalStatusClass(entry: KnowledgeEntry) {
  if (entry.disabled || entry.is_active === false) return 'border-gray-200 bg-gray-50 text-gray-500'
  return entry.embedding_available
    ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
    : 'border-amber-200 bg-amber-50 text-amber-700'
}

async function reindexEntry(entry: KnowledgeEntry) {
  const content = String(entry.content || '').trim()
  if (!content) {
    toast.warning('知识内容不能为空，无法重新索引')
    return
  }
  reindexingId.value = entry.id
  try {
    const { data } = await api.put(`/knowledge/${entry.id}`, { content })
    const updated = data as KnowledgeEntry
    selectedEntry.value = updated
    toast.success(updated.embedding_available ? '知识条目已重新索引' : '重新索引已请求，embedding provider 暂不可用')
    await fetchItems()
    if (searchQuery.value.trim()) await doSearch()
  } catch (err: any) {
    toast.error(err?.response?.data?.detail || '重新索引失败')
  } finally {
    reindexingId.value = null
  }
}

function showDisableUnavailable() {
  toast.warning('当前知识 API 不支持禁用，未执行任何变更')
}

onMounted(fetchItems)
</script>

<template>
  <div class="mx-auto max-w-7xl space-y-5 pb-10">
    <div class="flex flex-col gap-3 border-b border-gray-200/80 pb-5 lg:flex-row lg:items-end lg:justify-between">
      <div class="min-w-0">
        <div class="tc-page-kicker">Memory</div>
        <h2 class="text-xl font-semibold tracking-tight text-gray-950">RAG 知识库</h2>
        <p class="mt-1 max-w-3xl text-sm text-gray-500">管理测试经验、缺陷根因和修复建议；每条知识按真实 embedding 状态显示，不把降级检索伪装成向量命中。</p>
      </div>
      <span class="w-fit rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-bold text-gray-600">
        {{ items.length }} 条知识
      </span>
    </div>

    <div class="grid grid-cols-1 gap-4 lg:grid-cols-[340px_minmax(0,1fr)] lg:items-start">
      <aside class="space-y-4 lg:sticky lg:top-4 lg:max-h-[calc(100vh-9rem)] lg:overflow-y-auto lg:pr-1">
        <section class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <h3 class="mb-3 text-xs font-bold uppercase tracking-widest text-gray-400">添加知识</h3>
          <textarea v-model="newContent" rows="5" placeholder="输入知识内容..."
            class="w-full resize-none rounded-lg border border-gray-200 bg-gray-50 px-4 py-2.5 text-sm outline-none transition-all focus:border-blue-500 focus:bg-white" />
          <button @click="addEntry" :disabled="adding || !newContent.trim()"
            class="mt-3 flex w-full items-center justify-center gap-2 rounded-lg bg-gray-950 py-2.5 text-sm font-bold text-white transition-colors hover:bg-gray-800 disabled:opacity-50">
            <Plus :size="16" />
            {{ adding ? '添加中...' : '添加条目' }}
          </button>
        </section>

        <section class="rounded-lg border border-gray-200 bg-white shadow-sm">
          <div class="border-b border-gray-100 p-4">
            <h3 class="mb-3 text-xs font-bold uppercase tracking-widest text-gray-400">搜索 / 列表</h3>
            <div class="relative">
              <Search :size="16" class="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
              <input v-model="searchQuery" @input="debouncedSearch" placeholder="搜索知识内容..."
                class="w-full rounded-lg border border-gray-200 bg-gray-50 py-2.5 pl-9 pr-8 text-sm outline-none transition-all focus:border-blue-500 focus:bg-white" />
              <button v-if="searchQuery" @click="clearSearch" class="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-gray-400 hover:bg-gray-200">
                <X :size="14" />
              </button>
            </div>
            <div class="mt-2 text-[11px] text-gray-400">
              {{ searchQuery ? `搜索结果 ${searchResults.length} 条` : `全部条目 ${items.length} 条` }}
            </div>
          </div>

          <LoadingSpinner v-if="loading || searching" text="加载中..." />
          <EmptyState v-else-if="!displayedItems.length" :icon="BookOpen" title="暂无知识条目" description="添加知识条目来构建你的测试知识库。" />
          <div v-else class="max-h-96 min-h-[260px] space-y-3 overflow-y-auto p-3 lg:max-h-[calc(100vh-28rem)]">
            <article
              v-for="entry in displayedItems"
              :key="entry.id"
              data-testid="knowledge-card"
              class="rounded-lg border p-4 transition-all"
              :class="selectedEntry?.id === entry.id ? 'border-blue-300 bg-blue-50/50 shadow-[0_12px_30px_rgba(37,99,235,0.10)]' : 'border-gray-200 bg-white shadow-sm hover:border-blue-200 hover:shadow-[0_12px_30px_rgba(15,23,42,0.08)]'"
            >
              <div class="flex items-start gap-3">
                <div class="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-600">
                  <FileText :size="18" />
                </div>
                <div class="min-w-0 flex-1">
                  <div class="flex flex-wrap items-center gap-2">
                    <h3 class="min-w-0 flex-1 truncate text-sm font-semibold text-gray-950">{{ entryTitle(entry) }}</h3>
                    <span class="rounded-full border px-2 py-0.5 text-[10px] font-bold" :class="retrievalStatusClass(entry)">
                      {{ retrievalStatusLabel(entry) }}
                    </span>
                  </div>
                  <p class="mt-2 line-clamp-3 text-xs leading-5 text-gray-500">{{ knowledgeSummary(entry) }}</p>
                </div>
              </div>

              <div class="mt-4 grid grid-cols-2 gap-x-4 gap-y-2 border-t border-gray-100 pt-3 text-[11px]">
                <div>
                  <div class="font-bold uppercase tracking-widest text-gray-400">类型</div>
                  <div class="mt-0.5 truncate font-semibold text-gray-800">{{ knowledgeType(entry) }}</div>
                </div>
                <div>
                  <div class="font-bold uppercase tracking-widest text-gray-400">片段</div>
                  <div class="mt-0.5 font-semibold text-gray-800">{{ fragmentCount(entry) }}</div>
                </div>
                <div>
                  <div class="font-bold uppercase tracking-widest text-gray-400">最近更新</div>
                  <div class="mt-0.5 truncate font-mono text-gray-500">{{ lastUpdatedLabel(entry) }}</div>
                </div>
                <div>
                  <div class="font-bold uppercase tracking-widest text-gray-400">使用次数</div>
                  <div class="mt-0.5 font-semibold text-gray-800">{{ usageCount(entry) }}</div>
                </div>
              </div>

              <div class="mt-4 flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  class="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-bold text-gray-700 transition-all hover:border-gray-300 hover:bg-gray-50"
                  @click="selectEntry(entry)"
                >
                  <Eye :size="13" /> 查看
                </button>
                <button
                  type="button"
                  :disabled="reindexingId === entry.id"
                  class="inline-flex items-center gap-1.5 rounded-lg border border-blue-200 bg-blue-50 px-3 py-1.5 text-xs font-bold text-blue-700 transition-all hover:bg-blue-100 disabled:opacity-50"
                  @click="reindexEntry(entry)"
                >
                  <RefreshCw :size="13" /> {{ reindexingId === entry.id ? '索引中' : '重新索引' }}
                </button>
                <button
                  type="button"
                  title="当前知识 API 未提供禁用能力"
                  class="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-gray-50 px-3 py-1.5 text-xs font-bold text-gray-500 transition-all hover:bg-gray-100"
                  @click="showDisableUnavailable"
                >
                  <Ban :size="13" /> 禁用
                </button>
              </div>
            </article>
          </div>
        </section>
      </aside>

      <section class="min-w-0 overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm lg:flex lg:max-h-[calc(100vh-9rem)] lg:flex-col">
        <div class="flex flex-wrap items-start justify-between gap-3 border-b border-gray-100 px-5 py-4">
          <div>
            <h3 class="text-sm font-bold text-gray-900">知识详情</h3>
            <p v-if="selectedEntry" class="mt-1 text-xs text-gray-500">
              {{ sourceLabel(selectedEntry) }} / {{ embeddingLabel(selectedEntry) }} / {{ retrievalStatusLabel(selectedEntry) }}
            </p>
          </div>
          <div v-if="selectedEntry" class="flex flex-wrap items-center gap-2">
            <button
              @click="reindexEntry(selectedEntry)"
              :disabled="reindexingId === selectedEntry.id"
              class="inline-flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs font-bold text-blue-700 transition-all hover:bg-blue-100 disabled:opacity-50"
            >
              <RefreshCw :size="14" /> 重新索引
            </button>
            <button
              @click="showDisableUnavailable"
              title="当前知识 API 未提供禁用能力"
              class="inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs font-bold text-gray-500 transition-all hover:bg-gray-100"
            >
              <Ban :size="14" /> 禁用
            </button>
            <button v-if="!editing" @click="startEdit"
              class="inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-bold text-gray-600 transition-all hover:bg-gray-50 hover:text-gray-950">
              <Edit3 :size="14" /> 编辑
            </button>
            <button @click="deleteTarget = selectedEntry"
              class="rounded-lg p-2 text-gray-400 transition-all hover:bg-red-50 hover:text-red-500" title="删除">
              <Trash2 :size="16" />
            </button>
          </div>
        </div>

        <div v-if="!selectedEntry" class="p-6">
          <EmptyState :icon="BookOpen" title="选择一条知识" description="从左侧列表选择条目后查看详情、编辑内容和确认向量状态。" />
        </div>

        <div v-else class="min-h-0 p-5 lg:flex-1 lg:overflow-y-auto">
          <div class="mb-4 grid gap-3 sm:grid-cols-4">
            <div class="rounded-lg border border-gray-200 bg-gray-50 p-3">
              <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">类型</div>
              <div class="mt-1 truncate text-sm font-semibold text-gray-900">{{ knowledgeType(selectedEntry) }}</div>
            </div>
            <div class="rounded-lg border border-gray-200 bg-gray-50 p-3">
              <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">片段</div>
              <div class="mt-1 text-sm font-semibold text-gray-900">{{ fragmentCount(selectedEntry) }}</div>
            </div>
            <div class="rounded-lg border border-gray-200 bg-gray-50 p-3">
              <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">最近更新</div>
              <div class="mt-1 truncate text-xs font-mono text-gray-500">{{ lastUpdatedLabel(selectedEntry) }}</div>
            </div>
            <div class="rounded-lg border border-gray-200 bg-gray-50 p-3">
              <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">使用次数</div>
              <div class="mt-1 text-sm font-semibold text-gray-900">{{ usageCount(selectedEntry) }}</div>
            </div>
          </div>

          <template v-if="editing">
            <textarea v-model="editContent" rows="18"
              class="max-h-[calc(100vh-420px)] min-h-[320px] w-full resize-none rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-sm leading-6 outline-none transition-all focus:border-blue-500 focus:bg-white" />
            <div class="mt-4 flex justify-end gap-2">
              <button @click="editing = false" class="rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-bold text-gray-600 transition-all hover:bg-gray-50">取消</button>
              <button @click="saveEdit" class="inline-flex items-center gap-2 rounded-lg bg-gray-950 px-4 py-2 text-sm font-bold text-white transition-all hover:bg-gray-800">
                <Check :size="15" /> 保存
              </button>
            </div>
          </template>

          <div v-else class="max-h-[420px] overflow-y-auto rounded-lg border border-gray-200 bg-gray-50 p-4 lg:max-h-[calc(100vh-22rem)]">
            <div v-if="memoryCandidate(selectedEntry)" class="space-y-4">
              <div>
                <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">摘要</div>
                <p class="mt-1 text-sm leading-6 text-gray-700">{{ knowledgeSummary(selectedEntry) }}</p>
              </div>
              <div v-if="memoryCandidate(selectedEntry)?.planner_hint">
                <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">Planner Hint</div>
                <p class="mt-1 text-sm leading-6 text-gray-700">{{ compactText(memoryCandidate(selectedEntry)?.planner_hint, 360) }}</p>
              </div>
              <div class="grid gap-3 sm:grid-cols-3">
                <div class="rounded-lg border border-gray-200 bg-white p-3">
                  <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">Target</div>
                  <div class="mt-1 truncate text-xs font-semibold text-gray-700">{{ memoryCandidate(selectedEntry)?.target_hint || '未记录' }}</div>
                </div>
                <div class="rounded-lg border border-gray-200 bg-white p-3">
                  <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">Verdict</div>
                  <div class="mt-1 truncate text-xs font-semibold text-gray-700">{{ memoryCandidate(selectedEntry)?.final_verdict || '未记录' }}</div>
                </div>
                <div class="rounded-lg border border-gray-200 bg-white p-3">
                  <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">Failure</div>
                  <div class="mt-1 truncate text-xs font-semibold text-gray-700">{{ memoryCandidate(selectedEntry)?.failure_type || '无' }}</div>
                </div>
              </div>
              <div v-if="candidateFactSummaries(selectedEntry).length">
                <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">Facts</div>
                <ul class="mt-2 space-y-2">
                  <li v-for="fact in candidateFactSummaries(selectedEntry)" :key="fact" class="rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm leading-6 text-gray-700">
                    {{ fact }}
                  </li>
                </ul>
              </div>
              <p class="text-xs text-gray-400">编辑时可查看和修改完整原始内容。</p>
            </div>
            <p v-else class="whitespace-pre-wrap break-words text-sm leading-6 text-gray-700">{{ selectedEntry.content }}</p>
          </div>
        </div>
      </section>
    </div>

    <ConfirmDialog
      :show="!!deleteTarget"
      title="删除知识条目"
      :message="`确定要删除这条知识条目吗？`"
      confirm-text="删除"
      cancel-text="取消"
      :danger="true"
      @confirm="doDelete"
      @cancel="deleteTarget = null"
    />
  </div>
</template>
