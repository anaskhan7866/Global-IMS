from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from .models import Contact, InventoryLedger, TransactionType, PurchaseOrder, PurchaseOrderStatus


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = (
        'company_name',
        'type_badge',
        'contact_person',
        'email',
        'phone',
        'GST_number',
        'is_active',
        'created_at',
    )
    list_filter = (
        'type',
        'is_active',
        'created_at',
    )
    search_fields = (
        'company_name',
        'contact_person',
        'email',
        'phone',
        'GST_number',
    )
    ordering = ('company_name',)
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        (_('Entity Identification'), {
            'fields': ('type', 'company_name', 'contact_person', 'is_active')
        }),
        (_('Communication & Billing'), {
            'fields': ('email', 'phone', 'GST_number', 'address')
        }),
        (_('Audit Timestamps'), {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    @admin.display(description=_('Contact Type'), ordering='type')
    def type_badge(self, obj: Contact) -> str:
        color = '#7C2D12' if obj.type == 'Vendor' else '#1E3A8A'
        bg = '#FFEDD5' if obj.type == 'Vendor' else '#DBEAFE'
        return format_html(
            '<span style="background-color: {}; color: {}; font-weight: 600; padding: 2px 8px; border-radius: 9999px;">{}</span>',
            bg, color, obj.get_type_display()
        )


@admin.register(InventoryLedger)
class InventoryLedgerAdmin(admin.ModelAdmin):
    list_display = (
        'timestamp',
        'transaction_type_badge',
        'item',
        'formatted_quantity',
        'formatted_unit_cost',
        'formatted_selling_price',
        'formatted_total_cost',
        'contact',
        'generic_reference_id',
    )
    list_filter = (
        'transaction_type',
        'timestamp',
        'item__type',
    )
    search_fields = (
        'generic_reference_id',
        'item__sku',
        'item__name',
        'contact__company_name',
        'narration',
    )
    autocomplete_fields = ['item', 'contact']
    date_hierarchy = 'timestamp'
    readonly_fields = ('created_at',)

    fieldsets = (
        (_('Movement Particulars'), {
            'fields': ('transaction_type', 'item', 'quantity_changed', 'timestamp', 'generic_reference_id')
        }),
        (_('Valuation Impact'), {
            'fields': ('unit_cost', 'selling_price', 'total_cost', 'contact')
        }),
        (_('Audit & Narration'), {
            'fields': ('narration', 'created_at')
        }),
    )

    def get_readonly_fields(self, request, obj=None):
        # Enforce append-only ledger: existing persisted records are strictly read-only
        if obj:
            return [f.name for f in self.model._meta.fields]
        return self.readonly_fields

    def has_delete_permission(self, request, obj=None) -> bool:
        # Enforce audit trail preservation: ledger entries cannot be deleted
        return False

    @admin.display(description=_('Type'), ordering='transaction_type')
    def transaction_type_badge(self, obj: InventoryLedger) -> str:
        badge_styles = {
            TransactionType.IN: ('#D1FAE5', '#065F46'),
            TransactionType.OUT: ('#FEE2E2', '#991B1B'),
            TransactionType.MANUFACTURED: ('#EDE9FE', '#5B21B6'),
        }
        bg, color = badge_styles.get(obj.transaction_type, ('#F3F4F6', '#1F2937'))
        return format_html(
            '<span style="background-color: {}; color: {}; font-weight: 600; padding: 2px 8px; border-radius: 9999px;">{}</span>',
            bg, color, obj.get_transaction_type_display()
        )

    @admin.display(description=_('Quantity Changed'), ordering='quantity_changed')
    def formatted_quantity(self, obj: InventoryLedger) -> str:
        color = '#059669' if obj.quantity_changed > 0 else '#DC2626'
        sign = "+" if obj.quantity_changed > 0 else ""
        return format_html(
            '<span style="color: {}; font-weight: 700;">{}{} {}</span>',
            color, sign, f"{obj.quantity_changed:,.2f}", obj.item.unit_of_measure
        )

    @admin.display(description=_('Unit Cost'), ordering='unit_cost')
    def formatted_unit_cost(self, obj: InventoryLedger) -> str:
        return f"${obj.unit_cost:,.2f}"

    @admin.display(description=_('Selling Price'), ordering='selling_price')
    def formatted_selling_price(self, obj: InventoryLedger) -> str:
        return f"${obj.selling_price:,.2f}" if obj.selling_price > 0 else "-"

    @admin.display(description=_('Total Cost'), ordering='total_cost')
    def formatted_total_cost(self, obj: InventoryLedger) -> str:
        return f"${obj.total_cost:,.2f}"


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'vendor',
        'item',
        'quantity',
        'order_date',
        'expected_delivery_date',
        'status_badge',
    )
    list_filter = (
        'status',
        'order_date',
        'expected_delivery_date',
    )
    search_fields = (
        'vendor__company_name',
        'item__sku',
        'item__name',
    )
    autocomplete_fields = ['vendor', 'item']
    date_hierarchy = 'order_date'
    readonly_fields = ('order_date',)

    @admin.display(description=_('Status'), ordering='status')
    def status_badge(self, obj: PurchaseOrder) -> str:
        if obj.status == PurchaseOrderStatus.DELIVERED:
            badge_style = (
                "background-color: #D1FAE5; color: #065F46; "
                "font-weight: 600; padding: 2px 8px; border-radius: 9999px;"
            )
            return format_html(
                '<span style="{}">{}</span>',
                badge_style,
                obj.get_status_display()
            )
        badge_style = (
            "background-color: #FEF3C7; color: #92400E; "
            "font-weight: 600; padding: 2px 8px; border-radius: 9999px;"
        )
        return format_html(
            '<span style="{}">{}</span>',
            badge_style,
            obj.get_status_display()
        )
