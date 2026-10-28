import datetime
import re
from decimal import Decimal
from django.db import transaction, IntegrityError
from django.db.models import Count, Sum, Q, Value, ProtectedError, Max
from django.db.models.functions import Coalesce
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils import timezone
from django.views import View
from django.views.generic import ListView, DetailView

from inventory.models import Employee, ProductionLog, Item, ItemType, Company, DailyCoreBoxDispatch
from transactions.models import PurchaseOrder, Contact, ContactType, InventoryLedger, TransactionType, PurchaseOrderStatus


def get_or_resolve_production_item(item_id=None, item_name=None, weight=None):
    """Resolve an item from the catalog or auto-create a finished good if newly named."""
    if item_id:
        found = Item.objects.filter(pk=item_id).first()
        if found:
            return found

    if item_name and str(item_name).strip():
        clean_name = str(item_name).strip()
        parsed_weight = None
        if weight is not None and str(weight).strip() != '':
            try:
                parsed_weight = Decimal(str(weight).strip())
            except (ValueError, TypeError):
                pass

        if parsed_weight is not None:
            found = Item.objects.filter(name__iexact=clean_name, weight=parsed_weight, is_active=True).first()
            if not found:
                found = Item.objects.filter(name__iexact=clean_name, weight=parsed_weight).first()
            if found:
                if not found.is_active:
                    found.is_active = True
                    found.save(update_fields=['is_active'])
                return found

            # If no box with that exact weight exists, check if one exists with 0 weight (unassigned weight)
            unweighted = Item.objects.filter(name__iexact=clean_name, weight=Decimal('0.000'), is_active=True).first()
            if not unweighted:
                unweighted = Item.objects.filter(name__iexact=clean_name, weight=Decimal('0.000')).first()
            if unweighted:
                unweighted.weight = parsed_weight
                if not unweighted.is_active:
                    unweighted.is_active = True
                unweighted.save(update_fields=['weight', 'is_active', 'updated_at'])
                return unweighted
        else:
            found = Item.objects.filter(name__iexact=clean_name, is_active=True).first()
            if not found:
                found = Item.objects.filter(name__iexact=clean_name).first()
            if found:
                return found

        # Auto-create finished good with generated unique SKU
        clean_slug = re.sub(r'[^A-Za-z0-9]+', '-', clean_name).strip('-').upper()
        base_sku = f"FG-{clean_slug[:12]}" if clean_slug else "FG-BOX"
        sku = base_sku
        counter = 1
        while Item.objects.filter(sku=sku).exists():
            sku = f"{base_sku}-{counter:02d}"
            counter += 1
        return Item.objects.create(
            sku=sku,
            name=clean_name,
            type=ItemType.FINISHED_GOOD,
            unit_of_measure='pcs',
            current_stock=Decimal('0.0000'),
            weight=parsed_weight if parsed_weight is not None else Decimal('0.000'),
            moving_average_cost=Decimal('0.0000')
        )

    fg = Item.objects.filter(type=ItemType.FINISHED_GOOD, is_active=True).first()
    if fg:
        return fg

    any_item = Item.objects.filter(is_active=True).first()
    if any_item:
        return any_item

    # Fallback to creating standard finished good if catalog is empty
    return Item.objects.create(
        sku='FG-CORE-BOX-01',
        name='GI Core Box',
        type=ItemType.FINISHED_GOOD,
        unit_of_measure='pcs',
        current_stock=Decimal('0.0000'),
        weight=Decimal('0.000'),
        moving_average_cost=Decimal('0.0000')
    )


