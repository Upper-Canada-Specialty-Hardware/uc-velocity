from pydantic import BaseModel, validator
from typing import Generic, List, Optional, TypeVar
from datetime import datetime
from enum import Enum


# ===== Generic Pagination Envelope =====
T = TypeVar('T')


class Paginated(BaseModel, Generic[T]):
    """Standard response shape for paginated list endpoints.

    `total` is the unfiltered total matching the query (independent of offset/limit),
    so clients can render "X of Y" without an extra count round trip.
    """
    items: List[T]
    total: int
    limit: int
    offset: int


class ProfileType(str, Enum):
    customer = "customer"
    vendor = "vendor"


# ===== Company Settings Schemas =====
class CompanySettingsBase(BaseModel):
    name: str
    address: Optional[str] = None
    phone: Optional[str] = None
    fax: Optional[str] = None
    gst_number: Optional[str] = None
    hst_rate: float = 13.0
    default_pms_percent: Optional[float] = None
    logo_data_url: Optional[str] = None


class CompanySettingsUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    fax: Optional[str] = None
    gst_number: Optional[str] = None
    hst_rate: Optional[float] = None
    default_pms_percent: Optional[float] = None
    logo_data_url: Optional[str] = None


class CompanySettings(CompanySettingsBase):
    id: int

    class Config:
        from_attributes = True


# ===== System Rate Schemas =====
class SystemRateBase(BaseModel):
    rate_type: str
    description: str
    unit_price: float
    markup_percent: float = 0.0
    sort_order: int = 0
    is_active: bool = True


class SystemRateCreate(BaseModel):
    description: str
    unit_price: float
    markup_percent: float = 0.0
    sort_order: int = 0


class SystemRateUpdate(BaseModel):
    description: Optional[str] = None
    unit_price: Optional[float] = None
    markup_percent: Optional[float] = None
    sort_order: Optional[int] = None


class SystemRate(SystemRateBase):
    id: int
    linked_misc_id: Optional[int] = None

    class Config:
        from_attributes = True


# ===== Invoice Summary (for Reports) =====
class InvoiceSummaryItem(BaseModel):
    invoice_id: int
    invoice_date: datetime
    uca_project_number: str
    project_name: str
    customer_name: str
    client_po_number: Optional[str] = None
    net_sales: float
    hst_amount: float
    grand_total: float


# ===== Backlog Quotes Report =====
class BacklogLineItem(BaseModel):
    line_item_id: int
    item_type: str
    description: str
    quantity: int
    qty_fulfilled: int
    qty_pending: int
    unit_price: float
    backlog_value: float


class BacklogQuoteItem(BaseModel):
    quote_id: int
    quote_number: str
    uca_project_number: str
    customer_name: str
    project_name: str
    client_po_number: Optional[str] = None
    status: str
    quote_total: float
    backlog_total: float
    line_items: List[BacklogLineItem] = []


class PhoneType(str, Enum):
    work = "work"
    mobile = "mobile"


class POStatus(str, Enum):
    draft = "Draft"
    sent = "Sent"
    received = "Received"
    closed = "Closed"


# ===== Cost Code Schemas =====
class CostCodeBase(BaseModel):
    code: str
    description: str
    gp_cost_code_properties: Optional[str] = None
    uch_dept_properties: Optional[str] = None


class CostCodeCreate(CostCodeBase):
    pass


class CostCodeUpdate(BaseModel):
    code: Optional[str] = None
    description: Optional[str] = None
    gp_cost_code_properties: Optional[str] = None
    uch_dept_properties: Optional[str] = None


class CostCode(CostCodeBase):
    id: int

    class Config:
        from_attributes = True


# ===== Category Schemas =====
class CategoryBase(BaseModel):
    name: str
    type: str  # "part" or "labor"


class CategoryCreate(CategoryBase):
    pass


class Category(CategoryBase):
    id: int

    class Config:
        from_attributes = True


