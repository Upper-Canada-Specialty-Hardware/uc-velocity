// TypeScript interfaces matching backend schemas

// ===== Pagination =====
// Standard response envelope for paginated list endpoints (see backend
// schemas.Paginated). `total` is the unfiltered total for the query
// independent of offset/limit, so the UI can render "X of Y" without an
// extra count round trip.
export interface Paginated<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ListParams {
  offset?: number;
  limit?: number;
}

// ===== Company Settings =====
export interface CompanySettings {
  id: number;
  name: string;
  address?: string;
  phone?: string;
  fax?: string;
  gst_number?: string;
  hst_rate: number;
  default_pms_percent?: number | null;
  logo_data_url?: string | null;
}

export interface CompanySettingsUpdate {
  name?: string;
  address?: string;
  phone?: string;
  fax?: string;
  gst_number?: string;
  hst_rate?: number;
  default_pms_percent?: number | null;
  logo_data_url?: string | null;
}

// ===== System Rates =====
export interface SystemRate {
  id: number;
  rate_type: 'parking' | 'travel_distance';
  description: string;
  unit_price: number;
  markup_percent: number;
  sort_order: number;
  is_active: boolean;
  linked_misc_id?: number;
}

export interface SystemRateCreate {
  description: string;
  unit_price: number;
  markup_percent?: number;
  sort_order?: number;
}

export interface SystemRateUpdate {
  description?: string;
  unit_price?: number;
  markup_percent?: number;
  sort_order?: number;
}

// ===== Invoice Summary (for Reports) =====
export interface InvoiceSummaryItem {
  invoice_id: number;
  invoice_date: string;
  uca_project_number: string;
  project_name: string;
  customer_name: string;
  client_po_number?: string;
  net_sales: number;
  hst_amount: number;
  grand_total: number;
}

// ===== Backlog Quotes Report =====
export interface BacklogLineItem {
  line_item_id: number;
  item_type: string;
  description: string;
  quantity: number;
  qty_fulfilled: number;
  qty_pending: number;
  unit_price: number;
  backlog_value: number;
}

export interface BacklogQuoteItem {
  quote_id: number;
  quote_number: string;
  uca_project_number: string;
  customer_name: string;
  project_name: string;
  client_po_number?: string;
  status: string;
  quote_total: number;
  backlog_total: number;
  line_items: BacklogLineItem[];
}

// ===== Inventory Health (UX-7) =====
export type InventoryHealthIssueCode =
  | "zero_cost"
  | "description_matches_part_number"
  | "description_has_escaped_quotes";

export interface InventoryHealthIssue {
  part_id: number;
  part_number: string;
  description: string;
  cost: number;
  issues: InventoryHealthIssueCode[];
}

export interface InventoryHealthReport {
  total_parts: number;
  flagged: number;
  items: InventoryHealthIssue[];
}

// ===== Cost Codes =====
export interface CostCode {
  id: number;
  code: string;
  description: string;
}

export interface CostCodeCreate {
  code: string;
  description: string;
}

export interface CostCodeUpdate {
  code?: string;
  description?: string;
}

// ===== Labor =====
export interface Labor {
  id: number;
  description: string;
  hours: number;
  rate: number;
  markup_percent: number;
  category_id?: number;
}

export interface LaborCreate {
  description: string;
  hours: number;
  rate: number;
  markup_percent: number;
  category_id?: number;
}

// ===== Parts =====
export interface Part {
  id: number;
  part_number: string;
  description: string;
  cost: number;
  markup_percent: number;
  category_id?: number;
  labor_items?: Labor[];  // Linked labor items
  vendor_id?: number;
  list_price?: number;
  discount_percent?: number;  // Per-part discount override
}

export interface PartCreate {
  part_number: string;
  description: string;
  cost: number;
  markup_percent?: number;
  category_id?: number;
  linked_labor_ids?: number[];
  vendor_id?: number;
  list_price?: number;
  discount_percent?: number;
}

// ===== Miscellaneous =====
export interface Miscellaneous {
  id: number;
  description: string;
  unit_price: number;
  markup_percent: number;
  category_id?: number;
  is_system_item: boolean;
}

export interface MiscellaneousCreate {
  description: string;
  unit_price: number;
  markup_percent: number;
  category_id?: number;
}

// ===== Contact Phone =====
export type PhoneType = 'work' | 'mobile';

