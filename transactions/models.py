from decimal import Decimal
from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class ContactType(models.TextChoices):
    VENDOR = 'Vendor', _('Vendor')
    CLIENT = 'Client', _('Client')


class Contact(models.Model):
    """
    Represents external business entities: Vendors (suppliers) and Clients (customers).
    Includes tax and registration details (GST).
    """
    type = models.CharField(
        max_length=20,
        choices=ContactType.choices,
        db_index=True,
        help_text=_("Designates whether this contact is a supplier (Vendor) or a customer (Client).")
    )
    company_name = models.CharField(
        max_length=255,
        db_index=True,
        help_text=_("Registered legal entity or trading company name.")
    )
    contact_person = models.CharField(
        max_length=150,
        blank=True,
        help_text=_("Primary point of contact.")
    )
    email = models.EmailField(
        blank=True,
        help_text=_("Primary business correspondence email.")
    )
    phone = models.CharField(
        max_length=30,
        blank=True,
        help_text=_("Business telephone or mobile number.")
    )
    GST_number = models.CharField(
        max_length=20,
        blank=True,
        help_text=_("Goods and Services Tax Identification Number (GSTIN) / Tax ID.")
    )
    address = models.TextField(
        blank=True,
        help_text=_("Registered billing or dispatch address.")
    )
    is_active = models.BooleanField(
        default=True,
        help_text=_("Designates whether this contact is active for commercial orders.")
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['company_name']
        verbose_name = _('Contact')
        verbose_name_plural = _('Contacts')
        indexes = [
            models.Index(fields=['type', 'company_name']),
            models.Index(fields=['GST_number']),
        ]

    def clean(self):
        super().clean()
        if self.GST_number:
            self.GST_number = self.GST_number.strip().upper()

    def __str__(self) -> str:
        return f"{self.company_name} ({self.get_type_display()})"


class TransactionType(models.TextChoices):
    IN = 'In', _('In (Procurement / Inward Receipt)')
    OUT = 'Out', _('Out (Dispatch / Sales Fulfillment)')
    MANUFACTURED = 'Manufactured', _('Manufactured (Production Inward / WIP Conversion)')


class InventoryLedgerQuerySet(models.QuerySet):
    """
    Guarantees ledger immutability even across bulk operations.
    """
    def delete(self):
        raise ValidationError(_("Audit Preservation Violation: Bulk deletion of InventoryLedger records is prohibited."))

    def update(self, **kwargs):
        raise ValidationError(_("Append-Only Violation: Bulk updating InventoryLedger records is prohibited."))


class InventoryLedger(models.Model):
    """
    Strictly append-only double-entry audit ledger tracking every single stock movement.
    Immutable: Entries cannot be modified or deleted once persisted.
    """
    objects = InventoryLedgerQuerySet.as_manager()
    item = models.ForeignKey(
        'inventory.Item',
        on_delete=models.PROTECT,
        related_name='ledger_entries',
        help_text=_("The inventory item affected by this stock transaction.")
    )
    quantity_changed = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        help_text=_("Quantity changed. Positive for inward/production, negative for outward/consumption.")
    )
    transaction_type = models.CharField(
        max_length=20,
        choices=TransactionType.choices,
        db_index=True,
        help_text=_("Operational category of the stock movement.")
    )
    contact = models.ForeignKey(
        Contact,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='inventory_transactions',
        help_text=_("Associated Vendor (for inward receipts) or Client (for outward dispatches).")
    )
    generic_reference_id = models.CharField(
        max_length=100,
        db_index=True,
        blank=True,
        help_text=_("External reference identifier (e.g., PO-10042, WO-203, SO-5091, INV-882).")
    )
    unit_cost = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        default=Decimal('0.0000'),
        help_text=_("Unit acquisition or production cost at the moment of this transaction.")
    )
    selling_price = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        default=Decimal('0.0000'),
        help_text=_("Unit selling price for commercial sales dispatches.")
    )
    total_cost = models.DecimalField(
        max_digits=16,
        decimal_places=4,
        default=Decimal('0.0000'),
        help_text=_("Total financial valuation impact (quantity * unit_cost).")
    )
    narration = models.TextField(
        blank=True,
        help_text=_("Explanatory remarks, batch numbers, or inspection notes.")
    )
    timestamp = models.DateTimeField(
        default=timezone.now,
        db_index=True,
        help_text=_("Timestamp when the physical movement occurred.")
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp', '-id']
        verbose_name = _('Inventory Ledger Entry')
        verbose_name_plural = _('Inventory Ledger Entries')
        indexes = [
            models.Index(fields=['item', 'timestamp']),
            models.Index(fields=['transaction_type', 'timestamp']),
            models.Index(fields=['generic_reference_id']),
        ]

    def save(self, *args, **kwargs):
        """
        Enforce immutability: once written to the database, entries cannot be updated.
        """
        if self.pk is not None:
            raise ValidationError(
                _("Append-Only Violation: InventoryLedger records are immutable and cannot be updated.")
            )
        # Automatically compute total cost if unit_cost is provided and total_cost is unset
        if self.unit_cost and self.quantity_changed and not self.total_cost:
            self.total_cost = abs(self.quantity_changed) * self.unit_cost
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """
        Enforce audit preservation: ledger entries can never be deleted.
        """
        raise ValidationError(
            _("Audit Preservation Violation: InventoryLedger records cannot be deleted.")
        )

    def __str__(self) -> str:
        sign = "+" if self.quantity_changed > 0 else ""
        return (
            f"[{self.timestamp.strftime('%Y-%m-%d %H:%M')}] "
            f"{self.transaction_type} | {self.item.sku}: {sign}{self.quantity_changed} {self.item.unit_of_measure} "
            f"(Ref: {self.generic_reference_id or 'N/A'})"
        )


class PurchaseOrderStatus(models.TextChoices):
    PENDING = 'Pending', _('Pending')
    DELIVERED = 'Delivered', _('Delivered')


class PurchaseOrder(models.Model):
    """
    Formal procurement order placed with an external Vendor for Raw Materials or Components.
    """
    vendor = models.ForeignKey(
        Contact,
        on_delete=models.PROTECT,
        related_name='purchase_orders',
        limit_choices_to={'type': ContactType.VENDOR},
        help_text=_("Vendor supplier receiving this procurement order.")
    )
    item = models.ForeignKey(
        'inventory.Item',
        on_delete=models.PROTECT,
        related_name='purchase_orders',
        help_text=_("Inventory item ordered from the vendor.")
    )
    quantity = models.IntegerField(
        help_text=_("Total units ordered.")
    )
    order_date = models.DateField(
        auto_now_add=True,
        db_index=True,
        help_text=_("Date when the purchase order was generated.")
    )
    expected_delivery_date = models.DateField(
        help_text=_("Agreed delivery date from the supplier.")
    )
    status = models.CharField(
        max_length=20,
        choices=PurchaseOrderStatus.choices,
        default=PurchaseOrderStatus.PENDING,
        db_index=True,
        help_text=_("Procurement fulfillment status.")
    )

    class Meta:
        ordering = ['-order_date', '-id']
        verbose_name = _('Purchase Order')
        verbose_name_plural = _('Purchase Orders')
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name='purchase_order_quantity_positive'
            )
        ]
        indexes = [
            models.Index(fields=['vendor', 'status']),
            models.Index(fields=['order_date']),
        ]

    @property
    def is_overdue(self) -> bool:
        """
        Returns True if the order is still Pending and its expected delivery date is in the past.
        """
        return self.status == PurchaseOrderStatus.PENDING and self.expected_delivery_date < timezone.localdate()

    def __str__(self) -> str:
        return f"PO #{self.id or 'New'}: {self.vendor.company_name} - {self.quantity} x {self.item.sku} ({self.status})"