# ===== Part Schemas =====
class PartBase(BaseModel):
    part_number: str
    description: str
    cost: float
    markup_percent: float = 0.0
    category_id: Optional[int] = None
    vendor_id: Optional[int] = None
    list_price: Optional[float] = None
    discount_percent: Optional[float] = None  # Per-part discount override


class PartCreate(PartBase):
    linked_labor_ids: List[int] = []  # IDs of labor items to link


class PartUpdate(BaseModel):
    part_number: Optional[str] = None
    description: Optional[str] = None
    cost: Optional[float] = None
    markup_percent: Optional[float] = None
    category_id: Optional[int] = None
    linked_labor_ids: Optional[List[int]] = None
    vendor_id: Optional[int] = None
    list_price: Optional[float] = None
    discount_percent: Optional[float] = None


class Part(PartBase):
    id: int

    class Config:
        from_attributes = True


# ===== Labor Schemas =====
class LaborBase(BaseModel):
    description: str
    hours: float = 1
    rate: float
    markup_percent: float = 0.0
    category_id: Optional[int] = None

    @validator('hours', pre=True)
    def hours_must_be_positive(cls, v) -> float:
        v_float = float(v)
        if v_float <= 0:
            raise ValueError('Hours must be a positive number')
        # Enforce max 2 decimal places
        if round(v_float, 2) != v_float:
            raise ValueError('Hours cannot have more than 2 decimal places')
        return round(v_float, 2)


class LaborCreate(LaborBase):
    pass


class LaborUpdate(BaseModel):
    description: Optional[str] = None
    hours: Optional[float] = None
    rate: Optional[float] = None
    markup_percent: Optional[float] = None
    category_id: Optional[int] = None

    @validator('hours', pre=True)
    def hours_must_be_positive(cls, v) -> Optional[float]:
        if v is None:
            return None
        v_float = float(v)
        if v_float <= 0:
            raise ValueError('Hours must be a positive number')
        # Enforce max 2 decimal places
        if round(v_float, 2) != v_float:
            raise ValueError('Hours cannot have more than 2 decimal places')
        return round(v_float, 2)


class Labor(LaborBase):
    id: int

    class Config:
        from_attributes = True


# ===== Miscellaneous Schemas =====
class MiscellaneousBase(BaseModel):
    description: str
    unit_price: float
    markup_percent: float = 0.0
    category_id: Optional[int] = None


class MiscellaneousCreate(MiscellaneousBase):
    pass


class MiscellaneousUpdate(BaseModel):
    description: Optional[str] = None
    unit_price: Optional[float] = None
    markup_percent: Optional[float] = None
    category_id: Optional[int] = None


class Miscellaneous(MiscellaneousBase):
    id: int
    is_system_item: bool = False

    class Config:
        from_attributes = True


# Part with nested labor items for read operations
class PartWithLabor(Part):
    labor_items: List[Labor] = []

    class Config:
        from_attributes = True


# ===== ContactPhone Schemas =====
class ContactPhoneBase(BaseModel):
    type: PhoneType
    number: str


class ContactPhoneCreate(ContactPhoneBase):
    pass


class ContactPhone(ContactPhoneBase):
    id: int
    contact_id: int

    class Config:
        from_attributes = True


# ===== Contact Schemas =====
class ContactBase(BaseModel):
    name: str
    job_title: Optional[str] = None
    email: Optional[str] = None


class ContactCreate(ContactBase):
    phone_numbers: List[ContactPhoneCreate] = []


class ContactUpdate(BaseModel):
    name: Optional[str] = None
    job_title: Optional[str] = None
    email: Optional[str] = None
    phone_numbers: Optional[List[ContactPhoneCreate]] = None


class Contact(ContactBase):
    id: int
    profile_id: int
    phone_numbers: List[ContactPhone] = []

    class Config:
        from_attributes = True


