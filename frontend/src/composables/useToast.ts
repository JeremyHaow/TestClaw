export function useToast() {
  return {
    success: (message: string) => window.__toast?.success(message),
    error: (message: string) => window.__toast?.error(message),
    warning: (message: string) => window.__toast?.warning(message),
    info: (message: string) => window.__toast?.info(message),
  }
}
