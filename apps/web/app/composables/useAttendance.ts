import type { AttendanceState } from '~/types/api'

const GUEST_KEY = 'hoy:guest:v1'
const GUEST_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
let memoryGuest: string | null = null

/**
 * An anonymous random identifier for this browser. It is not an account and not personal data.
 * When localStorage is blocked, it falls back to one identifier per page load.
 */
export function guestId(): string {
  try {
    const stored = window.localStorage.getItem(GUEST_KEY)
    if (stored && GUEST_ID.test(stored)) return stored
    const created = crypto.randomUUID()
    window.localStorage.setItem(GUEST_KEY, created)
    return created
  } catch {
    memoryGuest ??= crypto.randomUUID()
    return memoryGuest
  }
}

export function useAttendance(slug: string) {
  const url = `/api/v1/events/${slug}/attendance`
  const state = ref<AttendanceState | null>(null)
  const pending = ref(false)
  const failed = ref(false)

  async function request(method: 'GET' | 'POST' | 'DELETE'): Promise<void> {
    failed.value = false
    try {
      state.value = await $fetch<AttendanceState>(url, {
        method,
        headers: { 'X-Guest-Id': guestId() },
      })
    } catch {
      failed.value = true
    }
  }

  async function load(): Promise<void> {
    await request('GET')
  }

  async function toggle(): Promise<void> {
    if (pending.value) return
    pending.value = true
    try {
      await request(state.value?.attending ? 'DELETE' : 'POST')
    } finally {
      pending.value = false
    }
  }

  return { state, pending, failed, load, toggle }
}