# ===== Profile Schemas =====
class ProfileBase(BaseModel):
    name: str
    type: ProfileType
    pst: str
    address: str
    postal_code: str
    website: Optional[str] = None
    default_discount_percent: Optional[float] = None


class ProfileCreate(ProfileBase):
    contacts: List[ContactCreate]

    @validator('contacts')
    def validate_at_least_one_contact(cls, v: List[ContactCreate]) -> List[ContactCreate]:
        if not v or len(v) == 0:
            raise ValueError('At least one contact is required')
        return v


class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[ProfileType] = None
    pst: Optional[str] = None
    address: Optional[str] = None
    postal_code: Optional[str] = None
    website: Optional[str] = None
    default_discount_percent: Optional[float] = None


class Profile(ProfileBase):
    id: int
    contacts: List[Contact] = []

    class Config:
        from_attributes = True


# ===== Project Schemas =====
class ProjectBase(BaseModel):
    name: str
    customer_id: int
    status: str = "active"
    ucsh_project_number: Optional[str] = None
    project_lead: Optional[str] = None


class ProjectCreate(ProjectBase):
    pass


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    customer_id: Optional[int] = None
    status: Optional[str] = None
    ucsh_project_number: Optional[str] = None
    project_lead: Optional[str] = None


class Project(ProjectBase):
    id: int
    created_on: datetime
    uca_project_number: str
    customer: Profile

    class Config:
        from_attributes = True


# ===== Quote Line Item Schemas =====
class QuoteLineItemBase(BaseModel):
    item_type: str  # "labor", "part", "misc"
    labor_id: Optional[int] = None
    part_id: Optional[int] = None
    misc_id: Optional[int] = None
    description: Optional[str] = None
    quantity: int = 1  # Must be a positive whole number
    unit_price: Optional[float] = None
    is_pms: bool = False  # True for PMS items (Project Management Services)
    pms_percent: Optional[float] = None  # Percentage value for PMS % items
    original_markup_percent: Optional[float] = None  # Individual markup before global override
    base_cost: Optional[float] = None  # Base cost used for recalculation
    markup_percent: Optional[float] = None  # Per-line-item markup (when global OFF)

    @validator('quantity', pre=True)
    def quantity_must_be_positive_integer(cls, v) -> int:
        # Check if value is a whole number before coercion
        if isinstance(v, float) and not v.is_integer():
            raise ValueError('Quantity must be a positive whole number')
        v_int = int(v)
        if v_int <= 0:
            raise ValueError('Quantity must be a positive whole number')
        return v_int


class QuoteLineItemCreate(QuoteLineItemBase):
    pass


class QuoteLineItemUpdate(BaseModel):
    quantity: Optional[int] = None  # Must be a positive whole number
    unit_price: Optional[float] = None

    @validator('quantity', pre=True)
    def quantity_must_be_positive_integer(cls, v) -> Optional[int]:
        if v is None:
            return None
        # Check if value is a whole number before coercion
        if isinstance(v, float) and not v.is_integer():
            raise ValueError('Quantity must be a positive whole number')
        v_int = int(v)
        if v_int <= 0:
            raise ValueError('Quantity must be a positive whole number')
        return v_int


class QuoteLineItem(QuoteLineItemBase):
    id: int
    quote_id: int
    qty_pending: int = 0  # Must be whole number
    qty_fulfilled: int = 0  # Must be whole number
    labor: Optional[Labor] = None
    part: Optional[Part] = None
    miscellaneous: Optional[Miscellaneous] = None

    class Config:
        from_attributes = True


# ===== Quote Schemas =====
class QuoteBase(BaseModel):
    project_id: int
    status: str = "Draft"  # "Draft", "Work Order", "Invoiced", "Closed" — computed by system
    client_po_number: Optional[str] = None
    work_description: Optional[str] = None
    markup_control_enabled: bool = False  # Markup Discount Control toggle
    parts_markup_percent: Optional[float] = None  # Section-level markup for parts
    labor_markup_percent: Optional[float] = None  # Section-level markup for labor
    misc_markup_percent: Optional[float] = None  # Section-level markup for misc


