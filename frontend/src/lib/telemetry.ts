/**
 * Drop-in telemetry client for a JS/TS app. Copy this ONE file into your project.
 *
 *   import { Telemetry } from './telemetry'
 *
 *   const tel = new Telemetry({ source: 'gone-fishing', appVersion: '1.4.2' })
 *   tel.action('trip_logged', { species: 'pike' })
 *   tel.error('backend_call', { endpoint: '/api/trips/42', status_code: 500 })
 *
 * Runs anywhere `fetch` exists: Node 18+, Electron's main process, a browser, a
 * serverless handler. No dependencies, no build-step requirements.
 *
 * THE ONE RULE THIS FILE ENFORCES: telemetry must never break, slow, or crash the app
 * it is measuring. Every method returns immediately, every failure is swallowed, and a
 * client with no url/key is a total no-op — no id, no timer, no network.
 *
 * BROWSER APPS: the service must name your exact origin in its ALLOWED_ORIGINS, or the
 * browser blocks the POST before it is sent (the Authorization header forces a
 * preflight). Server-side callers are unaffected. And note the key ships inside your
 * JS bundle — that is expected (it is a write-only credential) but it does mean anyone
 * can read it, so never reuse it as a read credential.
 */

export interface TelemetryOptions {
  /** Stable slug for THIS app, e.g. 'gone-fishing'. Never change it once data exists. */
  source: string
  appVersion?: string
  /** Defaults to process.env.TELEMETRY_URL. Missing url or key ⇒ dormant. */
  url?: string
  key?: string
  os?: string
  /**
   * Anonymous per-machine/per-browser id. Defaults to a UUID persisted in
   * localStorage. A SERVER app should pass its own persisted id (or a hostname):
   * without one, every process restart looks like a brand-new install.
   */
  installId?: string
}

interface EventFields {
  endpoint?: string
  http_method?: string
  status_code?: number
  error_class?: string
  error_message?: string
  duration_ms?: number
}

const BATCH_SIZE = 20        // events per POST
const FLUSH_MS = 30_000      // how long an event may wait before being sent
const MAX_QUEUE = 500        // hard cap; past this the OLDEST events are dropped
const TIMEOUT_MS = 10_000
const MAX_MESSAGE_LEN = 500  // error messages are truncated, not stored whole

const uuid = (): string =>
  globalThis.crypto?.randomUUID?.() ??
  `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`

/** Strip anything that looks like a home directory, so a username never leaves the
 *  machine inside a stack trace or file path. */
