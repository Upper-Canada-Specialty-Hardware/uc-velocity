import type {
  Paginated, ListParams,
  Part, PartCreate,
  Labor, LaborCreate,
  Miscellaneous, MiscellaneousCreate,
  CostCode, CostCodeCreate, CostCodeUpdate,
  Profile, ProfileCreate, ProfileUpdate, ProfileType,
  Contact, ContactCreate, ContactUpdate,
  Project, ProjectCreate, ProjectFull, ProjectListView, ProjectSearchResult,
  Quote, QuoteCreate, QuoteUpdate, QuoteLineItem, QuoteLineItemCreate, QuoteLineItemUpdate,
  PurchaseOrder, PurchaseOrderCreate, PurchaseOrderUpdate, POLineItem, POLineItemCreate,
  POReceiving, POReceivingCreate, POSnapshot, PORevertPreview,
  POCommitEditsRequest, POCommitEditsResponse,
  Invoice, InvoiceCreate, InvoiceStatusUpdate, QuoteSnapshot, RevertPreview,
  InvoiceSnapshot, InvoiceRevertPreview,
  MarkupControlToggleRequest, MarkupControlToggleResponse,
  CommitEditsRequest, CommitEditsResponse,
  CompanySettings, CompanySettingsUpdate, InvoiceSummaryItem,
  BacklogQuoteItem, InventoryHealthReport, PricebookImportResult, MigrationResult,
  SystemRate, SystemRateCreate, SystemRateUpdate
} from '@/types';

// API base URL - configurable via environment variable for production
const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';

/** Fetch wrapper that handles JSON serialization and extracts error details from FastAPI responses. */
async function request<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  // Attach the Clerk session token so the backend can attribute audit-trail actions.
  let authHeaders: Record<string, string> = {};
  try {
    const clerk = (window as unknown as { Clerk?: { session?: { getToken?: () => Promise<string | null> } } }).Clerk;
    const token = await clerk?.session?.getToken?.();
    if (token) authHeaders = { Authorization: `Bearer ${token}` };
  } catch {
    // Best-effort: if Clerk isn't ready yet, proceed without a token.
  }

  const response = await fetch(`${API_BASE}${endpoint}`, {
    // Disable HTTP caching for all API calls so a cold full-page reload never
    // replays a stale cached GET. Placed before ...options so a caller can
    // still override it per-call if ever needed.
    cache: 'no-store',
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders,
      ...options.headers,
    },
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    let message = `API error: ${response.status}`;
    if (typeof error.detail === 'string') {
      message = error.detail;
    } else if (Array.isArray(error.detail)) {
      message = error.detail.map((e: any) => e.msg).join('; ');
    }
    throw new Error(message);
  }

  return response.json();
}