class EmployeeListView(LoginRequiredMixin, ListView):
    """Display active workforce directory and aggregate current-month and daily manufacturing outputs."""
    model = Employee
    context_object_name = 'employees'
    template_name = 'inventory/employee_list.html'

    def get_queryset(self):
        today = timezone.localdate()
        current_year = today.year
        current_month = today.month

        # Month filter for production logs
        current_month_filter = Q(
            production_logs__production_date__year=current_year,
            production_logs__production_date__month=current_month
        )

        # Today's filter for end-of-day tracking
        today_filter = Q(
            production_logs__production_date=today
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
                today_boxes=Coalesce(
                    Sum('production_logs__quantity', filter=today_filter),
                    Value(0)
                ),
                production_count=Count(
                    'production_logs',
                    filter=current_month_filter
                ),
            )
            .order_by('-today_boxes', '-total_boxes', 'last_name', 'first_name')
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        context['current_month'] = today.month
        context['current_year'] = today.year
        context['today'] = today
        context['today_str'] = today.strftime('%A, %b %d, %Y')
        context['current_month_name'] = today.strftime('%B %Y')

        # Catalog finished goods for box logging
        finished_goods = Item.objects.filter(type=ItemType.FINISHED_GOOD, is_active=True).order_by('name')
        if not finished_goods.exists():
            finished_goods = Item.objects.filter(is_active=True).order_by('name')
        context['finished_goods'] = finished_goods
        context['default_item'] = finished_goods.first() if finished_goods.exists() else None

        employees = context.get('employees', [])
        context['total_today_boxes'] = sum(e.today_boxes for e in employees)
        return context

    def post(self, request, *args, **kwargs):
        action = request.POST.get('action')

        # 1. Add New Employee
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

            # Optional: if initial boxes for today were entered when adding the employee
            initial_boxes_raw = request.POST.get('initial_boxes', '').strip()
            if initial_boxes_raw:
                try:
                    initial_qty = int(initial_boxes_raw)
                    if initial_qty > 0:
                        item = get_or_resolve_production_item(request.POST.get('item_id'))
                        ProductionLog.objects.create(
                            employee=emp,
                            item=item,
                            quantity=initial_qty
                        )
                        messages.success(
                            request,
                            f"Employee '{emp.full_name}' registered with {initial_qty} boxes built today."
                        )
                        return redirect('employee-list')
                except (ValueError, TypeError):
                    pass

            messages.success(request, f"Employee '{emp.full_name}' was registered successfully.")
            return redirect('employee-list')

        # 2. Log End-of-Day Output for a Single Employee
        elif action == 'log_production':
            employee_id = request.POST.get('employee_id')
            raw_quantity = request.POST.get('quantity')
            raw_weight = request.POST.get('weight')
            item_id = request.POST.get('item_id')
            box_name = (request.POST.get('box_name') or request.POST.get('name') or request.POST.get('item_name') or '').strip()

            emp = get_object_or_404(Employee, pk=employee_id)

            try:
                quantity = int(raw_quantity)
                if quantity <= 0:
                    raise ValueError
            except (ValueError, TypeError):
                messages.error(request, f"Please enter a valid positive number of boxes for {emp.full_name}.")
                return redirect('employee-list')

            item = get_or_resolve_production_item(item_id=item_id, item_name=box_name, weight=raw_weight)
            if raw_weight and item.weight == Decimal('0.000'):
                try:
                    w_val = Decimal(str(raw_weight).strip())
                    if w_val > Decimal('0'):
                        item.weight = w_val
                        item.save(update_fields=['weight', 'updated_at'])
                except (ValueError, TypeError):
                    pass

            target_date = timezone.localdate()
            raw_date = request.POST.get('production_date') or request.POST.get('date')
            if raw_date:
                try:
                    target_date = datetime.date.fromisoformat(raw_date.strip())
                except (ValueError, TypeError):
                    pass

            ProductionLog.objects.create(
                employee=emp,
                item=item,
                quantity=quantity,
                production_date=target_date
            )

            messages.success(
                request,
                f"Saved {quantity} boxes of '{item.name}' for {emp.full_name} today!"
            )
            return redirect('employee-list')

        # 3. Bulk End-of-Day Production Sheet (Multiple Employees at Once)
        elif action == 'bulk_log_production':
            item_id = request.POST.get('item_id')
            item = get_or_resolve_production_item(item_id)
            logged_count = 0
            total_boxes_saved = 0

            for key, val in request.POST.items():
                if key.startswith('boxes_') and val.strip():
                    try:
                        emp_id = int(key.split('_')[1])
                        qty = int(val.strip())
                        if qty > 0:
                            emp = Employee.objects.filter(pk=emp_id, is_active=True).first()
                            if emp:
                                ProductionLog.objects.create(
                                    employee=emp,
                                    item=item,
                                    quantity=qty,
                                    production_date=timezone.localdate()
                                )
                                logged_count += 1
                                total_boxes_saved += qty
                    except (ValueError, IndexError):
                        continue

            if logged_count > 0:
                messages.success(
                    request,
                    f"Saved end-of-day production: {total_boxes_saved} total boxes recorded across {logged_count} operators."
                )
            else:
                messages.info(request, "No production quantities were entered.")
            return redirect('employee-list')

        elif action in ('deactivate', 'delete'):
            employee_id = request.POST.get('employee_id')
            emp = get_object_or_404(Employee, pk=employee_id)
            emp_name = emp.full_name
            try:
                emp.delete()
            except (ProtectedError, IntegrityError):
                # Fall back to soft-deactivation if historical production logs reference this record
                emp.is_active = False
                emp.save(update_fields=['is_active'])
            messages.success(request, f"Employee '{emp_name}' has been deleted.")
            return redirect('employee-list')

        elif action == 'delete_date':
            employee_id = request.POST.get('employee_id')
            emp = get_object_or_404(Employee, pk=employee_id)
            raw_date = request.POST.get('production_date') or request.POST.get('date')
            if not raw_date:
                messages.error(request, "Date is required to delete production logs.")
                return redirect('employee-list')

            try:
                target_date = datetime.date.fromisoformat(raw_date.strip())
            except (ValueError, TypeError):
                messages.error(request, "Invalid date format specified.")
                return redirect('employee-list')

            deleted_count, _ = ProductionLog.objects.filter(
                employee=emp,
                production_date=target_date
            ).delete()

            messages.success(
                request,
                f"Deleted daily log for {emp.full_name} on {target_date.strftime('%b %d, %Y')} "
                f"({deleted_count} record{'s' if deleted_count > 1 else ''} removed)."
            )
            return redirect('employee-list')

        messages.warning(request, "Unrecognized action.")
        return redirect('employee-list')