export interface ContactPhone {
  id: number;
  contact_id: number;
  type: PhoneType;
  number: string;
}

export interface ContactPhoneCreate {
  type: PhoneType;
  number: string;
}

// ===== Contacts =====
export interface Contact {
  id: number;
  profile_id: number;
  name: string;
  job_title?: string;
  email?: string;
  phone_numbers: ContactPhone[];
}

export interface ContactCreate {
  name: string;
  job_title?: string;
  email?: string;
  phone_numbers: ContactPhoneCreate[];
}

export interface ContactUpdate {
  name?: string;
  job_title?: string;
  email?: string;
  phone_numbers?: ContactPhoneCreate[];
}

// ===== Profiles =====
export type ProfileType = 'customer' | 'vendor';

export interface Profile {
  id: number;
  name: string;
  type: ProfileType;
  pst: string;
  address: string;
  postal_code: string;
  default_discount_percent?: number;
  contacts: Contact[];
}

export interface ProfileCreate {
  name: string;
  type: ProfileType;
  pst: string;
  address: string;
  postal_code: string;
  default_discount_percent?: number;
  contacts: ContactCreate[];
}

export interface ProfileUpdate {
  name?: string;
  type?: ProfileType;
  pst?: string;
  address?: string;
  postal_code?: string;
  default_discount_percent?: number;
}

// ===== Projects =====
export interface Project {
  id: number;
  name: string;
  customer_id: number;
  created_on: string;
  status: string;
  ucsh_project_number: string | null;
  uca_project_number: string;
  project_lead: string | null;
  customer: Profile;
}

export interface ProjectCreate {
  name: string;
  customer_id: number;
  status?: string;
  ucsh_project_number?: string;
  project_lead?: string;
}

export interface ProjectUpdate {
  name?: string;
  customer_id?: number;
  status?: string;
  ucsh_project_number?: string;
  project_lead?: string | null;
}

// Lighter shapes returned by GET /projects/{id} — no nested line items.
// Open a specific quote/PO via its own endpoint to load its line items.
export type QuoteSummary = Omit<Quote, 'line_items' | 'cost_code'>;
export type POSummary = Omit<PurchaseOrder, 'line_items' | 'cost_code'>;

export interface ProjectFull extends Project {
  quotes: QuoteSummary[];
  purchase_orders: POSummary[];
}

// ===== Projects List View (lightweight, aggregated) =====
export interface ProjectListView {
  id: number;
  name: string;
  uca_project_number: string;
  ucsh_project_number: string | null;
  customer_id: number;
  customer_name: string;
  project_lead: string | null;
  status: string;
  created_on: string;
  quote_count: number;
  po_count: number;
  latest_quote_number: string | null;
  latest_po_number: string | null;
}

export interface MatchedQuoteRef {
  id: number;
  quote_number: string;
}

export interface MatchedPORef {
  id: number;
  po_number: string;
  vendor_name: string;
}

export interface ProjectSearchResult extends ProjectListView {
  matched_quotes: MatchedQuoteRef[];
  matched_pos: MatchedPORef[];
}

// ===== Quote Line Items =====
export type LineItemType = 'labor' | 'part' | 'misc';

export interface QuoteLineItem {
  id: number;
  quote_id: number;
  item_type: LineItemType;
  labor_id?: number;
  part_id?: number;
  misc_id?: number;
  description?: string;
  quantity: number;  // Qty Ordered
  unit_price?: number;
  qty_pending: number;  // Remaining to fulfill
  qty_fulfilled: number;  // Total fulfilled across all invoices
  is_pms: boolean;  // True for PMS items (Project Management Services)
  pms_percent?: number;  // Percentage value for PMS % items
  original_markup_percent?: number;  // Individual markup before global override
  base_cost?: number;  // Base cost used for recalculation
  markup_percent?: number;  // Per-line-item markup
  labor?: Labor;
  part?: Part;
  miscellaneous?: Miscellaneous;
}

export interface QuoteLineItemCreate {
  item_type: LineItemType;
  labor_id?: number;
  part_id?: number;
  misc_id?: number;
  description?: string;
  quantity: number;
  unit_price?: number;
  is_pms?: boolean;  // True for PMS items (Project Management Services)
  pms_percent?: number;  // Percentage value for PMS % items
}

