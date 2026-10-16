import tailwindcss from '@tailwindcss/vite'

export default defineNuxtConfig({
  compatibilityDate: '2026-10-01',
  devtools: { enabled: false },
  modules: ['@nuxtjs/i18n', '@nuxt/fonts'],
  css: ['~/assets/css/main.css'],
  vite: {
    plugins: [tailwindcss()],
  },
  fonts: {
    families: [
      { name: 'Fraunces', provider: 'google' },
      { name: 'Inter', provider: 'google' },
    ],
  },
  i18n: {
    defaultLocale: 'es',
    strategy: 'prefix_except_default',
    detectBrowserLanguage: false,
    baseUrl: process.env.NUXT_PUBLIC_SITE_URL ?? 'http://localhost:3000',
    langDir: '../i18n/locales',
    locales: [
      { code: 'es', language: 'es-ES', name: 'Español', file: 'es.json' },
      { code: 'en', language: 'en-GB', name: 'English', file: 'en.json' },
      { code: 'it', language: 'it-IT', name: 'Italiano', file: 'it.json' },
    ],
  },
  runtimeConfig: {
    apiBase: 'http://localhost:8000',
    public: {
      siteUrl: 'http://localhost:3000',
    },
  },
})
