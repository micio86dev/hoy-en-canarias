// Forwards a visitor submission to the API. Only this one POST is exposed; the API decides.
export default defineEventHandler(async (event) => {
  const config = useRuntimeConfig(event)
  const body = await readBody(event)
  if (typeof body !== 'object' || body === null || Array.isArray(body)) {
    throw createError({ statusCode: 400, statusMessage: 'Invalid submission' })
  }

  let response: Response
  try {
    response = await fetch(new URL('/api/v1/submissions', config.apiBase as string), {
      method: 'POST',
      headers: { 'content-type': 'application/json', accept: 'application/json' },
      body: JSON.stringify(body),
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
