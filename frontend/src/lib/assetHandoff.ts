export const REDACTED_VALUE = '[REDACTED]'

const DEFAULT_CONTEXT_LIMIT = 1400
const SENSITIVE_KEY_PATTERN = 'password|passwd|pwd|token|secret|api[_-]?key|authorization|auth|cookie|session|captcha|mfa|otp|csrf|xsrf|jwt'
const SENSITIVE_FIELD_PATTERN = /password|passwd|pwd|token|secret|api[_-]?key|authorization|auth|cookie|session|captcha|mfa|otp|csrf|xsrf|jwt/i

function isSensitiveQueryKey(key: string) {
  return new RegExp(`(^|[_-])(${SENSITIVE_KEY_PATTERN})([_-]|$)`, 'i').test(key)
}

function redactUrl(value: string) {
  try {
    const url = new URL(value)
    if (url.username) url.username = REDACTED_VALUE
    if (url.password) url.password = REDACTED_VALUE
    url.searchParams.forEach((_value, key) => {
      if (isSensitiveQueryKey(key)) {
        url.searchParams.set(key, REDACTED_VALUE)
      }
    })
    return url.toString()
  } catch (_err) {
    return value
  }
}

export function redactSensitiveText(value: unknown, limit = DEFAULT_CONTEXT_LIMIT) {
  let text = String(value ?? '').trim()
  text = text.replace(/https?:\/\/[^\s,，。)]+/gi, (url) => redactUrl(url))
  text = text.replace(/\b(Bearer|Basic)\s+[A-Za-z0-9._~+/-]+=*/gi, `$1 ${REDACTED_VALUE}`)
  text = text.replace(
    new RegExp(`(["'])(${SENSITIVE_KEY_PATTERN})\\1\\s*:\\s*(["'])(.*?)\\3`, 'gi'),
    (_match, keyQuote, key, valueQuote) => `${keyQuote}${key}${keyQuote}: ${valueQuote}${REDACTED_VALUE}${valueQuote}`,
  )
  text = text.replace(
    new RegExp(`\\b(${SENSITIVE_KEY_PATTERN})\\s*[:=]\\s*[^,\\n;，。)]+`, 'gi'),
    (_match, key) => `${key}=${REDACTED_VALUE}`,
  )
  text = text.replace(
    /\b(fill|type)\s+("[^"]+"|'[^']+'|\S+)\s+("[^"]*"|'[^']*'|\S+)/gi,
    (match, action, target, rawValue) => {
      if (!SENSITIVE_FIELD_PATTERN.test(target)) return match
      const quote = rawValue.startsWith('"') ? '"' : rawValue.startsWith("'") ? "'" : ''
      const redactedValue = quote ? `${quote}${REDACTED_VALUE}${quote}` : REDACTED_VALUE
      return `${action} ${target} ${redactedValue}`
    },
  )
  text = text.replace(/\n{3,}/g, '\n\n')
  if (text.length <= limit) return text
  return `${text.slice(0, limit - 3).trim()}...`
}

export function compactLines(lines: Array<string | false | null | undefined>) {
  return lines.filter(Boolean).join('\n')
}