export interface QuoteLineItemUpdate {
  quantity?: number;
  unit_price?: number;
}

// ===== Quotes =====
export type QuoteStatus = 'Draft' | 'Work Order' | 'Invoiced' | 'Closed';

// ===== Purchase Orders Status =====
export type POStatus = 'Draft' | 'Sent' | 'Received' | 'Closed';

export interface Quote {
  id: number;
  project_id: number;
  quote_sequence: number;  // Per-project sequence number (1, 2, 3...)
  quote_number: string;    // Formatted: "{UCA Project Number}-{Sequence:04d}-{Version}"
  created_at: string;
  status: QuoteStatus;
  current_version: number;
  client_po_number?: string | null;
  work_description?: string | null;
  hardware_schedule_version?: string | null;
  markup_control_enabled: boolean;  // Markup Control toggle
  parts_markup_percent?: number | null;  // Section-level markup for parts
  labor_markup_percent?: number | null;  // Section-level markup for labor
  misc_markup_percent?: number | null;  // Section-level markup for misc
  cost_code_id?: number | null;
  cost_code?: CostCode | null;
  legacy_imported?: boolean;  // True when the row came in from the original UC Vision CSV migration
  line_items: QuoteLineItem[];
}

export interface QuoteCreate {
  project_id: number;
  client_po_number?: string;
  work_description?: string;
  hardware_schedule_version?: string;
  cost_code_id?: number;
}

export interface QuoteUpdate {
  client_po_number?: string | null;
  work_description?: string | null;
  hardware_schedule_version?: string | null;
  cost_code_id?: number | null;
}

// ===== PO Line Items =====
export type POLineItemType = 'part' | 'misc'; // NO labor for POs

export interface POLineItem {
  id: number;
  purchase_order_id: number;
  item_type: POLineItemType;
  part_id?: number;
  description?: string;
  quantity: number;
  unit_price?: number;
  qty_pending: number;
  qty_received: number;
  actual_unit_price?: number | null;
  part?: Part;
}

export interface POLineItemCreate {
  item_type: POLineItemType;
  part_id?: number;
  description?: string;
  quantity: number;
  unit_price?: number;
}

// ===== Purchase Orders =====
export interface PurchaseOrder {
  id: number;
  project_id: number;
  vendor_id: number;
  created_at: string;
  po_sequence: number;
  po_number: string;
  current_version: number;
  status: POStatus;
  work_description?: string | null;
  vendor_po_number?: string | null;
  expected_delivery_date?: string | null;
  cost_code_id?: number | null;
  cost_code?: CostCode | null;
  legacy_imported?: boolean;  // True when the row came in from the original UC Vision CSV migration
  vendor: Profile;
  line_items: POLineItem[];
}

export interface PurchaseOrderCreate {
  project_id: number;
  vendor_id: number;
  status?: POStatus;
  work_description?: string;
  vendor_po_number?: string;
  expected_delivery_date?: string;
  cost_code_id?: number;
}

export interface PurchaseOrderUpdate {
  status?: POStatus;
  work_description?: string | null;
  vendor_po_number?: string | null;
  expected_delivery_date?: string | null;
  cost_code_id?: number | null;
}

// ===== PO Receiving =====
export interface POReceivingLineItem {
  id: number;
  receiving_id: number;
  po_line_item_id: number | null;
  item_type: POLineItemType;
  description?: string | null;
  part_id?: number | null;
  unit_price?: number | null;
  actual_unit_price?: number | null;
  qty_ordered?: number | null;
  qty_received_this_receiving?: number | null;
  qty_received_total?: number | null;
  qty_pending_after?: number | null;
}

export interface POReceiving {
  id: number;
  purchase_order_id: number;
  created_at: string;
  received_date: string;
  notes?: string | null;
  voided_at?: string | null;
  voided_by_snapshot_id?: number | null;
  line_items: POReceivingLineItem[];
}

export interface POReceivingLineItemCreate {
  po_line_item_id: number;
  qty_received: number;
  actual_unit_price?: number;
}

export interface POReceivingCreate {
  received_date: string;
  notes?: string;
  line_items: POReceivingLineItemCreate[];
}