/** Build a `?key=value&...` querystring from a plain object, skipping null/undefined values. */
function buildQuery(params: object): string {
  const parts: string[] = [];
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined) continue;
    parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`);
  }
  return parts.length ? `?${parts.join('&')}` : '';
}

export const api = {
  // ===== Parts =====
  parts: {
    // Paginated list for list views. Returns {items, total, limit, offset}.
    // Pass `vendor_id` to filter server-side to parts linked to that vendor.
    list: (params: ListParams & { vendor_id?: number } = {}) =>
      request<Paginated<Part>>(`/parts/${buildQuery(params)}`),
    // Convenience: unbounded fetch (limit=0) returning just items.
    // Used by autocomplete/dropdown loaders that need every row.
    // Pass `{ vendor_id }` to filter server-side.
    getAll: (params: { vendor_id?: number } = {}) =>
      request<Paginated<Part>>(`/parts/${buildQuery({ ...params, limit: 0 })}`).then(r => r.items),
    get: (id: number) => request<Part>(`/parts/${id}`),
    create: (data: PartCreate) =>
      request<Part>('/parts/', { method: 'POST', body: JSON.stringify(data) }),
    update: (id: number, data: Partial<PartCreate>) =>
      request<Part>(`/parts/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    delete: (id: number) =>
      request<{ message: string }>(`/parts/${id}`, { method: 'DELETE' }),
  },

  // ===== Labor =====
  labor: {
    getAll: () => request<Labor[]>('/labor/'),
    get: (id: number) => request<Labor>(`/labor/${id}`),
    create: (data: LaborCreate) =>
      request<Labor>('/labor/', { method: 'POST', body: JSON.stringify(data) }),
    update: (id: number, data: Partial<LaborCreate>) =>
      request<Labor>(`/labor/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    delete: (id: number) =>
      request<{ message: string }>(`/labor/${id}`, { method: 'DELETE' }),
  },

  // ===== Profiles =====
  profiles: {
    list: (params: ListParams & { profile_type?: ProfileType } = {}) =>
      request<Paginated<Profile>>(`/profiles/${buildQuery(params)}`),
    // Convenience: unbounded fetch returning just items.
    getAll: (type?: ProfileType) =>
      request<Paginated<Profile>>(
        `/profiles/${buildQuery({ limit: 0, profile_type: type })}`
      ).then(r => r.items),
    get: (id: number) => request<Profile>(`/profiles/${id}`),
    create: (data: ProfileCreate) =>
      request<Profile>('/profiles/', { method: 'POST', body: JSON.stringify(data) }),
    update: (id: number, data: ProfileUpdate) =>
      request<Profile>(`/profiles/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    delete: (id: number) =>
      request<{ message: string }>(`/profiles/${id}`, { method: 'DELETE' }),

    // Contact management
    addContact: (profileId: number, data: ContactCreate) =>
      request<Contact>(`/profiles/${profileId}/contacts`, { method: 'POST', body: JSON.stringify(data) }),
    updateContact: (profileId: number, contactId: number, data: ContactUpdate) =>
      request<Contact>(`/profiles/${profileId}/contacts/${contactId}`, { method: 'PUT', body: JSON.stringify(data) }),
    deleteContact: (profileId: number, contactId: number) =>
      request<{ message: string }>(`/profiles/${profileId}/contacts/${contactId}`, { method: 'DELETE' }),
  },

  // ===== Projects =====
  projects: {
    list: (params: ListParams = {}) =>
      request<Paginated<Project>>(`/projects/${buildQuery(params)}`),
    getAll: () =>
      request<Paginated<Project>>('/projects/?limit=0').then(r => r.items),
    getListView: () => request<ProjectListView[]>('/projects/list-view'),
    search: (q: string) =>
      request<ProjectSearchResult[]>(`/projects/search?q=${encodeURIComponent(q)}`),
    get: (id: number) => request<ProjectFull>(`/projects/${id}`),
    create: (data: ProjectCreate) =>
      request<Project>('/projects/', { method: 'POST', body: JSON.stringify(data) }),
    update: (id: number, data: Partial<ProjectCreate>) =>
      request<Project>(`/projects/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    delete: (id: number) =>
      request<{ message: string }>(`/projects/${id}`, { method: 'DELETE' }),
  },

  // ===== Miscellaneous =====
  misc: {
    getAll: () => request<Miscellaneous[]>('/misc/'),
    get: (id: number) => request<Miscellaneous>(`/misc/${id}`),
    create: (data: MiscellaneousCreate) =>
      request<Miscellaneous>('/misc/', { method: 'POST', body: JSON.stringify(data) }),
    update: (id: number, data: Partial<MiscellaneousCreate>) =>
      request<Miscellaneous>(`/misc/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    delete: (id: number) =>
      request<{ message: string }>(`/misc/${id}`, { method: 'DELETE' }),
  },

  // ===== System Rates =====
  systemRates: {
    getParking: () => request<SystemRate>('/system-rates/parking'),
    updateParking: (data: SystemRateUpdate) =>
      request<SystemRate>('/system-rates/parking', { method: 'PUT', body: JSON.stringify(data) }),
    getTravelDistance: () => request<SystemRate[]>('/system-rates/travel-distance'),
    createTravelDistance: (data: SystemRateCreate) =>
      request<SystemRate>('/system-rates/travel-distance', { method: 'POST', body: JSON.stringify(data) }),
    updateTravelDistance: (id: number, data: SystemRateUpdate) =>
      request<SystemRate>(`/system-rates/travel-distance/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    deleteTravelDistance: (id: number) =>
      request<SystemRate>(`/system-rates/travel-distance/${id}`, { method: 'DELETE' }),
    getPmsDefault: () => request<{ default_pms_percent: number | null }>('/system-rates/pms-default'),
    updatePmsDefault: (value: number | null) =>
      request<{ default_pms_percent: number | null }>('/system-rates/pms-default', {
        method: 'PUT', body: JSON.stringify({ default_pms_percent: value })
      }),
  },

  // ===== Quotes =====
  quotes: {
    list: (params: ListParams = {}) =>
      request<Paginated<Quote>>(`/quotes/${buildQuery(params)}`),
    getAll: () =>
      request<Paginated<Quote>>('/quotes/?limit=0').then(r => r.items),
    get: (id: number) => request<Quote>(`/quotes/${id}`),
    create: (data: QuoteCreate) =>
      request<Quote>('/quotes/', { method: 'POST', body: JSON.stringify(data) }),
    update: (id: number, data: QuoteUpdate) =>
      request<Quote>(`/quotes/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    // Edit the "created on" date/time (bumps version, snapshots, changes quote number).
    // `iso` is a UTC ISO-8601 string (e.g. from new Date(localValue).toISOString()).
    updateCreatedAt: (id: number, iso: string) =>
      request<Quote>(`/quotes/${id}/created-at`, { method: 'PUT', body: JSON.stringify({ created_at: iso }) }),
    delete: (id: number) =>
      request<{ message: string }>(`/quotes/${id}`, { method: 'DELETE' }),

    // Line items
    getLines: (quoteId: number) =>
      request<QuoteLineItem[]>(`/quotes/${quoteId}/lines`),
    addLine: (quoteId: number, data: QuoteLineItemCreate) =>
      request<QuoteLineItem>(`/quotes/${quoteId}/lines`, { method: 'POST', body: JSON.stringify(data) }),
    updateLine: (quoteId: number, lineId: number, data: QuoteLineItemUpdate) =>
      request<QuoteLineItem>(`/quotes/${quoteId}/lines/${lineId}`, { method: 'PUT', body: JSON.stringify(data) }),
    deleteLine: (quoteId: number, lineId: number) =>
      request<{ message: string }>(`/quotes/${quoteId}/lines/${lineId}`, { method: 'DELETE' }),

    // Invoices
    getInvoices: (quoteId: number) =>
      request<Invoice[]>(`/quotes/${quoteId}/invoices`),
    createInvoice: (quoteId: number, data: InvoiceCreate) =>
      request<Invoice>(`/quotes/${quoteId}/invoices`, { method: 'POST', body: JSON.stringify(data) }),

    // Snapshots (Audit Trail)
    getSnapshots: (quoteId: number) =>
      request<QuoteSnapshot[]>(`/quotes/${quoteId}/snapshots`),
    getSnapshot: (quoteId: number, version: number) =>
      request<QuoteSnapshot>(`/quotes/${quoteId}/snapshots/${version}`),

    // Revert
    previewRevert: (quoteId: number, version: number) =>
      request<RevertPreview>(`/quotes/${quoteId}/revert/${version}/preview`),
    revert: (quoteId: number, version: number) =>
      request<Quote>(`/quotes/${quoteId}/revert/${version}`, { method: 'POST' }),

    // Markup Control
    toggleMarkupControl: (quoteId: number, data: MarkupControlToggleRequest) =>
      request<MarkupControlToggleResponse>(`/quotes/${quoteId}/markup-control`, { method: 'POST', body: JSON.stringify(data) }),

    // Clone
    clone: (quoteId: number) =>
      request<Quote>(`/quotes/${quoteId}/clone`, { method: 'POST' }),

    // Commit Edits (Edit Mode)
    commitEdits: (quoteId: number, data: CommitEditsRequest) =>
      request<CommitEditsResponse>(`/quotes/${quoteId}/commit`, { method: 'POST', body: JSON.stringify(data) }),
  },

  // ===== Invoices =====
  invoices: {
    get: (id: number) => request<Invoice>(`/invoices/${id}`),
    updateStatus: (id: number, data: InvoiceStatusUpdate) =>
      request<Invoice>(`/invoices/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    // Edit the "created on" date/time (bumps version, snapshots; invoice number unchanged).
    // `iso` is a UTC ISO-8601 string (e.g. from new Date(localValue).toISOString()).
    updateCreatedAt: (id: number, iso: string) =>
      request<Invoice>(`/invoices/${id}/created-at`, { method: 'PUT', body: JSON.stringify({ created_at: iso }) }),
    getSummary: (startDate: string, endDate: string, projectId?: number) => {
      const qs = new URLSearchParams({ start_date: startDate, end_date: endDate })
      if (projectId !== undefined) qs.set('project_id', String(projectId))
      return request<InvoiceSummaryItem[]>(`/invoices/?${qs.toString()}`)
    },

    // Snapshots (Audit Trail)
    getSnapshots: (invoiceId: number) =>
      request<InvoiceSnapshot[]>(`/invoices/${invoiceId}/snapshots`),
    getSnapshot: (invoiceId: number, version: number) =>
      request<InvoiceSnapshot>(`/invoices/${invoiceId}/snapshots/${version}`),

    // Revert
    previewRevert: (invoiceId: number, version: number) =>
      request<InvoiceRevertPreview>(`/invoices/${invoiceId}/revert/${version}/preview`),
    revert: (invoiceId: number, version: number) =>
      request<Invoice>(`/invoices/${invoiceId}/revert/${version}`, { method: 'POST' }),
  },

  // ===== Company Settings =====
  companySettings: {
    get: () => request<CompanySettings>('/company-settings/'),
    update: (data: CompanySettingsUpdate) =>
      request<CompanySettings>('/company-settings/', { method: 'PUT', body: JSON.stringify(data) }),
  },

  // ===== Purchase Orders =====
  purchaseOrders: {
    // Core CRUD
    list: (params: ListParams = {}) =>
      request<Paginated<PurchaseOrder>>(`/purchase-orders/${buildQuery(params)}`),
    getAll: () =>
      request<Paginated<PurchaseOrder>>('/purchase-orders/?limit=0').then(r => r.items),

    get: (id: number) => request<PurchaseOrder>(`/purchase-orders/${id}`),

    create: (data: PurchaseOrderCreate) =>
      request<PurchaseOrder>('/purchase-orders/', { method: 'POST', body: JSON.stringify(data) }),

    update: (id: number, data: PurchaseOrderUpdate) =>
      request<PurchaseOrder>(`/purchase-orders/${id}`, { method: 'PUT', body: JSON.stringify(data) }),

    // Edit the "created on" date/time (bumps version, snapshots, changes PO number).
    // `iso` is a UTC ISO-8601 string (e.g. from new Date(localValue).toISOString()).
    updateCreatedAt: (id: number, iso: string) =>
      request<PurchaseOrder>(`/purchase-orders/${id}/created-at`, { method: 'PUT', body: JSON.stringify({ created_at: iso }) }),

    delete: (id: number) =>
      request<{ message: string }>(`/purchase-orders/${id}`, { method: 'DELETE' }),

    // Line items
    getLines: (poId: number) =>
      request<POLineItem[]>(`/purchase-orders/${poId}/lines`),

    addLine: (poId: number, data: POLineItemCreate) =>
      request<POLineItem>(`/purchase-orders/${poId}/lines`, { method: 'POST', body: JSON.stringify(data) }),

    updateLine: (poId: number, lineId: number, data: POLineItemCreate) =>
      request<POLineItem>(`/purchase-orders/${poId}/lines/${lineId}`, { method: 'PUT', body: JSON.stringify(data) }),

    deleteLine: (poId: number, lineId: number) =>
      request<{ message: string }>(`/purchase-orders/${poId}/lines/${lineId}`, { method: 'DELETE' }),

    // Batch commit
    commitEdits: (poId: number, data: POCommitEditsRequest) =>
      request<POCommitEditsResponse>(`/purchase-orders/${poId}/commit`, { method: 'POST', body: JSON.stringify(data) }),

    // Receiving
    getReceivings: (poId: number) =>
      request<POReceiving[]>(`/purchase-orders/${poId}/receivings`),

    createReceiving: (poId: number, data: POReceivingCreate) =>
      request<POReceiving>(`/purchase-orders/${poId}/receivings`, { method: 'POST', body: JSON.stringify(data) }),

    // Snapshots
    getSnapshots: (poId: number) =>
      request<POSnapshot[]>(`/purchase-orders/${poId}/snapshots`),

    getSnapshot: (poId: number, version: number) =>
      request<POSnapshot>(`/purchase-orders/${poId}/snapshots/${version}`),

    // Revert
    previewRevert: (poId: number, version: number) =>
      request<PORevertPreview>(`/purchase-orders/${poId}/revert/${version}/preview`, { method: 'POST' }),

    revert: (poId: number, version: number) =>
      request<PurchaseOrder>(`/purchase-orders/${poId}/revert/${version}`, { method: 'POST' }),

    // Clone
    clone: (poId: number) =>
      request<PurchaseOrder>(`/purchase-orders/${poId}/clone`, { method: 'POST' }),
  },

  // ===== Cost Codes =====
  costCodes: {
    getAll: () => request<CostCode[]>('/cost-codes/'),
    get: (id: number) => request<CostCode>(`/cost-codes/${id}`),
    create: (data: CostCodeCreate) =>
      request<CostCode>('/cost-codes/', { method: 'POST', body: JSON.stringify(data) }),
    update: (id: number, data: CostCodeUpdate) =>
      request<CostCode>(`/cost-codes/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    delete: (id: number) =>
      request<{ message: string }>(`/cost-codes/${id}`, { method: 'DELETE' }),
  },

  // ===== Reports =====
  reports: {
    getBacklogQuotes: () => request<BacklogQuoteItem[]>('/reports/backlog-quotes'),
    getInventoryHealth: () => request<InventoryHealthReport>('/reports/inventory-health'),
  },

  // ===== Legacy Migration =====
  migration: {
    import: async (files: File[]): Promise<MigrationResult> => {
      const formData = new FormData();
      files.forEach(f => formData.append('files', f));
      const response = await fetch(`${API_BASE}/migration/import`, {
        method: 'POST',
        body: formData,
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        let message = `API error: ${response.status}`;
        if (typeof error.detail === 'string') {
          message = error.detail;
        } else if (Array.isArray(error.detail)) {
          message = error.detail.map((e: any) => e.msg).join('; ');
        }
        throw new Error(message);
      }
      return response.json();
    },
  },

  // ===== Vendor Pricebook =====
  vendorPricebook: {
    import: async (vendorId: number, file: File): Promise<PricebookImportResult> => {
      const formData = new FormData();
      formData.append('file', file);
      const response = await fetch(`${API_BASE}/vendors/${vendorId}/pricebook/import`, {
        method: 'POST',
        body: formData,
        // Note: do NOT set Content-Type header — browser sets multipart boundary automatically
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        let message = `API error: ${response.status}`;
        if (typeof error.detail === 'string') {
          message = error.detail;
        } else if (Array.isArray(error.detail)) {
          message = error.detail.map((e: any) => e.msg).join('; ');
        }
        throw new Error(message);
      }
      return response.json();
    },
  },
};
