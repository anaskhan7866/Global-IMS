import datetime
from decimal import Decimal
from django.db import transaction
from django.db.models import Count, Sum, Q, Value
from django.db.models.functions import Coalesce
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.views import View
from django.views.generic import ListView, DetailView

from inventory.models import Employee, ProductionLog, Item
from transactions.models import PurchaseOrder, Contact, ContactType, InventoryLedger, TransactionType


class EmployeeListView(ListView):
    """
    Fetches all active employees and calculates their manufacturing productivity
    for the current calendar month using Count and Sum annotations.
    Also handles adding a new employee and deactivating an employee via POST.
    """
    model = Employee
    context_object_name = 'employees'
    template_name = 'inventory/employee_list.html'

    def get_queryset(self):
        now = timezone.now()
        current_year = now.year
        current_month = now.month

        # Month filter for production logs
        current_month_filter = Q(
            production_logs__production_date__year=current_year,
            production_logs__production_date__month=current_month
        )

        return (
            Employee.objects.filter(is_active=True)
            .annotate(
                total_boxes=Coalesce(
                    Sum('production_logs__quantity', filter=current_month_filter),
                    Value(0)
                ),
                boxes_manufactured=Coalesce(
                    Sum('production_logs__quantity', filter=current_month_filter),
                    Value(0)
                ),
                production_count=Count(
                    'production_logs',
                    filter=current_month_filter
                ),
            )
            .order_by('-total_boxes', 'last_name', 'first_name')
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        now = timezone.now()
        context['current_month'] = now.month
        context['current_year'] = now.year
        context['current_month_name'] = now.strftime('%B %Y')
        return context

    def post(self, request, *args, **kwargs):
        action = request.POST.get('action')

        if action == 'add_employee':
            first_name = request.POST.get('first_name', '').strip()
            last_name = request.POST.get('last_name', '').strip()
            is_active_val = request.POST.get('is_active') in ['true', 'on', '1']

            if not first_name or not last_name:
                messages.error(request, "First Name and Last Name are required.")
                return redirect('employee-list')

            emp = Employee.objects.create(
                first_name=first_name,
                last_name=last_name,
                is_active=is_active_val
            )
            messages.success(request, f"Employee '{emp.full_name}' was registered successfully.")
            return redirect('employee-list')

        elif action == 'deactivate' or action == 'delete':
            employee_id = request.POST.get('employee_id')
            emp = get_object_or_404(Employee, pk=employee_id)
            emp.is_active = False
            emp.save(update_fields=['is_active'])
            messages.info(request, f"Employee '{emp.full_name}' has been deactivated.")
            return redirect('employee-list')

        messages.warning(request, "Unrecognized action.")
        return redirect('employee-list')


class EmployeeDetailView(DetailView):
    """
    Takes an employee ID and a Month parameter.
    Returns a grouped daily breakdown (using values('production_date').annotate(Sum('quantity')))
    of what the employee built that month, along with item-level specifics.
    """
    model = Employee
    context_object_name = 'employee'
    template_name = 'inventory/employee_detail.html'
    pk_url_kwarg = 'pk'

    def get_object(self, queryset=None):
        pk = self.kwargs.get('pk') or self.kwargs.get('employee_id') or self.kwargs.get('id')
        if pk is not None:
            if queryset is None:
                queryset = self.get_queryset()
            return get_object_or_404(queryset, pk=pk)
        return super().get_object(queryset)

    def get_daily_breakdown(self, employee, month: int, year: int):
        """
        Executes daily breakdown aggregation for the specified employee and month.
        """
        return (
            ProductionLog.objects.filter(
                employee=employee,
                production_date__year=year,
                production_date__month=month
            )
            .values('production_date')
            .annotate(Sum('quantity'))
            .order_by('production_date')
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        employee = self.object

        # Resolve target month (URL kwarg or GET parameter, fallback to current month)
        now = timezone.now()
        request_get = self.request.GET if hasattr(self, 'request') and self.request else {}
        raw_month = self.kwargs.get('month') or request_get.get('month', now.month)
        try:
            month = int(raw_month)
            if not 1 <= month <= 12:
                month = now.month
        except (ValueError, TypeError):
            month = now.month

        # Resolve target year (URL kwarg or GET parameter, fallback to current year)
        raw_year = self.kwargs.get('year') or request_get.get('year', now.year)
        try:
            year = int(raw_year)
        except (ValueError, TypeError):
            year = now.year

        # Daily breakdown using values('production_date').annotate(Sum('quantity'))
        daily_breakdown = self.get_daily_breakdown(employee, month, year)

        # Detailed breakdown including item name and SKU
        production_breakdown = (
            ProductionLog.objects.filter(
                employee=employee,
                production_date__year=year,
                production_date__month=month
            )
            .values('production_date', 'item__name', 'item__sku', 'item__unit_of_measure')
            .annotate(
                quantity_produced=Sum('quantity'),
                total_qty=Sum('quantity')
            )
            .order_by('-production_date', 'item__name')
        )

        # Summary calculations for KPI cards
        total_units = sum(entry['quantity__sum'] for entry in daily_breakdown) if daily_breakdown else 0
        active_days = len(daily_breakdown)

        month_date = datetime.date(year, month, 1)

        context['selected_month'] = month
        context['selected_year'] = year
        context['selected_month_name'] = month_date.strftime('%B %Y')
        context['daily_breakdown'] = daily_breakdown
        context['production_breakdown'] = production_breakdown
        context['total_month_units'] = total_units
        context['active_days'] = active_days
        return context


class VendorOrderListView(ListView):
    """
    Fetches all PurchaseOrder records, displaying the Vendor name, item,
    quantity, and expected delivery date.
    Optimized with select_related for vendor and item foreign keys.
    """
    model = PurchaseOrder
    context_object_name = 'purchase_orders'
    template_name = 'inventory/vendor_orders.html'

    def get_queryset(self):
        return (
            PurchaseOrder.objects.select_related('vendor', 'item')
            .all()
            .order_by('-order_date', '-id')
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.now().date()
        orders = list(self.get_queryset())

        context['today'] = today
        context['total_count'] = len(orders)
        context['pending_count'] = sum(1 for o in orders if o.status == 'Pending')
        context['overdue_count'] = sum(1 for o in orders if o.is_overdue)
        context['delivered_count'] = sum(1 for o in orders if o.status == 'Delivered')
        return context


class AddInventoryView(View):
    """
    Unified page and handler allowing the user to select an Item and add stock directly.
    Recalculates Moving Weighted Average Cost and writes an immutable entry to InventoryLedger.
    """
    template_name = 'inventory/add_inventory.html'

    def get(self, request, *args, **kwargs):
        items = Item.objects.filter(is_active=True).order_by('type', 'name')
        vendors = Contact.objects.filter(type=ContactType.VENDOR, is_active=True).order_by('company_name')
        recent_ledgers = (
            InventoryLedger.objects.filter(transaction_type=TransactionType.IN)
            .select_related('item', 'contact')
            .order_by('-id')[:10]
        )
        return render(request, self.template_name, {
            'items': items,
            'vendors': vendors,
            'recent_ledgers': recent_ledgers,
        })

    def post(self, request, *args, **kwargs):
        item_id = request.POST.get('item_id')
        raw_quantity = request.POST.get('quantity')
        raw_unit_cost = request.POST.get('unit_cost', '0.00')
        vendor_id = request.POST.get('vendor_id')
        reference_id = request.POST.get('reference_id', '').strip()
        narration = request.POST.get('narration', '').strip()

        # Validation
        if not item_id:
            messages.error(request, "Please select an inventory item.")
            return redirect('add-inventory')

        try:
            quantity = Decimal(str(raw_quantity))
            if quantity <= Decimal('0'):
                raise ValueError
        except (ValueError, TypeError):
            messages.error(request, "Please enter a valid positive quantity to add.")
            return redirect('add-inventory')

        try:
            unit_cost = Decimal(str(raw_unit_cost)) if raw_unit_cost else Decimal('0.0000')
            if unit_cost < Decimal('0'):
                raise ValueError
        except (ValueError, TypeError):
            messages.error(request, "Unit cost cannot be negative.")
            return redirect('add-inventory')

        vendor = None
        if vendor_id:
            vendor = Contact.objects.filter(pk=vendor_id, type=ContactType.VENDOR).first()

        with transaction.atomic():
            locked_item = Item.objects.select_for_update().get(pk=item_id)

            old_stock = locked_item.current_stock
            old_cost = locked_item.moving_average_cost
            old_total_val = old_stock * old_cost
            new_purchase_val = quantity * unit_cost
            new_stock = old_stock + quantity

            # Recalculate moving average cost if positive cost supplied, otherwise preserve previous
            if new_stock > Decimal('0'):
                if unit_cost > Decimal('0'):
                    new_wac = (old_total_val + new_purchase_val) / new_stock
                else:
                    new_wac = old_cost
            else:
                new_wac = unit_cost

            locked_item.current_stock = new_stock
            locked_item.moving_average_cost = new_wac.quantize(Decimal('0.0001'))
            locked_item.save(update_fields=['current_stock', 'moving_average_cost', 'updated_at'])

            default_narration = narration or f"Manual stock inward addition of {quantity} {locked_item.unit_of_measure}."
            InventoryLedger.objects.create(
                item=locked_item,
                quantity_changed=quantity,
                transaction_type=TransactionType.IN,
                contact=vendor,
                generic_reference_id=reference_id or 'DIRECT-INWARD',
                unit_cost=unit_cost,
                total_cost=new_purchase_val,
                narration=default_narration
            )

        messages.success(
            request,
            f"Successfully added +{quantity} {locked_item.unit_of_measure} to '{locked_item.name}' (SKU: {locked_item.sku}). "
            f"New Stock: {locked_item.current_stock} | Updated WAC: ${locked_item.moving_average_cost:.2f}"
        )
        return redirect('add-inventory')
