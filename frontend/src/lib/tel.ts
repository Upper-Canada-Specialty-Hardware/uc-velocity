import { Telemetry } from './telemetry'

// One process-wide browser telemetry emitter. `source` MUST equal the dashboard
// slug AND match the backend emitter's source ("uc-velocity") — otherwise the two
// halves split into separate apps in the dashboard. Vite inlines VITE_* at BUILD
// time, so a rebuild is required after setting them. Fully INERT until both
// VITE_TELEMETRY_URL and VITE_TELEMETRY_KEY are set, so this is safe to ship before
// the service is switched on. The ingest key is a write-only, intentionally-public
// credential (it ships in the JS bundle) — never reuse it to read.
export const tel = new Telemetry({
  source: 'uc-velocity', // NEVER change once data exists; must match the backend source
  url: import.meta.env.VITE_TELEMETRY_URL,
  key: import.meta.env.VITE_TELEMETRY_KEY,
  appVersion: import.meta.env.VITE_APP_VERSION, // optional
})

// A launch isn't an attempt at anything → lifecycle (kept out of the error rate).
tel.lifecycle('app_start')

// Browser tabs close without a clean exit — nudge the queue out on hide.
document.addEventListener('visibilitychange', () => {
  if (document.hidden) void tel.flush()
})