// ===== PO Snapshots =====
export interface POLineItemSnapshot {
  id: number;
  snapshot_id: number;
  original_line_item_id?: number;
  item_type: POLineItemType;
  part_id?: number;
  description: string;
  quantity: number;
  unit_price: number;
  qty_pending: number;
  qty_received: number;
  actual_unit_price?: number;
  is_deleted: boolean;
}

export type POSnapshotActionType = 'create' | 'edit' | 'delete' | 'receive' | 'status_change' | 'date_edit' | 'revert';

export interface POSnapshot {
  id: number;
  purchase_order_id: number;
  version: number;
  action_type: POSnapshotActionType;
  action_description?: string;
  created_at: string;
  actor_user_id?: string | null;
  actor_email?: string | null;
  receiving_id?: number;
  line_items_states: POLineItemSnapshot[];
}

// ===== PO Revert Preview =====
export interface PORevertPreview {
  target_version: number;
  receivings_to_void: POReceiving[];
  changes_summary: string;
}

// ===== PO Commit Edits =====
export interface StagedPOLineItemChange {
  action: 'add' | 'edit' | 'delete';
  line_item_id?: number;
  item_type?: POLineItemType;
  part_id?: number;
  description?: string;
  quantity?: number;
  unit_price?: number;
}

export interface POCommitEditsRequest {
  changes: StagedPOLineItemChange[];
  commit_message?: string;
}

export interface POCommitEditsResponse {
  success: boolean;
  message: string;
  purchase_order: PurchaseOrder;
  snapshot_version: number;
}

// ===== PO Editor Mode (frontend-only state) =====
export type POEditorMode = 'view' | 'edit' | 'receiving';

/**
 * Represents a staged edit to an existing PO line item (for Edit Mode).
 * Only contains the changed fields - undefined means "unchanged".
 */
export interface StagedPOEdit {
  originalItem: POLineItem;
  quantity?: number;
  unit_price?: number;
  description?: string;
}

/**
 * Represents a new PO line item being staged for addition (for Edit Mode).
 * Uses a temporary negative ID to identify it before commit.
 */
export interface StagedPOAdd {
  tempId: number;
  item_type: POLineItemType;
  part_id?: number;
  description?: string;
  quantity: number;
  unit_price?: number;
  part?: Part;  // Hydrated reference for display
}

// ===== Invoice Line Items =====
export interface InvoiceLineItem {
  id: number;
  invoice_id: number;
  quote_line_item_id?: number;
  item_type: LineItemType;
  description?: string;
  unit_price?: number;
  qty_ordered: number;
  qty_fulfilled_this_invoice: number;
  qty_fulfilled_total: number;
  qty_pending_after: number;
  labor_id?: number;
  part_id?: number;
  misc_id?: number;
}

// ===== Invoices =====
export type InvoiceStatus = 'Sent' | 'Paid' | 'Voided';

export interface Invoice {
  id: number;
  quote_id: number;
  invoice_sequence: number;
  quote_version: number;
  current_version: number;
  invoice_number?: string | null; // Computed: "{invoice_sequence}-{UCA}-{quote_seq}-{quote_version}"
  created_at: string;
  status: InvoiceStatus;
  notes?: string;
  voided_at?: string;
  voided_by_snapshot_id?: number;
  line_items: InvoiceLineItem[];
}

export interface LineItemFulfillment {
  line_item_id: number;
  quantity: number;
}

export interface InvoiceCreate {
  fulfillments: LineItemFulfillment[];
  notes?: string;
}

export interface InvoiceStatusUpdate {
  status: 'Sent' | 'Paid';
}

// ===== Quote Line Item Snapshots =====
export interface QuoteLineItemSnapshot {
  id: number;
  snapshot_id: number;
  original_line_item_id?: number;
  item_type: LineItemType;
  labor_id?: number;
  part_id?: number;
  misc_id?: number;
  description?: string;
  quantity: number;
  unit_price?: number;
  qty_pending: number;
  qty_fulfilled: number;
  is_deleted: boolean;
  is_pms: boolean;  // True for PMS items (Project Management Services)
  pms_percent?: number;  // Percentage value for PMS % items
  original_markup_percent?: number;  // Individual markup before global override
  base_cost?: number;  // Base cost used for recalculation
  markup_percent?: number;  // Per-line-item markup
}

// ===== Pricebook Import =====
export interface PricebookImportResult {
  created: number;
  updated: number;
  errors: string[];
}