class EmployeeDetailView(LoginRequiredMixin, DetailView):
    """Provide date-grouped daily production log breakdowns for an individual operator."""
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
        today = timezone.localdate()
        request_get = self.request.GET if hasattr(self, 'request') and self.request else {}
        raw_month = self.kwargs.get('month') or request_get.get('month', today.month)
        try:
            month = int(raw_month)
            if not 1 <= month <= 12:
                month = today.month
        except (ValueError, TypeError):
            month = today.month

        # Resolve target year (URL kwarg or GET parameter, fallback to current year)
        raw_year = self.kwargs.get('year') or request_get.get('year', today.year)
        try:
            year = int(raw_year)
        except (ValueError, TypeError):
            year = today.year

        # Daily breakdown using values('production_date').annotate(Sum('quantity'))
        daily_breakdown = self.get_daily_breakdown(employee, month, year)

        # Detailed breakdown including item name, SKU, and unit weight
        production_breakdown = (
            ProductionLog.objects.filter(
                employee=employee,
                production_date__year=year,
                production_date__month=month
            )
            .values('production_date', 'item__id', 'item__name', 'item__sku', 'item__unit_of_measure', 'item__weight')
            .annotate(
                quantity_produced=Sum('quantity'),
                total_qty=Sum('quantity')
            )
            .order_by('-production_date', 'item__name')
        )

        # Build daily_groups: for each date, total boxes and individual box type breakdown
        daily_records = {}
        for row in production_breakdown:
            p_date = row['production_date']
            if p_date not in daily_records:
                daily_records[p_date] = {
                    'production_date': p_date,
                    'total_quantity': 0,
                    'items': [],
                }
            qty = row['quantity_produced'] or 0
            daily_records[p_date]['total_quantity'] += qty
            daily_records[p_date]['items'].append({
                'item_id': row.get('item__id'),
                'name': row.get('item__name'),
                'sku': row.get('item__sku'),
                'weight': row.get('item__weight'),
                'quantity': qty,
                'unit_of_measure': row.get('item__unit_of_measure') or 'Units',
            })

        daily_groups = []
        for p_date in sorted(daily_records.keys(), reverse=True):
            rec = daily_records[p_date]
            rec['is_single_type'] = (len(rec['items']) == 1)
            rec['single_item'] = rec['items'][0] if rec['is_single_type'] else None
            daily_groups.append(rec)

        # Finished goods catalog for output logging
        finished_goods = Item.objects.filter(type=ItemType.FINISHED_GOOD, is_active=True).order_by('name')
        if not finished_goods.exists():
            finished_goods = Item.objects.filter(is_active=True).order_by('name')

        # Summary calculations for KPI cards
        total_units = sum(entry['quantity__sum'] for entry in daily_breakdown) if daily_breakdown else 0
        active_days = len(daily_breakdown)
        daily_average = round(total_units / active_days, 1) if active_days > 0 else 0.0

        month_date = datetime.date(year, month, 1)

        context['selected_month'] = month
        context['selected_year'] = year
        context['selected_month_name'] = month_date.strftime('%B %Y')
        context['daily_breakdown'] = daily_breakdown
        context['production_breakdown'] = production_breakdown
        context['daily_groups'] = daily_groups
        context['total_month_units'] = total_units
        context['active_days'] = active_days
        context['daily_average'] = daily_average
        context['finished_goods'] = finished_goods
        context['default_item'] = finished_goods.first() if finished_goods.exists() else None
        context['employee'] = employee
        context['today'] = today
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        action = request.POST.get('action', 'log_production')

        if action == 'log_production':
            raw_quantity = request.POST.get('quantity')
            raw_weight = request.POST.get('weight')
            item_id = request.POST.get('item_id')
            box_name = (request.POST.get('box_name') or request.POST.get('name') or request.POST.get('item_name') or '').strip()
            raw_date = request.POST.get('production_date')

            try:
                quantity = int(raw_quantity)
                if quantity <= 0:
                    raise ValueError
            except (ValueError, TypeError):
                messages.error(request, f"Please enter a valid positive number of boxes for {self.object.full_name}.")
                return redirect('employee-detail', pk=self.object.pk, month=self.kwargs.get('month', timezone.localdate().month))

            item = get_or_resolve_production_item(item_id=item_id, item_name=box_name, weight=raw_weight)
            if raw_weight and item.weight == Decimal('0.000'):
                try:
                    w_val = Decimal(str(raw_weight).strip())
                    if w_val > Decimal('0'):
                        item.weight = w_val
                        item.save(update_fields=['weight', 'updated_at'])
                except (ValueError, TypeError):
                    pass

            target_date = timezone.localdate()
            if raw_date:
                try:
                    target_date = datetime.date.fromisoformat(raw_date.strip())
                except (ValueError, TypeError):
                    pass

            log = ProductionLog.objects.create(
                employee=self.object,
                item=item,
                quantity=quantity,
                production_date=target_date
            )

            messages.success(
                request,
                f"Logged {quantity} boxes of '{item.name}' for {self.object.full_name} ({log.production_date})."
            )
            return redirect('employee-detail', pk=self.object.pk, month=log.production_date.month)

        elif action == 'delete_date':
            raw_date = request.POST.get('production_date') or request.POST.get('date')
            redirect_month = self.kwargs.get('month') or timezone.localdate().month

            if not raw_date:
                messages.error(request, "Date is required to delete daily production logs.")
                return redirect('employee-detail', pk=self.object.pk, month=redirect_month)

            try:
                target_date = datetime.date.fromisoformat(raw_date.strip())
            except (ValueError, TypeError):
                messages.error(request, "Invalid date format specified.")
                return redirect('employee-detail', pk=self.object.pk, month=redirect_month)

            logs_to_delete = ProductionLog.objects.filter(
                employee=self.object,
                production_date=target_date
            )
            deleted_count, _ = logs_to_delete.delete()

            if deleted_count > 0:
                messages.success(
                    request,
                    f"Successfully deleted daily log for {target_date.strftime('%A, %b %d, %Y')} "
                    f"({deleted_count} log entr{'ies' if deleted_count > 1 else 'y'} removed)."
                )
            else:
                messages.info(request, f"No daily production logs found for {target_date.strftime('%b %d, %Y')}.")

            redirect_month = self.kwargs.get('month') or target_date.month
            year = request.POST.get('year') or request.GET.get('year')
            url = reverse('employee-detail', kwargs={'pk': self.object.pk, 'month': redirect_month})
            if year:
                url = f"{url}?year={year}"
            return redirect(url)

        messages.warning(request, "Unrecognized action.")
        return redirect('employee-detail', pk=self.object.pk, month=self.kwargs.get('month', timezone.now().month))