class QuoteCreate(BaseModel):
    project_id: int
    client_po_number: Optional[str] = None
    work_description: Optional[str] = None
    cost_code_id: Optional[int] = None


class QuoteUpdate(BaseModel):
    client_po_number: Optional[str] = None
    work_description: Optional[str] = None
    cost_code_id: Optional[int] = None


class Quote(QuoteBase):
    id: int
    quote_sequence: int  # Per-project sequence number (1, 2, 3...)
    quote_number: Optional[str] = None  # Computed: "{UCA Project Number}-{Sequence:04d}-{Version}"
    created_at: datetime
    current_version: int = 0
    cost_code_id: Optional[int] = None
    cost_code: Optional[CostCode] = None
    line_items: List[QuoteLineItem] = []

    class Config:
        from_attributes = True


# ===== PO Line Item Schemas =====
class POLineItemBase(BaseModel):
    item_type: str  # "part" or "misc" (NO labor for POs)
    part_id: Optional[int] = None
    description: Optional[str] = None
    quantity: int = 1  # Must be a positive whole number
    unit_price: Optional[float] = None

    @validator('quantity', pre=True)
    def quantity_must_be_positive_integer(cls, v) -> int:
        # Check if value is a whole number before coercion
        if isinstance(v, float) and not v.is_integer():
            raise ValueError('Quantity must be a positive whole number')
        v_int = int(v)
        if v_int <= 0:
            raise ValueError('Quantity must be a positive whole number')
        return v_int


class POLineItemCreate(POLineItemBase):
    pass


class POLineItem(POLineItemBase):
    id: int
    purchase_order_id: int
    qty_pending: int = 0
    qty_received: int = 0
    actual_unit_price: Optional[float] = None
    part: Optional[Part] = None

    class Config:
        from_attributes = True


# ===== Purchase Order Schemas =====
class PurchaseOrderBase(BaseModel):
    project_id: int
    vendor_id: int
    status: POStatus = POStatus.draft
    work_description: Optional[str] = None
    vendor_po_number: Optional[str] = None
    expected_delivery_date: Optional[datetime] = None


class PurchaseOrderCreate(PurchaseOrderBase):
    cost_code_id: Optional[int] = None


class PurchaseOrderUpdate(BaseModel):
    status: Optional[POStatus] = None
    work_description: Optional[str] = None
    vendor_po_number: Optional[str] = None
    expected_delivery_date: Optional[datetime] = None
    cost_code_id: Optional[int] = None


class PurchaseOrder(PurchaseOrderBase):
    id: int
    created_at: datetime
    po_sequence: int
    current_version: int = 0
    po_number: Optional[str] = None
    cost_code_id: Optional[int] = None
    cost_code: Optional[CostCode] = None
    vendor: Profile
    line_items: List[POLineItem] = []

    class Config:
        from_attributes = True


# ===== PO Receiving Line Item Schemas =====
class POReceivingLineItemBase(BaseModel):
    po_line_item_id: Optional[int] = None
    item_type: str
    description: Optional[str] = None
    unit_price: Optional[float] = None
    actual_unit_price: Optional[float] = None
    qty_ordered: Optional[int] = None
    qty_received_this_receiving: Optional[int] = None
    qty_received_total: Optional[int] = None
    qty_pending_after: Optional[int] = None
    part_id: Optional[int] = None

    @validator('qty_ordered', 'qty_received_this_receiving', 'qty_received_total', 'qty_pending_after', pre=True)
    def quantities_must_be_whole_numbers(cls, v) -> Optional[int]:
        if v is None:
            return None
        if isinstance(v, float) and not v.is_integer():
            raise ValueError('Must be a positive whole number')
        v_int = int(v)
        if v_int < 0:
            raise ValueError('Must be a positive whole number')
        return v_int


