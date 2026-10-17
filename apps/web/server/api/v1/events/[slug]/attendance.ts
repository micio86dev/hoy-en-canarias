// Guest attendance for one event. The browser sends an anonymous guest identifier in a header;
// the API stores only its hash. Only these three methods are exposed, and nothing is cached.
const SAFE_SLUG = /^[a-z0-9-]{1,160}$/
const GUEST_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const METHODS = new Set(['GET', 'POST', 'DELETE'])

export default defineEventHandler(async (event) => {
  const slug = getRouterParam(event, 'slug') ?? ''
  if (!SAFE_SLUG.test(slug)) {
    throw createError({ statusCode: 404, statusMessage: 'Not Found' })
  }
  if (!METHODS.has(event.method)) {
    throw createError({ statusCode: 405, statusMessage: 'Method Not Allowed' })
  }
  const guest = getHeader(event, 'x-guest-id') ?? ''
  if (!GUEST_ID.test(guest)) {
    throw createError({ statusCode: 400, statusMessage: 'Invalid guest identifier' })
  }

  const config = useRuntimeConfig(event)
  const upstream = new URL(`/api/v1/events/${slug}/attendance`, config.apiBase as string)

  let response: Response
  try {
    response = await fetch(upstream, {
      method: event.method,
      headers: { accept: 'application/json', 'x-guest-id': guest },
      signal: AbortSignal.timeout(8_000),
    })
  } catch {
    throw createError({ statusCode: 502, statusMessage: 'Upstream unavailable' })
  }

  setResponseStatus(event, response.status)
  setResponseHeader(event, 'cache-control', 'no-store')
  const text = await response.text()
  try {
    return JSON.parse(text) as unknown
  } catch {
    throw createError({ statusCode: 502, statusMessage: 'Invalid upstream response' })
  }
})
