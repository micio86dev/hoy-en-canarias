// Read-only proxy: the browser talks to the Nuxt origin, and only the API origin in
// runtime config is ever contacted, so the API never has to be exposed to visitors.
const SAFE_PATH = /^[a-z0-9/_-]{1,120}$/

export default defineEventHandler(async (event) => {
  const path = getRouterParam(event, 'path') ?? ''
  if (!SAFE_PATH.test(path)) {
    throw createError({ statusCode: 404, statusMessage: 'Not Found' })
  }

  const config = useRuntimeConfig(event)
  const upstream = new URL(`/api/v1/${path}`, config.apiBase)
  for (const [key, value] of Object.entries(getQuery(event))) {
    for (const item of Array.isArray(value) ? value : [value]) {
      if (item !== undefined && item !== null) {
        upstream.searchParams.append(key, String(item))
      }
    }
  }

  let response: Response
  try {
    response = await fetch(upstream, {
      headers: { accept: 'application/json' },
      signal: AbortSignal.timeout(8_000),
    })
  } catch {
    throw createError({ statusCode: 502, statusMessage: 'Upstream unavailable' })
  }

  setResponseStatus(event, response.status)
  if (response.ok) {
    setResponseHeader(event, 'cache-control', 'public, max-age=60, stale-while-revalidate=300')
  } else {
    setResponseHeader(event, 'cache-control', 'no-store')
  }
  const body = await response.text()
  try {
    return JSON.parse(body) as unknown
  } catch {
    throw createError({ statusCode: 502, statusMessage: 'Invalid upstream response' })
  }
})