class VendorOrderListView(LoginRequiredMixin, ListView):
    """Manage company purchase orders and delivery fulfillment schedules."""
    model = PurchaseOrder
    context_object_name = 'purchase_orders'
    template_name = 'inventory/vendor_orders.html'

    def get_queryset(self):
        return PurchaseOrder.objects.select_related('vendor', 'item').all().order_by('expected_delivery_date', '-id')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        orders = list(self.get_queryset())

        context['today'] = today
        context['total_orders_count'] = len(orders)

        # Suppliers / Companies for auto-completion datalist
        context['vendors'] = Contact.objects.filter(is_active=True).order_by('company_name')

        # Items catalog for core box suggestions
        context['items'] = Item.objects.filter(is_active=True).order_by('name')

        # Default suggested delivery date (today + 7 days)
        context['default_delivery_date'] = (today + datetime.timedelta(days=7)).isoformat()
        return context

    def post(self, request, *args, **kwargs):
        action = request.POST.get('action')

        # -------------------------------------------------------------
        # Action 1: Add New Company / Vendor Order
        # -------------------------------------------------------------
        if action == 'add_order':
            vendor_id = request.POST.get('vendor_id')
            company_name = (request.POST.get('company_name') or request.POST.get('vendor_name') or '').strip()
            item_id = request.POST.get('item_id')
            box_name = (request.POST.get('box_name') or request.POST.get('item_name') or request.POST.get('name') or '').strip()
            raw_quantity = request.POST.get('quantity')
            raw_weight = request.POST.get('weight')
            raw_delivery_date = request.POST.get('expected_delivery_date') or request.POST.get('delivery_date')

            # 1. Resolve or Create Vendor / Company
            vendor = None
            if vendor_id:
                vendor = Contact.objects.filter(pk=vendor_id).first()
            if not vendor and company_name:
                vendor = Contact.objects.filter(company_name__iexact=company_name).first()
                if not vendor:
                    vendor = Contact.objects.create(
                        type=ContactType.VENDOR,
                        company_name=company_name,
                        is_active=True
                    )

            if not vendor:
                messages.error(request, "Please enter or select a Company / Vendor name.")
                return redirect('vendor-order-list')

            # 2. Resolve Quantity
            if not raw_quantity:
                messages.error(request, "Please enter the quantity of core boxes needed.")
                return redirect('vendor-order-list')

            try:
                quantity = int(raw_quantity)
                if quantity <= 0:
                    raise ValueError
            except (ValueError, TypeError):
                messages.error(request, "Quantity must be a valid positive number.")
                return redirect('vendor-order-list')

            # 3. Resolve Core Box Item (with optional weight spec)
            item = get_or_resolve_production_item(item_id=item_id, item_name=box_name, weight=raw_weight)
            if raw_weight and item.weight == Decimal('0.000'):
                try:
                    w_val = Decimal(str(raw_weight).strip())
                    if w_val > Decimal('0'):
                        item.weight = w_val
                        item.save(update_fields=['weight', 'updated_at'])
                except (ValueError, TypeError):
                    pass

            # 4. Resolve Delivery Date
            today = timezone.localdate()
            expected_date = today + datetime.timedelta(days=7)
            if raw_delivery_date:
                try:
                    expected_date = datetime.date.fromisoformat(raw_delivery_date.strip())
                except (ValueError, TypeError):
                    pass

            # 5. Create Order
            order = PurchaseOrder.objects.create(
                vendor=vendor,
                item=item,
                quantity=quantity,
                expected_delivery_date=expected_date,
                status=PurchaseOrderStatus.PENDING
            )

            messages.success(
                request,
                f"Order registered: '{vendor.company_name}' requested {quantity} boxes of '{item.name}' "
                f"(Delivery Date: {order.expected_delivery_date.strftime('%b %d, %Y')})."
            )
            return redirect('vendor-order-list')

        # -------------------------------------------------------------
        # Action 2: Edit Existing Vendor Order
        # -------------------------------------------------------------
        elif action == 'edit_order':
            order_id = request.POST.get('order_id')
            company_name = (request.POST.get('company_name') or request.POST.get('vendor_name') or '').strip()
            box_name = (request.POST.get('box_name') or request.POST.get('item_name') or request.POST.get('name') or '').strip()
            raw_quantity = request.POST.get('quantity')
            raw_weight = request.POST.get('weight')
            raw_delivery_date = request.POST.get('expected_delivery_date') or request.POST.get('delivery_date')

            order = PurchaseOrder.objects.filter(pk=order_id).select_related('vendor', 'item').first()
            if not order:
                messages.error(request, "Order not found or already removed.")
                return redirect('vendor-order-list')

            # 1. Resolve or Create Vendor / Company
            if company_name:
                vendor = Contact.objects.filter(company_name__iexact=company_name).first()
                if not vendor:
                    vendor = Contact.objects.create(
                        type=ContactType.VENDOR,
                        company_name=company_name,
                        is_active=True
                    )
                order.vendor = vendor

            # 2. Resolve Quantity
            if raw_quantity:
                try:
                    quantity = int(raw_quantity)
                    if quantity <= 0:
                        raise ValueError
                    order.quantity = quantity
                except (ValueError, TypeError):
                    messages.error(request, "Quantity must be a valid positive number.")
                    return redirect('vendor-order-list')

            # 3. Resolve Box Item & Weight
            if box_name:
                item = get_or_resolve_production_item(item_name=box_name, weight=raw_weight)
                if raw_weight and item.weight == Decimal('0.000'):
                    try:
                        w_val = Decimal(str(raw_weight).strip())
                        if w_val > Decimal('0'):
                            item.weight = w_val
                            item.save(update_fields=['weight', 'updated_at'])
                    except (ValueError, TypeError):
                        pass
                order.item = item
            elif raw_weight:
                try:
                    w_val = Decimal(str(raw_weight).strip())
                    if w_val > Decimal('0') and order.item.weight != w_val:
                        order.item.weight = w_val
                        order.item.save(update_fields=['weight', 'updated_at'])
                except (ValueError, TypeError):
                    pass

            # 4. Resolve Delivery Date
            if raw_delivery_date:
                try:
                    order.expected_delivery_date = datetime.date.fromisoformat(raw_delivery_date.strip())
                except (ValueError, TypeError):
                    pass

            order.save()
            messages.success(
                request,
                f"Order updated: '{order.vendor.company_name}' &bull; {order.quantity} boxes of "
                f"'{order.item.name}'."
            )
            return redirect('vendor-order-list')

        # -------------------------------------------------------------
        # Action 3: Remove Line from Vendor Orders (Delivered or Cleared)
        # -------------------------------------------------------------
        elif action in ['remove_order', 'clear_order', 'delete_order', 'deliver_order']:
            order_id = request.POST.get('order_id')
            order = get_object_or_404(PurchaseOrder, pk=order_id)
            order_num = order.id
            company_title = order.vendor.company_name
            box_title = order.item.name
            qty = order.quantity
            order.delete()

            messages.success(
                request,
                f"Order #{order_num} for '{company_title}' ({qty} boxes of '{box_title}') has been removed from vendor orders."
            )
            return redirect('vendor-order-list')

        # Graceful fallback for add_vendor if invoked programmatically
        elif action == 'add_vendor':
            company_name = request.POST.get('company_name', '').strip()
            if company_name:
                Contact.objects.get_or_create(
                    type=ContactType.VENDOR,
                    company_name=company_name,
                    defaults={'is_active': True}
                )
                messages.success(request, f"Company '{company_name}' registered successfully.")
            return redirect('vendor-order-list')

        messages.warning(request, "Unrecognized action.")
        return redirect('vendor-order-list')