const scrub = (text: string): string =>
  text
    .replace(/[A-Za-z]:\\Users\\[^\\/:*?"<>|\r\n]+/g, '~')
    .replace(/\/(?:home|Users)\/[^/\s]+/g, '~')

export class Telemetry {
  readonly enabled: boolean
  private readonly url: string
  private readonly key: string
  private readonly source: string
  private readonly appVersion?: string
  private readonly os?: string
  private readonly installId: string
  private readonly sessionId: string
  private queue: Record<string, unknown>[] = []
  private timer: ReturnType<typeof setTimeout> | null = null
  private userId: string | null = null
  private userName: string | null = null

  constructor(opts: TelemetryOptions) {
    const env = (globalThis as { process?: { env?: Record<string, string | undefined> } })
      .process?.env
    this.url = (opts.url ?? env?.TELEMETRY_URL ?? '').trim().replace(/\/$/, '')
    this.key = (opts.key ?? env?.TELEMETRY_KEY ?? '').trim()
    this.source = opts.source
    this.appVersion = opts.appVersion
    this.os = opts.os ?? env?.npm_config_platform ?? undefined
    this.enabled = Boolean(this.url && this.key)
    // Dormant clients create no id at all — nothing is stored, nothing is generated.
    this.installId = this.enabled ? (opts.installId ?? this.loadInstallId()) : ''
    this.sessionId = this.enabled ? uuid() : ''
  }

  /** A UUID kept in localStorage where one exists. In Node with no installId passed,
   *  this falls back to a per-process id — see the option's note. */
  private loadInstallId(): string {
    try {
      const store = (globalThis as { localStorage?: Storage }).localStorage
      if (!store) return uuid()
      const k = `${this.source}.install_id`
      const existing = store.getItem(k)
      if (existing) return existing
      const fresh = uuid()
      store.setItem(k, fresh)
      return fresh
    } catch {
      return uuid()
    }
  }

  /** Attach a signed-in person to everything sent from here on. Use YOUR app's
   *  internal user id — never an email, and never a token. */
  identify(userId: string | number | null, userName?: string | null): void {
    this.userId = userId == null ? null : String(userId)
    this.userName = userName ?? null
  }

  /** Something the user did, or a call the app made. This is the denominator of the
   *  error rate, so record ATTEMPTS — not successes. */
  action(name: string, props?: Record<string, unknown>, fields?: EventFields): void {
    this.enqueue('action', name, props, fields)
  }

  /** Something that failed. Pass endpoint/status_code/error_class/error_message. */
  error(name: string, fields?: EventFields, props?: Record<string, unknown>): void {
    this.enqueue('error', name, props, fields)
  }

  /** app_start / app_exit. Excluded from the error-rate denominator: a launch is not
   *  an attempt at anything. */
  lifecycle(name: string, props?: Record<string, unknown>): void {
    this.enqueue('lifecycle', name, props)
  }

  private enqueue(
    event_type: string, event_name: string,
    props?: Record<string, unknown>, fields?: EventFields,
  ): void {
    if (!this.enabled) return
    try {
      const msg = fields?.error_message
      this.queue.push({
        event_id: uuid(),          // idempotency key; the server dedups on it
        event_type,
        event_name,
        client_ts: new Date().toISOString(),
        props: props ?? null,
        endpoint: fields?.endpoint ?? null,
        http_method: fields?.http_method ?? null,
        status_code: fields?.status_code ?? null,
        error_class: fields?.error_class ?? null,
        error_message: msg ? scrub(String(msg)).slice(0, MAX_MESSAGE_LEN) : null,
        duration_ms: fields?.duration_ms ?? null,
      })
      // Drop the OLDEST on overflow: during an outage the newest events describe it.
      if (this.queue.length > MAX_QUEUE) this.queue = this.queue.slice(-MAX_QUEUE)
      if (this.queue.length >= BATCH_SIZE) void this.flush()
      else this.schedule()
    } catch {
      /* never let measurement break the thing being measured */
    }
  }

  private schedule(): void {
    if (this.timer) return
    this.timer = setTimeout(() => void this.flush(), FLUSH_MS)
    // Node only: a pending flush must never hold the process open.
    ;(this.timer as { unref?: () => void }).unref?.()
  }

  /** Send whatever is queued. Never rejects. Await it before exiting. */
  async flush(): Promise<void> {
    if (!this.enabled) return
    if (this.timer) { clearTimeout(this.timer); this.timer = null }
    const batch = this.queue.slice(0, BATCH_SIZE)
    if (!batch.length) return
    this.queue = this.queue.slice(BATCH_SIZE)
    const ok = await this.post('/events', {
      source: this.source,
      app_version: this.appVersion,
      os: this.os,
      install_id: this.installId,
      session_id: this.sessionId,
      user_id: this.userId,
      events: batch,
    })
    // Put events back only for a TRANSIENT failure. A 4xx means the service rejected
    // this payload and always will, so requeuing would loop forever; the event_id
    // makes a re-send after a lost ack harmless.
    if (ok === 'retry') this.queue = [...batch, ...this.queue].slice(-MAX_QUEUE)
    if (this.queue.length) this.schedule()
  }

  /** A note typed by a user, sent straight to the dashboard's feedback board. */
  async feedback(
    message: string,
    extra?: { title?: string; category?: string; region?: string },
  ): Promise<boolean> {
    if (!this.enabled || !message.trim()) return false
    return await this.post('/feedback', {
      source: this.source,
      app_version: this.appVersion,
      os: this.os,
      install_id: this.installId,
      session_id: this.sessionId,
      user_id: this.userId,
      user_name: this.userName,
      region: extra?.region ?? null,
      category: extra?.category ?? null,
      title: extra?.title ?? null,
      message: message.trim(),
      event_id: uuid(),
    }) === true
  }

  private async post(path: string, body: unknown): Promise<true | 'retry' | false> {
    const ctrl = new AbortController()
    const kill = setTimeout(() => ctrl.abort(), TIMEOUT_MS)
    try {
      const res = await fetch(this.url + path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${this.key}` },
        body: JSON.stringify(body),
        signal: ctrl.signal,
      })
      if (res.ok) return true
      return res.status >= 400 && res.status < 500 ? false : 'retry'
    } catch {
      return 'retry'   // offline, DNS, timeout — keep it for the next attempt
    } finally {
      clearTimeout(kill)
    }
  }
}
