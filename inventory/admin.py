from django.contrib import admin
from django.contrib.auth.models import Group
from django.db import models
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from .models import Item, Employee, ProductionLog, Company, DailyCoreBoxDispatch

# Groups are not used (access is per user), so hide them from the admin.
admin.site.unregister(Group)


class StockStatusFilter(admin.SimpleListFilter):
    """Zero-stock products stay in the system (and in the dropdowns) but are hidden from the inventory list."""
    title = _('stock')
    parameter_name = 'stock'

    def lookups(self, request, model_admin):
        return (('in', _('In stock')), ('out', _('Out of stock (hidden from inventory list)')))

    def queryset(self, request, queryset):
        if self.value() == 'in':
            return queryset.filter(current_stock__gt=0)
        if self.value() == 'out':
            return queryset.filter(current_stock__lte=0)
        return queryset


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    """Products: a name, a type and a quantity. Finished goods count pieces, raw material counts tonnes."""
    list_display = (
        'name',
        'type_badge',
        'formatted_stock',
        'is_active',
        'updated_at',
    )
    list_filter = (
        'type',
        StockStatusFilter,
        'is_active',
    )
    search_fields = (
        'name',
    )
    ordering = ('name',)
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        (None, {
            'fields': ('name', 'type', 'current_stock', 'is_active'),
            'description': _('Finished goods are counted in pieces, raw material in tonnes (set automatically from the type).'),
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
        return f"{obj.stock_display} {obj.unit_label}"


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
        'item__name',
    )
    autocomplete_fields = ['employee', 'item']
    list_select_related = ('employee', 'item')
    date_hierarchy = 'production_date'
    readonly_fields = ('production_date',)


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'is_active',
        'total_dispatched_boxes',
        'created_at',
    )
    list_filter = (
        'is_active',
        'created_at',
    )
    search_fields = (
        'name',
    )
    readonly_fields = ('created_at', 'updated_at')

    def get_queryset(self, request):
        # One query for the whole list instead of one SUM query per company.
        return super().get_queryset(request).annotate(_total_boxes=models.Sum('dispatches__quantity'))

    @admin.display(description=_('Total Core Boxes Dispatched'), ordering='_total_boxes')
    def total_dispatched_boxes(self, obj: Company) -> int:
        return getattr(obj, '_total_boxes', None) or 0


@admin.register(DailyCoreBoxDispatch)
class DailyCoreBoxDispatchAdmin(admin.ModelAdmin):
    list_display = (
        'dispatch_date',
        'company',
        'box_type',
        'quantity',
        'gate_pass_number',
        'vehicle_number',
        'location_to',
        'image_link',
        'created_at',
    )
    list_filter = (
        'dispatch_date',
        'company',
    )
    search_fields = (
        'company__name',
        'box_type',
        'gate_pass_number',
        'vehicle_number',
        'location_to',
    )
    autocomplete_fields = ['company', 'item']
    list_select_related = ('company',)
    date_hierarchy = 'dispatch_date'
    readonly_fields = ('image_link', 'created_at', 'updated_at')

    def get_queryset(self, request):
        return super().get_queryset(request).defer('attachment_data')

    @admin.display(description='Image')
    def image_link(self, obj):
        if not obj.pk or not obj.has_attachment:
            return '-'
        url = reverse('daily-core-box-attachment', kwargs={'pk': obj.pk})
        return format_html('<a href="{}" target="_blank" rel="noopener">View image</a>', url)

