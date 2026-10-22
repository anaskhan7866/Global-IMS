from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from .models import Item, BillOfMaterials, BOMComponent, Employee, ProductionLog


class BOMComponentInline(admin.TabularInline):
    model = BOMComponent
    extra = 1
    fk_name = 'bom'
    autocomplete_fields = ['raw_material']
    fields = ('raw_material', 'quantity', 'scrap_percentage')
    show_change_link = True


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = (
        'sku',
        'name',
        'type_badge',
        'unit_of_measure',
        'formatted_stock',
        'formatted_weight',
        'formatted_moving_cost',
        'is_active',
        'updated_at',
    )
    list_filter = (
        'type',
        'unit_of_measure',
        'is_active',
        'created_at',
    )
    search_fields = (
        'sku',
        'name',
        'description',
    )
    ordering = ('name',)
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        (_('Item Identification'), {
            'fields': ('sku', 'name', 'type', 'unit_of_measure', 'is_active', 'description')
        }),
        (_('Inventory & Financial Valuation'), {
            'fields': ('current_stock', 'weight', 'moving_average_cost'),
            'description': _('Current stock levels are modified via transaction ledgers.')
        }),
        (_('Audit Timestamps'), {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    @admin.display(description=_('Type'), ordering='type')
    def type_badge(self, obj: Item) -> str:
        color = '#1E40AF' if obj.type == 'Raw Material' else '#065F46'
        bg = '#DBEAFE' if obj.type == 'Raw Material' else '#D1FAE5'
        return format_html(
            '<span style="background-color: {}; color: {}; font-weight: 600; padding: 2px 8px; border-radius: 9999px;">{}</span>',
            bg, color, obj.get_type_display()
        )

    @admin.display(description=_('Current Stock'), ordering='current_stock')
    def formatted_stock(self, obj: Item) -> str:
        return f"{obj.current_stock:,.2f} {obj.unit_of_measure}"

    @admin.display(description=_('Weight (kg)'), ordering='weight')
    def formatted_weight(self, obj: Item) -> str:
        return f"{obj.weight:,.3f} kg"

    @admin.display(description=_('Moving Avg Cost'), ordering='moving_average_cost')
    def formatted_moving_cost(self, obj: Item) -> str:
        return f"${obj.moving_average_cost:,.2f}"


@admin.register(BillOfMaterials)
class BillOfMaterialsAdmin(admin.ModelAdmin):
    list_display = (
        'finished_good',
        'version',
        'components_count',
        'is_active',
        'created_at',
    )
    list_filter = (
        'is_active',
        'created_at',
    )
    search_fields = (
        'finished_good__sku',
        'finished_good__name',
        'version',
        'description',
    )
    autocomplete_fields = ['finished_good']
    inlines = [BOMComponentInline]
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        (_('BOM Specifications'), {
            'fields': ('finished_good', 'version', 'is_active', 'description')
        }),
        (_('Audit Timestamps'), {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    @admin.display(description=_('Total Components'))
    def components_count(self, obj: BillOfMaterials) -> int:
        return obj.components.count()


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = (
        'full_name',
        'first_name',
        'last_name',
        'status_badge',
        'created_at',
    )
    list_filter = (
        'is_active',
        'created_at',
    )
    search_fields = (
        'first_name',
        'last_name',
    )
    readonly_fields = ('created_at',)

    @admin.display(description=_('Status'), ordering='is_active')
    def status_badge(self, obj: Employee) -> str:
        if obj.is_active:
            style = "background-color: #D1FAE5; color: #065F46; font-weight: 600; padding: 2px 8px; border-radius: 9999px;"
            return format_html('<span style="{}">{}</span>', style, _('Active'))
        style = "background-color: #FEE2E2; color: #991B1B; font-weight: 600; padding: 2px 8px; border-radius: 9999px;"
        return format_html('<span style="{}">{}</span>', style, _('Inactive'))


@admin.register(ProductionLog)
class ProductionLogAdmin(admin.ModelAdmin):
    list_display = (
        'production_date',
        'employee',
        'item',
        'quantity',
    )
    list_filter = (
        'production_date',
        'item__type',
    )
    search_fields = (
        'employee__first_name',
        'employee__last_name',
        'item__sku',
        'item__name',
    )
    autocomplete_fields = ['employee', 'item']
    date_hierarchy = 'production_date'
    readonly_fields = ('production_date',)