class POReceivingLineItem(POReceivingLineItemBase):
    id: int
    receiving_id: int

    class Config:
        from_attributes = True


# ===== PO Receiving Schemas =====
class POReceivingLineItemCreate(BaseModel):
    po_line_item_id: int
    qty_received: int
    actual_unit_price: Optional[float] = None

    @validator('qty_received', pre=True)
    def qty_received_must_be_positive_integer(cls, v) -> int:
        if isinstance(v, float) and not v.is_integer():
            raise ValueError('Quantity received must be a positive whole number')
        v_int = int(v)
        if v_int <= 0:
            raise ValueError('Quantity received must be a positive whole number')
        return v_int


class POReceivingBase(BaseModel):
    received_date: datetime
    notes: Optional[str] = None


class POReceivingCreate(BaseModel):
    received_date: datetime
    notes: Optional[str] = None
    line_items: List[POReceivingLineItemCreate]


class POReceiving(POReceivingBase):
    id: int
    purchase_order_id: int
    created_at: datetime
    voided_at: Optional[datetime] = None
    voided_by_snapshot_id: Optional[int] = None
    line_items: List[POReceivingLineItem] = []

    class Config:
        from_attributes = True


# ===== PO Line Item Snapshot Schemas =====
class POLineItemSnapshotBase(BaseModel):
    original_line_item_id: Optional[int] = None
    item_type: str
    part_id: Optional[int] = None
    description: Optional[str] = None
    quantity: int
    unit_price: Optional[float] = None
    qty_pending: int
    qty_received: int
    actual_unit_price: Optional[float] = None
    is_deleted: bool = False

    @validator('quantity', 'qty_pending', 'qty_received', pre=True)
    def quantities_must_be_whole_numbers(cls, v) -> int:
        if isinstance(v, float) and not v.is_integer():
            raise ValueError('Must be a positive whole number')
        return int(v)


class POLineItemSnapshot(POLineItemSnapshotBase):
    id: int
    snapshot_id: int

    class Config:
        from_attributes = True


# ===== PO Snapshot Schemas =====
class POSnapshotBase(BaseModel):
    purchase_order_id: int
    version: int
    action_type: str  # "create", "edit", "delete", "receive", "status_change", "revert"
    action_description: Optional[str] = None
    receiving_id: Optional[int] = None


class POSnapshot(POSnapshotBase):
    id: int
    created_at: datetime
    line_item_states: List[POLineItemSnapshot] = []

    class Config:
        from_attributes = True


# ===== PO Revert Preview Schema =====
class PORevertPreview(BaseModel):
    target_version: int
    receivings_to_void: List[POReceiving] = []
    changes_summary: str


# ===== PO Commit Edits Schemas =====
class StagedPOLineItemChange(BaseModel):
    action: str  # "add", "edit", or "delete"
    line_item_id: Optional[int] = None  # Required for edit/delete
    item_type: Optional[str] = None  # "part" or "misc"
    part_id: Optional[int] = None
    description: Optional[str] = None
    quantity: Optional[int] = None
    unit_price: Optional[float] = None

    @validator('quantity', pre=True)
    def quantity_must_be_positive_integer(cls, v) -> Optional[int]:
        if v is None:
            return None
        if isinstance(v, float) and not v.is_integer():
            raise ValueError('Quantity must be a positive whole number')
        v_int = int(v)
        if v_int <= 0:
            raise ValueError('Quantity must be a positive whole number')
        return v_int


class POCommitEditsRequest(BaseModel):
    changes: List[StagedPOLineItemChange]
    commit_message: Optional[str] = None


class POCommitEditsResponse(BaseModel):
    success: bool
    message: str
    purchase_order: PurchaseOrder
    snapshot_version: int


