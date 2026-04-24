import { ref, onMounted, onUnmounted } from 'vue'

const mobileOpen = ref(false)
const isMobile = ref(false)

function checkMobile() {
  isMobile.value = window.innerWidth < 1024
  if (!isMobile.value) mobileOpen.value = false
}

let initialized = false

export function useSidebar() {
  if (!initialized) {
    initialized = true
    onMounted(() => {
      checkMobile()
      window.addEventListener('resize', checkMobile)
    })
    onUnmounted(() => {
      window.removeEventListener('resize', checkMobile)
    })
  }

  function toggleMobile() {
    mobileOpen.value = !mobileOpen.value
  }

  function closeMobile() {
    mobileOpen.value = false
  }

  return { mobileOpen, isMobile, toggleMobile, closeMobile }
}