// ===== Legacy Migration =====
export interface MigrationResult {
  success: boolean;
  counts: Record<string, number>;
  warnings: string[];
  errors: string[];
  skipped_files: string[];
}

// ===== Quote Snapshots =====
export type SnapshotActionType = 'create' | 'edit' | 'delete' | 'invoice' | 'date_edit' | 'revert';

export interface QuoteSnapshot {
  id: number;
  quote_id: number;
  version: number;
  action_type: SnapshotActionType;
  action_description?: string;
  created_at: string;
  actor_user_id?: string | null;
  actor_email?: string | null;
  invoice_id?: number;
  line_item_states: QuoteLineItemSnapshot[];
}

// ===== Revert Preview =====
export interface RevertPreview {
  target_version: number;
  invoices_to_void: Invoice[];
  changes_summary: string;
}

// ===== Invoice Snapshots =====
export type InvoiceSnapshotActionType = 'date_edit' | 'revert';

export interface InvoiceLineItemSnapshot {
  id: number;
  snapshot_id: number;
  original_line_item_id?: number;
  quote_line_item_id?: number;
  item_type: LineItemType;
  description?: string;
  unit_price?: number;
  qty_ordered?: number;
  qty_fulfilled_this_invoice?: number;
  qty_fulfilled_total?: number;
  qty_pending_after?: number;
  labor_id?: number;
  part_id?: number;
  misc_id?: number;
}

export interface InvoiceSnapshot {
  id: number;
  invoice_id: number;
  version: number;
  action_type: InvoiceSnapshotActionType;
  action_description?: string;
  created_at: string;
  entity_created_at?: string | null;
  actor_user_id?: string | null;
  actor_email?: string | null;
  line_item_states: InvoiceLineItemSnapshot[];
}

export interface InvoiceRevertPreview {
  target_version: number;
  changes_summary: string;
}

// ===== Staged Fulfillment (frontend-only state) =====
export interface StagedFulfillment {
  lineItemId: number;
  quantity: number;
}

// ===== Markup Control Toggle =====
export interface MarkupControlToggleRequest {
  enabled: boolean;
  parts_markup_percent?: number;
  labor_markup_percent?: number;
  misc_markup_percent?: number;
}

export interface MarkupControlToggleResponse {
  success: boolean;
  message: string;
  quote: Quote;
}

// ===== Commit Edits (Edit Mode) =====
export type StagedChangeAction = 'add' | 'edit' | 'delete';

export interface StagedLineItemChange {
  action: StagedChangeAction;
  line_item_id?: number;  // Required for edit/delete, undefined for add
  // For adds and edits:
  item_type?: LineItemType;
  labor_id?: number;
  part_id?: number;
  misc_id?: number;
  description?: string;
  quantity?: number;
  unit_price?: number;
  is_pms?: boolean;
  pms_percent?: number;
  markup_percent?: number;  // Per-line-item markup
  base_cost?: number;  // Unit cost override
}

export interface CommitEditsRequest {
  changes: StagedLineItemChange[];
  commit_message?: string;
}

export interface CommitEditsResponse {
  success: boolean;
  message: string;
  quote: Quote;
  snapshot_version: number;
}

// ===== Quote Editor Mode (frontend-only state) =====
export type QuoteEditorMode = 'view' | 'edit' | 'invoicing';

/**
 * Represents a staged edit to an existing line item (for Edit Mode).
 * Only contains the changed fields - undefined means "unchanged".
 */
export interface StagedEdit {
  originalItem: QuoteLineItem;  // Reference to the original item for comparison
  quantity?: number;
  unit_price?: number;
  description?: string;
  markup_percent?: number;
  base_cost?: number;  // Unit cost override
}

/**
 * Represents a new line item being staged for addition (for Edit Mode).
 * Uses a temporary negative ID to identify it before commit.
 */
export interface StagedAdd {
  tempId: number;  // Negative ID to identify this staged add
  item_type: LineItemType;
  labor_id?: number;
  part_id?: number;
  misc_id?: number;
  description?: string;
  quantity: number;
  unit_price?: number;
  is_pms?: boolean;
  pms_percent?: number;
  markup_percent?: number;  // Per-item markup (from inventory or section)
  base_cost?: number;  // Base cost before markup
  // Hydrated references for display (populated when staging)
  labor?: Labor;
  part?: Part;
  miscellaneous?: Miscellaneous;
}
