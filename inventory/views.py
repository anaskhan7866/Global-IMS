import datetime
import re
from decimal import Decimal
from django.db import transaction
from django.db.models import Count, Sum, Q, Value
from django.db.models.functions import Coalesce
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.views import View
from django.views.generic import ListView, DetailView

from inventory.models import Employee, ProductionLog, Item, ItemType
from transactions.models import PurchaseOrder, Contact, ContactType, InventoryLedger, TransactionType


def get_or_resolve_production_item(item_id=None):
    """
    Resolves target manufactured item (Finished Good), defaulting gracefully if unset.
    """
    from inventory.models import ItemType
    if item_id:
        found = Item.objects.filter(pk=item_id).first()
        if found:
            return found

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
        moving_average_cost=Decimal('0.0000')
    )


class EmployeeListView(ListView):
    """
    Fetches all active employees and calculates their manufacturing productivity
    for the current calendar month and today using Count and Sum annotations.
    Handles adding employees, logging end-of-day output per employee, bulk logs, and deactivation.
    """
    model = Employee
    context_object_name = 'employees'
    template_name = 'inventory/employee_list.html'

    def get_queryset(self):
        now = timezone.now()
        current_year = now.year
        current_month = now.month
        today = now.date()

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
        now = timezone.now()
        today = now.date()
        context['current_month'] = now.month
        context['current_year'] = now.year
        context['today'] = today
        context['today_str'] = now.strftime('%A, %b %d, %Y')
        context['current_month_name'] = now.strftime('%B %Y')

        # Catalog finished goods for box logging
        from inventory.models import ItemType
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
            item_id = request.POST.get('item_id')

            emp = get_object_or_404(Employee, pk=employee_id)

            try:
                quantity = int(raw_quantity)
                if quantity <= 0:
                    raise ValueError
            except (ValueError, TypeError):
                messages.error(request, f"Please enter a valid positive number of boxes for {emp.full_name}.")
                return redirect('employee-list')

            item = get_or_resolve_production_item(item_id)
            ProductionLog.objects.create(
                employee=emp,
                item=item,
                quantity=quantity
            )

            messages.success(
                request,
                f"Saved {quantity} boxes for {emp.full_name} today! ({item.name})"
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
                                    quantity=qty
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

        # 4. Delete / Deactivate Employee
        elif action == 'deactivate' or action == 'delete':
            employee_id = request.POST.get('employee_id')
            emp = get_object_or_404(Employee, pk=employee_id)
            emp_name = emp.full_name
            try:
                emp.delete()
            except Exception:
                emp.is_active = False
                emp.save(update_fields=['is_active'])
            messages.success(request, f"Employee '{emp_name}' has been deleted.")
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
    Inventory management view:
    1. Simple, direct form asking:
       - Name of the product
       - Quantity
       - Weight of the product (core box)
    2. Comprehensive table displaying all details of all products:
       - Product Name & SKU
       - Quantity / Current Stock
       - Unit Weight of the Product (Core Box)
       - Total Stock Weight
       - Moving Average Cost / Status
    """
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

            new_weight = Decimal('0.000')
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
                old_name = locked_item.name
                old_stock = locked_item.current_stock
                old_weight = locked_item.weight

                stock_diff = new_quantity - old_stock

                update_fields = ['name', 'weight', 'current_stock', 'updated_at']
                locked_item.name = new_name
                locked_item.weight = new_weight
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
                    f"(Quantity: {locked_item.current_stock} {locked_item.unit_of_measure}, Weight: {locked_item.weight} kg/box)."
                )

            return redirect('add-inventory')

        # -------------------------------------------------------------
        # Action: Remove / Delete Complete Product (if stock is zero)
        # -------------------------------------------------------------
        if action == 'delete_product':
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
                        f"Cannot remove product '{locked_item.name}'. Current stock is {locked_item.current_stock} {locked_item.unit_of_measure}. "
                        f"The stock must be zero to completely remove the product."
                    )
                    return redirect('add-inventory')

                item_name = locked_item.name
                item_sku = locked_item.sku
                item_weight = locked_item.weight

                # If protected by immutable audit ledgers, soft-delete via is_active=False
                try:
                    locked_item.delete()
                except Exception:
                    locked_item.is_active = False
                    locked_item.save(update_fields=['is_active', 'updated_at'])

                messages.success(
                    request,
                    f"Product '{item_name}' (Weight: {item_weight} kg/box, SKU: {item_sku}) has been completely removed from inventory."
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
                        f"Cannot remove {quantity} units from '{locked_item.name}' ({locked_item.weight} kg/box). "
                        f"Only {locked_item.current_stock} {locked_item.unit_of_measure} available on hand."
                    )
                    return redirect('add-inventory')

                locked_item.current_stock -= quantity
                locked_item.save(update_fields=['current_stock', 'updated_at'])

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

                messages.success(
                    request,
                    f"Successfully removed -{quantity} units from '{locked_item.name}' ({locked_item.weight} kg/box). "
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

            # Look up existing product with the SAME name AND the SAME weight of one core box
            if not target_item:
                target_item = Item.objects.filter(name__iexact=product_name, weight=effective_weight).first()

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

                weight_info = f" | Weight (One Core Box): {locked_item.weight} kg" if locked_item.weight else ""
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
                    f"New On-Hand Stock: {locked_item.current_stock} {locked_item.unit_of_measure}{weight_info}"
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

                default_narration = narration or f"Created distinct core box '{new_item.name}' (Weight: {effective_weight} kg/box) with {quantity} units."
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
                    f"Created distinct product '{new_item.name}' (Weight: {effective_weight} kg/box, SKU: {new_item.sku}) with {quantity} units."
                )

        return redirect('add-inventory')