class AddInventoryView(LoginRequiredMixin, View):
    """Manage multi-weight core box inventory, stock additions, and record modifications."""
    template_name = 'inventory/add_inventory.html'

    def get(self, request, *args, **kwargs):
        items = Item.objects.filter(is_active=True).order_by('name', 'weight')
        vendors = Contact.objects.filter(type=ContactType.VENDOR, is_active=True).order_by('company_name')

        # Summary calculations for KPI badges (no total weight calculation)
        total_items_count = items.count()
        total_units = sum(item.current_stock for item in items)
        low_stock_count = sum(1 for item in items if item.current_stock < 10)

        return render(request, self.template_name, {
            'items': items,
            'total_items_count': total_items_count,
            'total_units': total_units,
            'low_stock_count': low_stock_count,
            'vendors': vendors,
        })

    def post(self, request, *args, **kwargs):
        action = request.POST.get('action', 'add_stock')

        # -------------------------------------------------------------
        # Action: Edit / Modify Product Details (Name, Quantity, Weight)
        # -------------------------------------------------------------
        if action == 'edit_product':
            item_id = request.POST.get('item_id')
            new_name = (request.POST.get('name') or request.POST.get('product_name') or '').strip()
            raw_quantity = request.POST.get('quantity')
            raw_weight = request.POST.get('weight')

            target_item = None
            if item_id:
                target_item = Item.objects.filter(pk=item_id).first()

            if not target_item:
                messages.error(request, "Product not found.")
                return redirect('add-inventory')

            if not new_name:
                messages.error(request, "Product name cannot be empty.")
                return redirect('add-inventory')

            if raw_quantity is None or str(raw_quantity).strip() == '':
                messages.error(request, "Please enter a valid Quantity.")
                return redirect('add-inventory')

            try:
                new_quantity = Decimal(str(raw_quantity).strip())
                if new_quantity < Decimal('0'):
                    raise ValueError
            except (ValueError, TypeError):
                messages.error(request, "Quantity cannot be negative.")
                return redirect('add-inventory')

            new_weight = None
            if raw_weight is not None and str(raw_weight).strip() != '':
                try:
                    new_weight = Decimal(str(raw_weight).strip())
                    if new_weight < Decimal('0'):
                        raise ValueError
                except (ValueError, TypeError):
                    messages.error(request, "Weight of the product (core box) cannot be negative.")
                    return redirect('add-inventory')

            with transaction.atomic():
                locked_item = Item.objects.select_for_update().get(pk=target_item.pk)
                old_stock = locked_item.current_stock

                stock_diff = new_quantity - old_stock

                if new_quantity == Decimal('0'):
                    if stock_diff != Decimal('0'):
                        InventoryLedger.objects.create(
                            item=locked_item,
                            quantity_changed=stock_diff,
                            transaction_type=TransactionType.OUT,
                            generic_reference_id='EDIT-CORRECTION',
                            unit_cost=locked_item.moving_average_cost,
                            total_cost=(abs(stock_diff) * locked_item.moving_average_cost).quantize(Decimal('0.01')),
                            narration="Quantity updated to 0. Stock completely removed from inventory."
                        )

                    locked_item.current_stock = Decimal('0')
                    locked_item.name = new_name
                    if new_weight is not None:
                        locked_item.weight = new_weight
                    try:
                        locked_item.delete()
                    except (ProtectedError, IntegrityError):
                        locked_item.is_active = False
                        locked_item.save(update_fields=['is_active', 'current_stock', 'name', 'updated_at'])

                    messages.success(
                        request,
                        f"Product '{locked_item.name}' stock was set to zero and has been completely removed from inventory."
                    )
                    return redirect('add-inventory')

                update_fields = ['name', 'current_stock', 'updated_at']
                locked_item.name = new_name
                if new_weight is not None:
                    locked_item.weight = new_weight
                    update_fields.append('weight')
                locked_item.current_stock = new_quantity
                if not locked_item.is_active and new_quantity > Decimal('0'):
                    locked_item.is_active = True
                    update_fields.append('is_active')

                locked_item.save(update_fields=update_fields)

                if stock_diff != Decimal('0'):
                    tx_type = TransactionType.IN if stock_diff > 0 else TransactionType.OUT
                    narration_text = (
                        f"Inventory record correction: adjusted stock from {old_stock} to {new_quantity} "
                        f"({'+' if stock_diff > 0 else ''}{stock_diff} units)."
                    )
                    InventoryLedger.objects.create(
                        item=locked_item,
                        quantity_changed=stock_diff,
                        transaction_type=tx_type,
                        generic_reference_id='EDIT-CORRECTION',
                        unit_cost=locked_item.moving_average_cost,
                        total_cost=(abs(stock_diff) * locked_item.moving_average_cost).quantize(Decimal('0.01')),
                        narration=narration_text
                    )

                messages.success(
                    request,
                    f"Successfully modified product '{locked_item.name}' "
                    f"(Quantity: {locked_item.current_stock} {locked_item.unit_of_measure})."
                )

            return redirect('add-inventory')

        # -------------------------------------------------------------
        # Action: Remove / Delete Complete Product (if stock is zero)
        # -------------------------------------------------------------
        if action in ['delete_product', 'remove_zero_stock']:
            item_id = request.POST.get('item_id')
            product_name = (request.POST.get('name') or request.POST.get('product_name') or '').strip()

            target_item = None
            if item_id:
                target_item = Item.objects.filter(pk=item_id).first()
            if not target_item and product_name:
                target_item = Item.objects.filter(name__iexact=product_name).first()

            if not target_item:
                messages.error(request, "Product not found.")
                return redirect('add-inventory')

            with transaction.atomic():
                locked_item = Item.objects.select_for_update().get(pk=target_item.pk)

                if locked_item.current_stock > Decimal('0'):
                    messages.error(
                        request,
                        f"Cannot remove product '{locked_item.name}'. "
                        f"Current stock is {locked_item.current_stock} {locked_item.unit_of_measure}. "
                        f"The stock must be zero to completely remove the product."
                    )
                    return redirect('add-inventory')

                item_name = locked_item.name
                item_sku = locked_item.sku
                item_weight = locked_item.weight

                # Remove completely from inventory: delete or deactivate
                try:
                    locked_item.delete()
                except (ProtectedError, IntegrityError):
                    locked_item.is_active = False
                    locked_item.current_stock = Decimal('0')
                    locked_item.save(update_fields=['is_active', 'current_stock', 'updated_at'])

                messages.success(
                    request,
                    f"Product '{item_name}' (SKU: {item_sku}) "
                    f"has been completely removed from inventory."
                )

            return redirect('add-inventory')

        # -------------------------------------------------------------
        # Action: Remove / Deduct Stock from Product
        # -------------------------------------------------------------
        if action == 'remove_stock':
            product_name = (request.POST.get('name') or request.POST.get('product_name') or '').strip()
            item_id = request.POST.get('item_id')
            raw_quantity = request.POST.get('quantity')
            raw_weight = request.POST.get('weight')
            reason = request.POST.get('reason', '').strip() or request.POST.get('narration', '').strip()

            target_item = None
            if item_id:
                target_item = Item.objects.filter(pk=item_id).first()
            if not target_item and product_name:
                weight_val = None
                if raw_weight:
                    try:
                        weight_val = Decimal(str(raw_weight).strip())
                    except (ValueError, TypeError):
                        pass
                if weight_val is not None:
                    target_item = Item.objects.filter(name__iexact=product_name, weight=weight_val).first()
                    if not target_item:
                        messages.error(
                            request,
                            f"No core box found for '{product_name}' with weight {weight_val} kg/box."
                        )
                        return redirect('add-inventory')
                else:
                    target_item = Item.objects.filter(name__iexact=product_name).first()

            if not target_item:
                messages.error(request, "Please select or enter a valid product to remove stock from.")
                return redirect('add-inventory')

            if not raw_quantity:
                messages.error(request, "Please specify the quantity to remove.")
                return redirect('add-inventory')

            try:
                quantity = Decimal(str(raw_quantity).strip())
                if quantity <= Decimal('0'):
                    raise ValueError
            except (ValueError, TypeError):
                messages.error(request, "Quantity to remove must be a valid positive number.")
                return redirect('add-inventory')

            with transaction.atomic():
                locked_item = Item.objects.select_for_update().get(pk=target_item.pk)

                if locked_item.current_stock < quantity:
                    messages.error(
                        request,
                        f"Cannot remove {quantity} units from '{locked_item.name}'. "
                        f"Only {locked_item.current_stock} {locked_item.unit_of_measure} available on hand."
                    )
                    return redirect('add-inventory')

                locked_item.current_stock -= quantity

                narration_text = reason or f"Stock deduction of {quantity} {locked_item.unit_of_measure}."
                InventoryLedger.objects.create(
                    item=locked_item,
                    quantity_changed=-quantity,
                    transaction_type=TransactionType.OUT,
                    generic_reference_id='STOCK-REMOVAL',
                    unit_cost=locked_item.moving_average_cost,
                    total_cost=(quantity * locked_item.moving_average_cost).quantize(Decimal('0.01')),
                    narration=narration_text
                )

                if locked_item.current_stock <= Decimal('0'):
                    locked_item.current_stock = Decimal('0')
                    try:
                        locked_item.delete()
                    except (ProtectedError, IntegrityError):
                        locked_item.is_active = False
                        locked_item.save(update_fields=['is_active', 'current_stock', 'updated_at'])

                    messages.success(
                        request,
                        f"Successfully removed -{quantity} units from '{locked_item.name}'. "
                        f"Stock reached zero and product has been completely removed from inventory."
                    )
                else:
                    locked_item.save(update_fields=['current_stock', 'updated_at'])
                    messages.success(
                        request,
                        f"Successfully removed -{quantity} units from '{locked_item.name}'. "
                        f"Remaining Stock: {locked_item.current_stock} {locked_item.unit_of_measure}."
                    )

            return redirect('add-inventory')

        # -------------------------------------------------------------
        # Action: Add Inventory Stock (Default)
        # -------------------------------------------------------------
        # 1. Name of the product
        product_name = (request.POST.get('name') or request.POST.get('product_name') or '').strip()
        # 2. Quantity
        raw_quantity = request.POST.get('quantity')
        # 3. Weight of the product (core box) - unit weight of one core box
        raw_weight = request.POST.get('weight')

        # Optional auxiliary inputs (graceful fallback/backward compatibility)
        item_id = request.POST.get('item_id')
        raw_unit_cost = request.POST.get('unit_cost', '0.00')
        vendor_id = request.POST.get('vendor_id')
        reference_id = request.POST.get('reference_id', '').strip()
        narration = request.POST.get('narration', '').strip()

        # If product_name wasn't supplied directly but item_id was, resolve item
        target_item = None
        if item_id:
            target_item = Item.objects.filter(pk=item_id).first()
            if target_item and not product_name:
                product_name = target_item.name

        # Validate Product Name
        if not product_name:
            messages.error(request, "Please enter the Name of the product.")
            return redirect('add-inventory')

        # Validate Quantity
        if not raw_quantity:
            messages.error(request, "Please enter the Quantity.")
            return redirect('add-inventory')
        try:
            quantity = Decimal(str(raw_quantity).strip())
            if quantity <= Decimal('0'):
                raise ValueError
        except (ValueError, TypeError):
            messages.error(request, "Please enter a valid positive Quantity.")
            return redirect('add-inventory')

        # Validate Weight of the product (core box)
        weight = None
        if raw_weight is not None and str(raw_weight).strip() != '':
            try:
                weight = Decimal(str(raw_weight).strip())
                if weight < Decimal('0'):
                    raise ValueError
            except (ValueError, TypeError):
                messages.error(request, "Weight of the product (core box) cannot be negative.")
                return redirect('add-inventory')

        # Optional unit cost
        try:
            unit_cost = Decimal(str(raw_unit_cost).strip()) if raw_unit_cost else Decimal('0.0000')
            if unit_cost < Decimal('0'):
                unit_cost = Decimal('0.0000')
        except (ValueError, TypeError):
            unit_cost = Decimal('0.0000')

        vendor = None
        if vendor_id:
            vendor = Contact.objects.filter(pk=vendor_id, type=ContactType.VENDOR).first()

        effective_weight = weight if weight is not None else Decimal('0.000')

        with transaction.atomic():
            # If item_id was given, check if user changed weight:
            # If weight changed, treat as a different core box product
            if item_id and target_item:
                if weight is not None and target_item.weight != effective_weight:
                    target_item = None

            # Look up existing product with the SAME name
            if not target_item:
                if weight is not None:
                    target_item = Item.objects.filter(name__iexact=product_name, weight=effective_weight).first()
                else:
                    target_item = Item.objects.filter(name__iexact=product_name, is_active=True).first()
                    if not target_item:
                        target_item = Item.objects.filter(name__iexact=product_name).first()

            if target_item:
                # Same product with same unit weight: increment stock
                locked_item = Item.objects.select_for_update().get(pk=target_item.pk)
                old_stock = locked_item.current_stock
                old_cost = locked_item.moving_average_cost
                new_stock = old_stock + quantity

                update_fields = ['current_stock', 'updated_at']
                if not locked_item.is_active:
                    locked_item.is_active = True
                    update_fields.append('is_active')

                # Recompute WAC if positive cost supplied
                if unit_cost > Decimal('0'):
                    old_total_val = old_stock * old_cost
                    new_purchase_val = quantity * unit_cost
                    new_wac = (old_total_val + new_purchase_val) / new_stock
                    locked_item.moving_average_cost = new_wac.quantize(Decimal('0.0001'))
                    update_fields.append('moving_average_cost')

                locked_item.current_stock = new_stock
                locked_item.save(update_fields=update_fields)

                default_narration = narration or f"Stock addition of {quantity} units for '{locked_item.name}'."
                effective_cost = unit_cost if unit_cost > 0 else locked_item.moving_average_cost

                InventoryLedger.objects.create(
                    item=locked_item,
                    quantity_changed=quantity,
                    transaction_type=TransactionType.IN,
                    contact=vendor,
                    generic_reference_id=reference_id or 'DIRECT-INWARD',
                    unit_cost=effective_cost,
                    total_cost=(quantity * effective_cost).quantize(Decimal('0.01')),
                    narration=default_narration
                )

                messages.success(
                    request,
                    f"Successfully updated '{locked_item.name}'! Added +{quantity} units. "
                    f"New On-Hand Stock: {locked_item.current_stock} {locked_item.unit_of_measure}."
                )
            else:
                # Same core box with different weight, or new core box product:
                # Stored separately as a distinct item!
                clean_slug = re.sub(r'[^A-Za-z0-9]+', '-', product_name).strip('-').upper()
                base_sku = f"CB-{clean_slug[:12]}" if clean_slug else "CB-PROD"
                sku = base_sku
                counter = 1
                while Item.objects.filter(sku=sku).exists():
                    sku = f"{base_sku}-{counter:02d}"
                    counter += 1

                new_item = Item.objects.create(
                    sku=sku,
                    name=product_name,
                    type=ItemType.FINISHED_GOOD,
                    unit_of_measure='pcs',
                    current_stock=quantity,
                    weight=effective_weight,
                    moving_average_cost=unit_cost
                )

                default_narration = narration or (
                    f"Created product '{new_item.name}' with {quantity} units."
                )
                InventoryLedger.objects.create(
                    item=new_item,
                    quantity_changed=quantity,
                    transaction_type=TransactionType.IN,
                    contact=vendor,
                    generic_reference_id=reference_id or 'NEW-PROD-INWARD',
                    unit_cost=unit_cost,
                    total_cost=(quantity * unit_cost).quantize(Decimal('0.01')),
                    narration=default_narration
                )

                messages.success(
                    request,
                    f"Created product '{new_item.name}' (SKU: {new_item.sku}) with {quantity} units."
                )

        return redirect('add-inventory')


