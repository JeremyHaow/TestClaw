const HAS_TIMEZONE_SUFFIX = /(Z|[+-]\d{2}:?\d{2})$/i

export function parseServerDateTime(value: string | null | undefined): Date | null {
  if (!value) return null
  const trimmed = value.trim()
  if (!trimmed) return null
  const normalized = HAS_TIMEZONE_SUFFIX.test(trimmed) ? trimmed : `${trimmed}Z`
  const date = new Date(normalized)
  return Number.isNaN(date.getTime()) ? null : date
}

export function serverDateTimeMs(value: string | null | undefined): number | null {
  return parseServerDateTime(value)?.getTime() ?? null
}

export function formatServerDateTime(value: string | null | undefined, locale = 'zh-CN') {
  return parseServerDateTime(value)?.toLocaleString(locale) || ''
}
