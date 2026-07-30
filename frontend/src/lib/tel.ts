import { Telemetry } from './telemetry'

// One process-wide browser telemetry emitter, tagged source="velocity". Fully
// INERT until both VITE_TELEMETRY_URL and VITE_TELEMETRY_KEY are set at build
// time, so this is safe to ship before the service is switched on. The ingest key
// is a write-only, intentionally-public credential (it ships in the JS bundle) —
// never reuse it to read. Pairs with the backend emitter (same source="velocity")
// which covers API reliability without exposing the key.
export const tel = new Telemetry({
  source: 'velocity', // NEVER change once data exists
  url: import.meta.env.VITE_TELEMETRY_URL,
  key: import.meta.env.VITE_TELEMETRY_KEY,
})
