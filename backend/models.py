from sqlalchemy import Column, Integer, String, Float, ForeignKey, Enum, Table, DateTime, Boolean, UniqueConstraint, Text
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from database import Base


# Association table for Part-Labor many-to-many relationship (Parts link to Labor)
part_labor_link = Table(
    'part_labor_link',
    Base.metadata,
    Column('part_id', Integer, ForeignKey('parts.id'), primary_key=True),
    Column('labor_id', Integer, ForeignKey('labor.id'), primary_key=True)
)


class ProfileType(str, enum.Enum):
    customer = "customer"
    vendor = "vendor"


class PhoneType(str, enum.Enum):
    work = "work"
    mobile = "mobile"


class POStatus(str, enum.Enum):
    draft = "Draft"
    sent = "Sent"
    received = "Received"
    closed = "Closed"


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)  # "part" or "labor"

    parts = relationship("Part", back_populates="category")
    labor_items = relationship("Labor", back_populates="category")


class Profile(Base):
    __tablename__ = "profiles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    type = Column(Enum(ProfileType), nullable=False)
    pst = Column(String, nullable=False)  # Provincial Tax Number
    address = Column(String, nullable=False)
    postal_code = Column(String, nullable=False)
    default_discount_percent = Column(Float, nullable=True)  # Default vendor discount %

    # Relationships
    contacts = relationship("Contact", back_populates="profile", cascade="all, delete-orphan")
    projects = relationship("Project", back_populates="customer")
    purchase_orders = relationship("PurchaseOrder", back_populates="vendor")


class Contact(Base):
    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True, index=True)
    profile_id = Column(Integer, ForeignKey('profiles.id', ondelete='CASCADE'), nullable=False)
    name = Column(String, nullable=False)
    job_title = Column(String, nullable=True)
    email = Column(String, nullable=True)

    # Relationships
    profile = relationship("Profile", back_populates="contacts")
    phone_numbers = relationship("ContactPhone", back_populates="contact", cascade="all, delete-orphan")


class ContactPhone(Base):
    __tablename__ = "contact_phones"

    id = Column(Integer, primary_key=True, index=True)
    contact_id = Column(Integer, ForeignKey('contacts.id', ondelete='CASCADE'), nullable=False)
    type = Column(Enum(PhoneType), nullable=False)
    number = Column(String, nullable=False)

    # Relationships
    contact = relationship("Contact", back_populates="phone_numbers")


class Part(Base):
    __tablename__ = "parts"

    id = Column(Integer, primary_key=True, index=True)
    part_number = Column(String, unique=True, nullable=False)
    description = Column(String, nullable=False)
    cost = Column(Float, nullable=False)
    markup_percent = Column(Float, default=50.0)
    category_id = Column(Integer, ForeignKey('categories.id'))
    vendor_id = Column(Integer, ForeignKey('profiles.id'), nullable=True, index=True)
    list_price = Column(Float, nullable=True)
    discount_percent = Column(Float, nullable=True)  # Per-part discount override (nullable)

    # Relationships
    category = relationship("Category", back_populates="parts")
    labor_items = relationship("Labor", secondary=part_labor_link, back_populates="parts")
    vendor = relationship("Profile")


class Labor(Base):
    __tablename__ = "labor"

    id = Column(Integer, primary_key=True, index=True)
    description = Column(String, nullable=False)
    hours = Column(Float, nullable=False, default=1)
    rate = Column(Float, nullable=False)
    markup_percent = Column(Float, default=50.0)
    category_id = Column(Integer, ForeignKey('categories.id'))

    # Relationships
    category = relationship("Category", back_populates="labor_items")
    parts = relationship("Part", secondary=part_labor_link, back_populates="labor_items")


class Miscellaneous(Base):
    __tablename__ = "miscellaneous"

    id = Column(Integer, primary_key=True, index=True)
    description = Column(String, nullable=False)
    unit_price = Column(Float, nullable=False)
    markup_percent = Column(Float, default=50.0)
    category_id = Column(Integer, ForeignKey('categories.id'))
    is_system_item = Column(Boolean, default=False)

    # Relationships
    category = relationship("Category")


