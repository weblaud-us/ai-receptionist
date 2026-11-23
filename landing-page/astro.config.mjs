// @ts-check
import { defineConfig, envField } from 'astro/config';
import sitemap from '@astrojs/sitemap';
import tailwindcss from '@tailwindcss/vite';
import { loadEnv } from 'vite';

// VITE_* values are read at build time and baked into the static HTML, so build once per
// environment (the Dockerfile takes them as build args). See .env.example.
const fileEnv = loadEnv(process.env.NODE_ENV ?? 'production', process.cwd(), 'VITE_');
const SITE_URL = process.env.VITE_SITE_URL || fileEnv.VITE_SITE_URL || 'https://www.example.com';

const link = () => envField.string({ context: 'client', access: 'public', optional: true, default: '' });

export default defineConfig({
  site: SITE_URL,
  trailingSlash: 'ignore',
  integrations: [sitemap({ filter: (page) => !page.includes('/404') })],
  build: {
    // Keep every stylesheet and script an external file: the Nginx CSP allows 'self' only.
    inlineStylesheets: 'never',
  },
  env: {
    schema: {
      VITE_SITE_URL: envField.string({ context: 'client', access: 'public', optional: true, default: 'https://www.example.com' }),
      VITE_DASHBOARD_SIGNUP_URL: envField.string({ context: 'client', access: 'public', optional: true, default: 'https://app.example.com/signup' }),
      VITE_DASHBOARD_LOGIN_URL: envField.string({ context: 'client', access: 'public', optional: true, default: 'https://app.example.com/login' }),
      VITE_DEMO_CALL_URL: link(),
      VITE_BOOK_DEMO_URL: link(),
      VITE_DEMO_PHONE_AU: link(),
      VITE_DEMO_PHONE_UK: link(),
      VITE_PRIVACY_URL: link(),
      VITE_TERMS_URL: link(),
      VITE_DPA_URL: link(),
      VITE_CLAIM_ZDR: envField.boolean({ context: 'client', access: 'public', optional: true, default: false }),
      // Live "Talk to it now" demo: the API origin, the Google sign-in client id, and the switch.
      VITE_API_URL: link(),
      VITE_GOOGLE_CLIENT_ID: link(),
      VITE_WEB_DEMO: envField.boolean({ context: 'client', access: 'public', optional: true, default: false }),
    },
  },
  vite: {
    plugins: [tailwindcss()],
    // Bind mounts from a Windows or macOS host (docker compose) don't deliver file-change events.
    server: process.env.DEV_WATCH_POLLING === 'true' ? { watch: { usePolling: true, interval: 300 } } : {},
    build: { assetsInlineLimit: 0 },
  },
});