class DailyCoreBoxListView(LoginRequiredMixin, View):
    """
    Main portal for companies taking core boxes daily.
    Displays quick add company form, daily entry form, and full directory of companies with totals.
    """
    template_name = 'inventory/daily_core_box_list.html'

    def get(self, request, *args, **kwargs):
        # Auto-sync companies from Contact catalog if Company table is empty
        if not Company.objects.exists():
            for contact in Contact.objects.all():
                if contact.company_name and contact.company_name.strip():
                    Company.objects.get_or_create(name=contact.company_name.strip())

        # Query active companies annotated with total core boxes taken and last dispatch date
        companies = Company.objects.filter(is_active=True).annotate(
            total_boxes=Coalesce(Sum('dispatches__quantity'), Value(0)),
            last_dispatch_date=Max('dispatches__dispatch_date'),
            dispatch_count=Count('dispatches')
        ).order_by('name')

        today = timezone.localdate()
        total_all_core_boxes = DailyCoreBoxDispatch.objects.aggregate(total=Sum('quantity'))['total'] or 0
        today_core_boxes = DailyCoreBoxDispatch.objects.filter(dispatch_date=today).aggregate(total=Sum('quantity'))['total'] or 0

        # Suggested core box items from catalog
        box_items = Item.objects.filter(is_active=True).order_by('name')

        context = {
            'companies': companies,
            'total_all_core_boxes': total_all_core_boxes,
            'today_core_boxes': today_core_boxes,
            'total_companies_count': companies.count(),
            'today': today,
            'box_items': box_items,
        }
        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        action = request.POST.get('action')

        # -------------------------------------------------------------
        # Action 1: Add Company Name
        # -------------------------------------------------------------
        if action == 'add_company':
            company_name = (request.POST.get('company_name') or '').strip()
            if not company_name:
                messages.error(request, "Company name cannot be empty.")
                return redirect('daily-core-box-list')

            existing = Company.objects.filter(name__iexact=company_name).first()
            if existing:
                if not existing.is_active:
                    existing.is_active = True
                    existing.save(update_fields=['is_active'])
                    messages.success(request, f"Re-activated company '{existing.name}'.")
                else:
                    messages.error(request, f"Company '{company_name}' already exists.")
                return redirect('daily-core-box-list')

            company = Company.objects.create(name=company_name)
            Contact.objects.get_or_create(company_name=company_name, defaults={'type': ContactType.CLIENT})
            messages.success(request, f"Company '{company.name}' added successfully!")
            return redirect('daily-core-box-list')

        # -------------------------------------------------------------
        # Action 2: Record Daily Core Box (Company, Box Type, Quantity, Save)
        # -------------------------------------------------------------
        if action == 'add_entry':
            company_id = request.POST.get('company_id')
            company_name = (request.POST.get('company_name') or '').strip()
            box_type = (request.POST.get('box_type') or request.POST.get('core_box_type') or '').strip()
            raw_quantity = request.POST.get('quantity')
            raw_date = request.POST.get('dispatch_date')

            # 1. Resolve Company
            company = None
            if company_id:
                company = Company.objects.filter(pk=company_id).first()
            if not company and company_name:
                company = Company.objects.filter(name__iexact=company_name).first()
                if not company:
                    company = Company.objects.create(name=company_name)
                    Contact.objects.get_or_create(company_name=company_name, defaults={'type': ContactType.CLIENT})

            if not company:
                messages.error(request, "Please provide or select a Company name.")
                return redirect('daily-core-box-list')

            # 2. Resolve Core Box Type
            if not box_type:
                box_type = 'Core Box'

            # 3. Resolve Quantity
            try:
                quantity = int(raw_quantity)
                if quantity <= 0:
                    raise ValueError
            except (ValueError, TypeError):
                messages.error(request, "Quantity must be a positive number greater than 0.")
                return redirect('daily-core-box-list')

            # 4. Resolve Date
            dispatch_date = timezone.localdate()
            if raw_date:
                try:
                    dispatch_date = datetime.date.fromisoformat(raw_date.strip())
                except (ValueError, TypeError):
                    pass

            # 5. Link with catalog Item if matching
            matched_item = Item.objects.filter(name__iexact=box_type, is_active=True).first()
            if not matched_item:
                matched_item = Item.objects.filter(sku__iexact=box_type, is_active=True).first()

            DailyCoreBoxDispatch.objects.create(
                company=company,
                box_type=box_type,
                item=matched_item,
                quantity=quantity,
                dispatch_date=dispatch_date
            )

            messages.success(
                request,
                f"Successfully recorded {quantity} boxes for {company.name} on {dispatch_date.strftime('%d %b %Y')}!"
            )
            return redirect('daily-core-box-list')

        # -------------------------------------------------------------
        # Action 3: Delete Company
        # -------------------------------------------------------------
        if action == 'delete_company':
            company_id = request.POST.get('company_id')
            if company_id:
                comp = Company.objects.filter(pk=company_id).first()
                if comp:
                    cname = comp.name
                    comp.delete()
                    messages.success(request, f"Company '{cname}' and its dispatches were successfully removed.")
            return redirect('daily-core-box-list')

        messages.warning(request, "Unknown action requested.")
        return redirect('daily-core-box-list')