class CostCode(Base):
    __tablename__ = "cost_codes"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, nullable=False)
    description = Column(String, nullable=False)


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    customer_id = Column(Integer, ForeignKey('profiles.id'), nullable=False, index=True)
    created_on = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="active")
    ucsh_project_number = Column(String, nullable=True)
    uca_project_number = Column(String, unique=True, nullable=False)
    project_lead = Column(String, nullable=True)  # Static contact name

    # Relationships
    customer = relationship("Profile", back_populates="projects")
    quotes = relationship("Quote", back_populates="project", cascade="all, delete-orphan")
    purchase_orders = relationship("PurchaseOrder", back_populates="project", cascade="all, delete-orphan")


class Quote(Base):
    __tablename__ = "quotes"
    __table_args__ = (
        UniqueConstraint('project_id', 'quote_sequence', name='uq_quote_project_sequence'),
    )

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False, index=True)
    quote_sequence = Column(Integer, nullable=False)  # Per-project sequence number (1, 2, 3...)
    created_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="Draft")  # "Draft", "Work Order", "Invoiced", "Closed" — computed by system
    current_version = Column(Integer, default=0)  # Current snapshot version
    client_po_number = Column(String, nullable=True)  # Client's PO number (required for invoicing)
    work_description = Column(String, nullable=True)  # Optional work description
    hardware_schedule_version = Column(String, nullable=True)  # Optional hardware schedule version identifier
    markup_control_enabled = Column(Boolean, default=False)  # Markup Discount Control toggle
    parts_markup_percent = Column(Float, nullable=True)  # Section-level markup for parts
    labor_markup_percent = Column(Float, nullable=True)  # Section-level markup for labor
    misc_markup_percent = Column(Float, nullable=True)  # Section-level markup for misc
    cost_code_id = Column(Integer, ForeignKey('cost_codes.id'), nullable=True)
    legacy_imported = Column(Boolean, nullable=False, server_default='false')

    # Relationships
    project = relationship("Project", back_populates="quotes")
    line_items = relationship("QuoteLineItem", back_populates="quote", cascade="all, delete-orphan")
    invoices = relationship("Invoice", back_populates="quote", order_by="Invoice.created_at")
    snapshots = relationship("QuoteSnapshot", back_populates="quote", order_by="QuoteSnapshot.version")
    cost_code = relationship("CostCode")