# ===== Project-detail summary schemas =====
# Lighter shapes used by ProjectFull so the project detail screen does not
# hydrate every quote's and PO's line items. Open a specific quote/PO via
# `GET /quotes/{id}` or `GET /purchase-orders/{id}` to get its line items.
class QuoteSummary(QuoteBase):
    id: int
    quote_sequence: int
    quote_number: Optional[str] = None
    created_at: datetime
    current_version: int = 0
    cost_code_id: Optional[int] = None

    class Config:
        from_attributes = True


class POSummary(PurchaseOrderBase):
    id: int
    created_at: datetime
    po_sequence: int
    current_version: int = 0
    po_number: Optional[str] = None
    cost_code_id: Optional[int] = None
    vendor: Profile

    class Config:
        from_attributes = True


# ===== Project with nested documents (no line items) =====
class ProjectFull(Project):
    quotes: List[QuoteSummary] = []
    purchase_orders: List[POSummary] = []

    class Config:
        from_attributes = True


# ===== Project List View (lightweight, aggregated) =====
class ProjectListView(BaseModel):
    id: int
    name: str
    uca_project_number: str
    ucsh_project_number: Optional[str] = None
    customer_id: int
    customer_name: str
    project_lead: Optional[str] = None
    status: str
    created_on: datetime
    quote_count: int = 0
    po_count: int = 0
    latest_quote_number: Optional[str] = None
    latest_po_number: Optional[str] = None


class MatchedQuoteRef(BaseModel):
    id: int
    quote_number: str


class MatchedPORef(BaseModel):
    id: int
    po_number: str
    vendor_name: str


class ProjectSearchResult(ProjectListView):
    matched_quotes: List[MatchedQuoteRef] = []
    matched_pos: List[MatchedPORef] = []


# ===== Invoice Line Item Schemas =====
class InvoiceLineItemBase(BaseModel):
    quote_line_item_id: Optional[int] = None
    item_type: str
    description: Optional[str] = None
    unit_price: Optional[float] = None
    qty_ordered: int  # Must be whole number
    qty_fulfilled_this_invoice: int  # Must be whole number
    qty_fulfilled_total: int  # Must be whole number
    qty_pending_after: int  # Must be whole number
    labor_id: Optional[int] = None
    part_id: Optional[int] = None
    misc_id: Optional[int] = None

    @validator('qty_ordered', 'qty_fulfilled_this_invoice', 'qty_fulfilled_total', 'qty_pending_after', pre=True)
    def quantities_must_be_whole_numbers(cls, v) -> int:
        # Check if value is a whole number before coercion
        if isinstance(v, float) and not v.is_integer():
            raise ValueError('Must be a positive whole number')
        return int(v)


class InvoiceLineItem(InvoiceLineItemBase):
    id: int
    invoice_id: int

    class Config:
        from_attributes = True


# ===== Invoice Schemas =====
class InvoiceBase(BaseModel):
    quote_id: int
    notes: Optional[str] = None


class LineItemFulfillment(BaseModel):
    line_item_id: int
    quantity: int  # Amount to fulfill - must be a positive whole number

    @validator('quantity', pre=True)
    def quantity_must_be_positive_integer(cls, v) -> int:
        # Check if value is a whole number before coercion
        if isinstance(v, float) and not v.is_integer():
            raise ValueError('Must be a positive whole number')
        v_int = int(v)
        if v_int <= 0:
            raise ValueError('Must be a positive whole number')
        return v_int


class InvoiceCreate(BaseModel):
    fulfillments: List[LineItemFulfillment]
    notes: Optional[str] = None


class InvoiceStatusUpdate(BaseModel):
    status: str  # "Sent", "Paid"


class Invoice(InvoiceBase):
    id: int
    created_at: datetime
    status: str
    voided_at: Optional[datetime] = None
    voided_by_snapshot_id: Optional[int] = None
    line_items: List[InvoiceLineItem] = []

    class Config:
        from_attributes = True