class DailyCoreBoxDetailView(LoginRequiredMixin, View):
    """
    Detailed date-wise log of all core boxes taken by a specific company.
    Features top metric card with total core boxes taken, type breakdown, and date-wise entry table.
    """
    template_name = 'inventory/daily_core_box_detail.html'

    def get(self, request, pk, *args, **kwargs):
        company = get_object_or_404(Company, pk=pk)

        # Date-wise entries ordered by latest first
        dispatches = company.dispatches.select_related('item').all().order_by('-dispatch_date', '-id')

        # Total core boxes taken (for the top card)
        total_core_boxes = dispatches.aggregate(total=Sum('quantity'))['total'] or 0

        # Unique dispatch days
        total_days = dispatches.values('dispatch_date').distinct().count()

        # Breakdown by core box type
        box_breakdown = dispatches.values('box_type').annotate(
            total_qty=Sum('quantity'),
            entries_count=Count('id')
        ).order_by('-total_qty')

        # Available catalog items for autocomplete
        box_items = Item.objects.filter(is_active=True).order_by('name')

        context = {
            'company': company,
            'dispatches': dispatches,
            'total_core_boxes': total_core_boxes,
            'total_days': total_days,
            'box_breakdown': box_breakdown,
            'box_items': box_items,
            'today': timezone.localdate(),
        }
        return render(request, self.template_name, context)

    def post(self, request, pk, *args, **kwargs):
        company = get_object_or_404(Company, pk=pk)
        action = request.POST.get('action')

        # -------------------------------------------------------------
        # Action 1: Add Entry Directly for this Company
        # -------------------------------------------------------------
        if action == 'add_entry':
            box_type = (request.POST.get('box_type') or request.POST.get('core_box_type') or '').strip()
            raw_quantity = request.POST.get('quantity')
            raw_date = request.POST.get('dispatch_date')

            if not box_type:
                messages.error(request, "Please enter or select the Core Box Type.")
                return redirect('daily-core-box-detail', pk=company.pk)

            try:
                quantity = int(raw_quantity)
                if quantity <= 0:
                    raise ValueError
            except (ValueError, TypeError):
                messages.error(request, "Quantity must be a positive number greater than 0.")
                return redirect('daily-core-box-detail', pk=company.pk)

            dispatch_date = timezone.localdate()
            if raw_date:
                try:
                    dispatch_date = datetime.date.fromisoformat(raw_date.strip())
                except (ValueError, TypeError):
                    pass

            matched_item = Item.objects.filter(name__iexact=box_type, is_active=True).first()
            if not matched_item:
                matched_item = Item.objects.filter(sku__iexact=box_type, is_active=True).first()

            DailyCoreBoxDispatch.objects.create(
                company=company,
                box_type=box_type,
                item=matched_item,
                quantity=quantity,
                dispatch_date=dispatch_date
            )

            messages.success(
                request,
                f"Recorded {quantity} boxes of '{box_type}' on {dispatch_date.strftime('%d %b %Y')}."
            )
            return redirect('daily-core-box-detail', pk=company.pk)

        # -------------------------------------------------------------
        # Action 2: Edit Existing Dispatch Entry
        # -------------------------------------------------------------
        if action == 'edit_entry':
            entry_id = request.POST.get('entry_id')
            box_type = (request.POST.get('box_type') or '').strip()
            raw_quantity = request.POST.get('quantity')
            raw_date = request.POST.get('dispatch_date')

            dispatch = get_object_or_404(DailyCoreBoxDispatch, pk=entry_id, company=company)

            if not box_type:
                messages.error(request, "Core box type cannot be empty.")
                return redirect('daily-core-box-detail', pk=company.pk)

            try:
                quantity = int(raw_quantity)
                if quantity <= 0:
                    raise ValueError
            except (ValueError, TypeError):
                messages.error(request, "Quantity must be a positive number greater than 0.")
                return redirect('daily-core-box-detail', pk=company.pk)

            dispatch_date = dispatch.dispatch_date
            if raw_date:
                try:
                    dispatch_date = datetime.date.fromisoformat(raw_date.strip())
                except (ValueError, TypeError):
                    pass

            matched_item = Item.objects.filter(name__iexact=box_type, is_active=True).first()
            if not matched_item:
                matched_item = Item.objects.filter(sku__iexact=box_type, is_active=True).first()

            dispatch.box_type = box_type
            dispatch.quantity = quantity
            dispatch.dispatch_date = dispatch_date
            dispatch.item = matched_item
            dispatch.save()

            messages.success(request, f"Updated entry for {dispatch_date.strftime('%d %b %Y')}.")
            return redirect('daily-core-box-detail', pk=company.pk)

        # -------------------------------------------------------------
        # Action 3: Delete Entry
        # -------------------------------------------------------------
        if action == 'delete_entry':
            entry_id = request.POST.get('entry_id')
            dispatch = get_object_or_404(DailyCoreBoxDispatch, pk=entry_id, company=company)
            dispatch.delete()
            messages.success(request, "Dispatch entry successfully removed.")
            return redirect('daily-core-box-detail', pk=company.pk)

        # -------------------------------------------------------------
        # Action 4: Edit Company Name
        # -------------------------------------------------------------
        if action == 'edit_company':
            new_name = (request.POST.get('company_name') or '').strip()
            if not new_name:
                messages.error(request, "Company name cannot be empty.")
                return redirect('daily-core-box-detail', pk=company.pk)

            if Company.objects.filter(name__iexact=new_name).exclude(pk=company.pk).exists():
                messages.error(request, f"Another company with name '{new_name}' already exists.")
                return redirect('daily-core-box-detail', pk=company.pk)

            company.name = new_name
            company.save(update_fields=['name', 'updated_at'])
            messages.success(request, f"Company renamed to '{company.name}'.")
            return redirect('daily-core-box-detail', pk=company.pk)

        messages.warning(request, "Unknown action requested.")
        return redirect('daily-core-box-detail', pk=company.pk)