class QuoteLineItem(Base):
    __tablename__ = "quote_line_items"

    id = Column(Integer, primary_key=True, index=True)
    quote_id = Column(Integer, ForeignKey('quotes.id'), nullable=False, index=True)
    item_type = Column(String, nullable=False)  # "labor", "part", "misc"
    labor_id = Column(Integer, ForeignKey('labor.id'), nullable=True)
    part_id = Column(Integer, ForeignKey('parts.id'), nullable=True)
    misc_id = Column(Integer, ForeignKey('miscellaneous.id'), nullable=True)
    description = Column(String)  # For misc items or override
    quantity = Column(Integer, default=1)  # Qty Ordered (must be whole number)
    unit_price = Column(Float)  # Override price if needed
    qty_pending = Column(Integer, default=0)  # Remaining to fulfill (must be whole number)
    qty_fulfilled = Column(Integer, default=0)  # Total fulfilled across all invoices (must be whole number)
    is_pms = Column(Boolean, default=False)  # True for PMS items (Project Management Services)
    pms_percent = Column(Float, nullable=True)  # Percentage value for PMS % items (null for PMS $ or non-PMS)
    original_markup_percent = Column(Float, nullable=True)  # Individual markup before global override
    base_cost = Column(Float, nullable=True)  # Base cost used for recalculation
    markup_percent = Column(Float, nullable=True)  # Per-line-item markup (when global OFF)

    # Relationships
    quote = relationship("Quote", back_populates="line_items")
    labor = relationship("Labor")
    part = relationship("Part")
    miscellaneous = relationship("Miscellaneous")


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        UniqueConstraint('project_id', 'po_sequence', name='uq_po_project_sequence'),
    )

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False, index=True)
    vendor_id = Column(Integer, ForeignKey('profiles.id'), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    po_sequence = Column(Integer, nullable=False)
    current_version = Column(Integer, default=0)
    work_description = Column(String, nullable=True)
    vendor_po_number = Column(String, nullable=True)
    expected_delivery_date = Column(DateTime, nullable=True)
    status = Column(Enum(POStatus), default=POStatus.draft)
    cost_code_id = Column(Integer, ForeignKey('cost_codes.id'), nullable=True)
    legacy_imported = Column(Boolean, nullable=False, server_default='false')

    # Relationships
    project = relationship("Project", back_populates="purchase_orders")
    vendor = relationship("Profile", back_populates="purchase_orders")
    line_items = relationship("POLineItem", back_populates="purchase_order", cascade="all, delete-orphan")
    receivings = relationship("POReceiving", back_populates="purchase_order", order_by="POReceiving.created_at")
    snapshots = relationship("POSnapshot", back_populates="purchase_order", order_by="POSnapshot.version")
    cost_code = relationship("CostCode")


class POLineItem(Base):
    __tablename__ = "po_line_items"

    id = Column(Integer, primary_key=True, index=True)
    purchase_order_id = Column(Integer, ForeignKey('purchase_orders.id'), nullable=False, index=True)
    item_type = Column(String, nullable=False)  # "part" or "misc" (NO labor for POs)
    part_id = Column(Integer, ForeignKey('parts.id'), nullable=True)
    description = Column(String)  # For misc items or override
    quantity = Column(Integer, default=1)  # Must be whole number
    unit_price = Column(Float)
    qty_pending = Column(Integer, default=0)
    qty_received = Column(Integer, default=0)
    actual_unit_price = Column(Float, nullable=True)

    # Relationships
    purchase_order = relationship("PurchaseOrder", back_populates="line_items")
    part = relationship("Part")
    receiving_line_items = relationship("POReceivingLineItem", back_populates="po_line_item")


class POReceiving(Base):
    __tablename__ = "po_receivings"

    id = Column(Integer, primary_key=True, index=True)
    purchase_order_id = Column(Integer, ForeignKey('purchase_orders.id'), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    received_date = Column(DateTime, nullable=False)
    notes = Column(String, nullable=True)
    voided_at = Column(DateTime, nullable=True)
    voided_by_snapshot_id = Column(Integer, nullable=True)

    # Relationships
    purchase_order = relationship("PurchaseOrder", back_populates="receivings")
    line_items = relationship("POReceivingLineItem", back_populates="receiving", cascade="all, delete-orphan")


class POReceivingLineItem(Base):
    __tablename__ = "po_receiving_line_items"

    id = Column(Integer, primary_key=True, index=True)
    receiving_id = Column(Integer, ForeignKey('po_receivings.id'), nullable=False)
    po_line_item_id = Column(Integer, ForeignKey('po_line_items.id'), nullable=True)
    item_type = Column(String, nullable=False)
    description = Column(String, nullable=True)
    unit_price = Column(Float, nullable=True)
    actual_unit_price = Column(Float, nullable=True)
    qty_ordered = Column(Integer, nullable=True)
    qty_received_this_receiving = Column(Integer, nullable=True)
    qty_received_total = Column(Integer, nullable=True)
    qty_pending_after = Column(Integer, nullable=True)
    part_id = Column(Integer, nullable=True)

    # Relationships
    receiving = relationship("POReceiving", back_populates="line_items")
    po_line_item = relationship("POLineItem", back_populates="receiving_line_items")


class POSnapshot(Base):
    __tablename__ = "po_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    purchase_order_id = Column(Integer, ForeignKey('purchase_orders.id'), nullable=False)
    version = Column(Integer, nullable=False)
    action_type = Column(String, nullable=False)  # "create", "edit", "delete", "receive", "status_change", "date_edit", "revert"
    action_description = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    receiving_id = Column(Integer, ForeignKey('po_receivings.id'), nullable=True)
    actor_user_id = Column(String, nullable=True)  # Clerk user id (sub) who performed the action
    actor_email = Column(String, nullable=True)  # Resolved email, for display

    # Relationships
    purchase_order = relationship("PurchaseOrder", back_populates="snapshots")
    line_item_states = relationship("POLineItemSnapshot", back_populates="snapshot", cascade="all, delete-orphan")


class POLineItemSnapshot(Base):
    __tablename__ = "po_line_item_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    snapshot_id = Column(Integer, ForeignKey('po_snapshots.id'), nullable=False)
    original_line_item_id = Column(Integer, nullable=True)
    item_type = Column(String, nullable=False)
    part_id = Column(Integer, nullable=True)
    description = Column(String, nullable=True)
    quantity = Column(Integer, nullable=True)
    unit_price = Column(Float, nullable=True)
    qty_pending = Column(Integer, nullable=True)
    qty_received = Column(Integer, nullable=True)
    actual_unit_price = Column(Float, nullable=True)
    is_deleted = Column(Boolean, default=False)

    # Relationships
    snapshot = relationship("POSnapshot", back_populates="line_item_states")


# ===== Invoice Models =====

class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    quote_id = Column(Integer, ForeignKey('quotes.id'), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="Sent")  # "Sent", "Paid", "Voided"
    notes = Column(String)  # Optional notes for this invoice
    invoice_sequence = Column(Integer, default=1)  # Per-quote sequence (1, 2, 3...)
    quote_version = Column(Integer, default=0)  # Quote version captured at invoice time
    current_version = Column(Integer, default=0, server_default='0')  # Invoice's own snapshot version (audit trail)
    voided_at = Column(DateTime)  # When voided (if applicable)
    voided_by_snapshot_id = Column(Integer)  # Which revert voided this

    # Relationships
    quote = relationship("Quote", back_populates="invoices")
    line_items = relationship("InvoiceLineItem", back_populates="invoice", cascade="all, delete-orphan")
    snapshots = relationship("InvoiceSnapshot", back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceSnapshot.version")


class InvoiceLineItem(Base):
    __tablename__ = "invoice_line_items"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey('invoices.id'), nullable=False)
    quote_line_item_id = Column(Integer)  # Reference to original line item

    # Snapshot of line item at invoice time
    item_type = Column(String, nullable=False)
    description = Column(String)
    unit_price = Column(Float)
    qty_ordered = Column(Integer)  # Original ordered quantity (must be whole number)
    qty_fulfilled_this_invoice = Column(Integer)  # Amount fulfilled in THIS invoice (must be whole number)
    qty_fulfilled_total = Column(Integer)  # Total fulfilled up to this point (must be whole number)
    qty_pending_after = Column(Integer)  # Pending after this invoice (must be whole number)

    # Foreign keys for reference data
    labor_id = Column(Integer)
    part_id = Column(Integer)
    misc_id = Column(Integer)

    # Relationships
    invoice = relationship("Invoice", back_populates="line_items")


# ===== Invoice Snapshot Models (for versioning/revert) =====

class InvoiceSnapshot(Base):
    __tablename__ = "invoice_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey('invoices.id'), nullable=False)
    version = Column(Integer, nullable=False)
    action_type = Column(String, nullable=False)  # "date_edit", "revert"
    action_description = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)  # When this snapshot was taken
    entity_created_at = Column(DateTime, nullable=True)  # The invoice's created_at at snapshot time (for revert)
    actor_user_id = Column(String, nullable=True)  # Clerk user id (sub) who performed the action
    actor_email = Column(String, nullable=True)  # Resolved email, for display

    # Relationships
    invoice = relationship("Invoice", back_populates="snapshots")
    line_item_states = relationship("InvoiceLineItemSnapshot", back_populates="snapshot", cascade="all, delete-orphan")


class InvoiceLineItemSnapshot(Base):
    __tablename__ = "invoice_line_item_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    snapshot_id = Column(Integer, ForeignKey('invoice_snapshots.id'), nullable=False)
    original_line_item_id = Column(Integer, nullable=True)  # Reference to original invoice line item
    quote_line_item_id = Column(Integer, nullable=True)  # The invoice line's quote_line_item_id

    # Full state of the invoice line at this snapshot
    item_type = Column(String, nullable=False)
    description = Column(String, nullable=True)
    unit_price = Column(Float, nullable=True)
    qty_ordered = Column(Integer, nullable=True)
    qty_fulfilled_this_invoice = Column(Integer, nullable=True)
    qty_fulfilled_total = Column(Integer, nullable=True)
    qty_pending_after = Column(Integer, nullable=True)
    labor_id = Column(Integer, nullable=True)
    part_id = Column(Integer, nullable=True)
    misc_id = Column(Integer, nullable=True)

    # Relationships
    snapshot = relationship("InvoiceSnapshot", back_populates="line_item_states")


# ===== Quote Snapshot Models (for versioning/revert) =====

class QuoteSnapshot(Base):
    __tablename__ = "quote_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    quote_id = Column(Integer, ForeignKey('quotes.id'), nullable=False)
    version = Column(Integer, nullable=False)
    action_type = Column(String, nullable=False)  # "create", "edit", "delete", "invoice", "date_edit", "revert"
    action_description = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    invoice_id = Column(Integer)  # If action_type="invoice", link to Invoice
    actor_user_id = Column(String, nullable=True)  # Clerk user id (sub) who performed the action
    actor_email = Column(String, nullable=True)  # Resolved email, for display

    # Relationships
    quote = relationship("Quote", back_populates="snapshots")
    line_item_states = relationship("QuoteLineItemSnapshot", back_populates="snapshot", cascade="all, delete-orphan")


class SystemRate(Base):
    __tablename__ = "system_rates"

    id = Column(Integer, primary_key=True, index=True)
    rate_type = Column(String, nullable=False)  # "parking" or "travel_distance"
    description = Column(String, nullable=False)
    unit_price = Column(Float, nullable=False)
    markup_percent = Column(Float, nullable=False, default=0.0)
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    linked_misc_id = Column(Integer, ForeignKey('miscellaneous.id'), nullable=True)

    # Relationships
    linked_misc = relationship("Miscellaneous")


class CompanySettings(Base):
    __tablename__ = "company_settings"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    address = Column(String)
    phone = Column(String)
    fax = Column(String)
    gst_number = Column(String)
    hst_rate = Column(Float, default=13.0)
    default_pms_percent = Column(Float, nullable=True)
    logo_data_url = Column(Text, nullable=True)


class QuoteLineItemSnapshot(Base):
    __tablename__ = "quote_line_item_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    snapshot_id = Column(Integer, ForeignKey('quote_snapshots.id'), nullable=False)
    original_line_item_id = Column(Integer)  # Reference to original line item (may be deleted)

    # Full state at this snapshot
    item_type = Column(String, nullable=False)
    labor_id = Column(Integer)
    part_id = Column(Integer)
    misc_id = Column(Integer)
    description = Column(String)
    quantity = Column(Integer)  # qty_ordered (must be whole number)
    unit_price = Column(Float)
    qty_pending = Column(Integer)  # Must be whole number
    qty_fulfilled = Column(Integer)  # Must be whole number
    is_deleted = Column(Boolean, default=False)  # Track if item was deleted at this snapshot
    is_pms = Column(Boolean, default=False)  # True for PMS items (Project Management Services)
    pms_percent = Column(Float, nullable=True)  # Percentage value for PMS % items
    original_markup_percent = Column(Float, nullable=True)  # Individual markup before global override
    base_cost = Column(Float, nullable=True)  # Base cost used for recalculation
    markup_percent = Column(Float, nullable=True)  # Per-line-item markup

    # Relationships
    snapshot = relationship("QuoteSnapshot", back_populates="line_item_states")