# ===== Quote Line Item Snapshot Schemas =====
class QuoteLineItemSnapshotBase(BaseModel):
    original_line_item_id: Optional[int] = None
    item_type: str
    labor_id: Optional[int] = None
    part_id: Optional[int] = None
    misc_id: Optional[int] = None
    description: Optional[str] = None
    quantity: int  # Must be whole number
    unit_price: Optional[float] = None
    qty_pending: int  # Must be whole number
    qty_fulfilled: int  # Must be whole number
    is_deleted: bool = False
    is_pms: bool = False  # True for PMS items (Project Management Services)
    pms_percent: Optional[float] = None  # Percentage value for PMS % items
    original_markup_percent: Optional[float] = None  # Individual markup before global override
    base_cost: Optional[float] = None  # Base cost used for recalculation
    markup_percent: Optional[float] = None  # Per-line-item markup

    @validator('quantity', 'qty_pending', 'qty_fulfilled', pre=True)
    def quantities_must_be_whole_numbers(cls, v) -> int:
        # Check if value is a whole number before coercion
        if isinstance(v, float) and not v.is_integer():
            raise ValueError('Must be a positive whole number')
        return int(v)


class QuoteLineItemSnapshot(QuoteLineItemSnapshotBase):
    id: int
    snapshot_id: int

    class Config:
        from_attributes = True


# ===== Quote Snapshot Schemas =====
class QuoteSnapshotBase(BaseModel):
    quote_id: int
    version: int
    action_type: str  # "create", "edit", "delete", "invoice", "revert"
    action_description: Optional[str] = None
    invoice_id: Optional[int] = None


class QuoteSnapshot(QuoteSnapshotBase):
    id: int
    created_at: datetime
    line_item_states: List[QuoteLineItemSnapshot] = []

    class Config:
        from_attributes = True


# ===== Revert Preview Schema =====
class RevertPreview(BaseModel):
    target_version: int
    invoices_to_void: List[Invoice] = []
    changes_summary: str


# ===== Markup Control Toggle Schemas =====
class MarkupControlToggleRequest(BaseModel):
    enabled: bool
    parts_markup_percent: Optional[float] = None  # Required when enabled=True
    labor_markup_percent: Optional[float] = None
    misc_markup_percent: Optional[float] = None


class MarkupControlToggleResponse(BaseModel):
    success: bool
    message: str
    quote: Quote


# ===== Commit Edits Schemas (Edit Mode) =====
class StagedLineItemChange(BaseModel):
    """Represents a single staged change to a line item."""
    action: str  # "add", "edit", or "delete"
    line_item_id: Optional[int] = None  # Required for edit/delete, None for add
    # For adds and edits:
    item_type: Optional[str] = None  # "labor", "part", "misc"
    labor_id: Optional[int] = None
    part_id: Optional[int] = None
    misc_id: Optional[int] = None
    description: Optional[str] = None
    quantity: Optional[int] = None  # Must be a positive whole number
    unit_price: Optional[float] = None
    is_pms: bool = False
    pms_percent: Optional[float] = None
    markup_percent: Optional[float] = None  # Per-line-item markup
    base_cost: Optional[float] = None  # Unit cost override

    @validator('quantity', pre=True)
    def quantity_must_be_positive_integer(cls, v) -> Optional[int]:
        if v is None:
            return None
        # Check if value is a whole number before coercion
        if isinstance(v, float) and not v.is_integer():
            raise ValueError('Quantity must be a positive whole number')
        v_int = int(v)
        if v_int <= 0:
            raise ValueError('Quantity must be a positive whole number')
        return v_int


class CommitEditsRequest(BaseModel):
    """Request body for committing staged edits to a quote."""
    changes: List[StagedLineItemChange]
    commit_message: Optional[str] = None  # Optional description for audit trail


class CommitEditsResponse(BaseModel):
    """Response from committing edits."""
    success: bool
    message: str
    quote: Quote
    snapshot_version: int


# ===== Vendor Pricebook Import Schemas =====
class PricebookImportResult(BaseModel):
    created: int
    updated: int
    errors: List[str] = []
