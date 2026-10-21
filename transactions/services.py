from decimal import Decimal
from typing import Dict, Any, Optional

from django.db import transaction
from django.core.exceptions import ValidationError

from inventory.models import Item, BillOfMaterials, BOMComponent, ItemType, Employee, ProductionLog
from transactions.models import Contact, ContactType, InventoryLedger, TransactionType
from transactions.exceptions import InsufficientStockError, BOMNotFoundError


@transaction.atomic
def process_purchase_order(
    vendor: Contact,
    item: Item,
    quantity: Decimal,
    unit_price: Decimal,
    reference_id: str = "",
    timestamp: Optional[Any] = None
) -> InventoryLedger:
    """
    Executes a procurement receipt:
    1. Locks the item row using select_for_update() to prevent concurrent stock corruption.
    2. Recalculates Moving Weighted Average Cost (WAC) based on existing stock valuation + new purchase value:
       WAC = (old_stock * old_cost + new_qty * unit_price) / (old_stock + new_qty)
    3. Increases the item's physical current_stock.
    4. Records an immutable 'In' entry in InventoryLedger.
    """
    quantity = Decimal(str(quantity))
    unit_price = Decimal(str(unit_price))

    if quantity <= Decimal('0'):
        raise ValidationError("Purchase quantity must be strictly greater than zero.")
    if unit_price < Decimal('0'):
        raise ValidationError("Purchase unit price cannot be negative.")

    # Pessimistic row-level locking on Item
    locked_item = Item.objects.select_for_update().get(pk=item.pk)

    old_stock = locked_item.current_stock
    old_cost = locked_item.moving_average_cost
    old_total_value = old_stock * old_cost
    new_purchase_value = quantity * unit_price
    new_stock = old_stock + quantity

    # Calculate Moving Weighted Average Cost
    if new_stock > Decimal('0'):
        new_wac = (old_total_value + new_purchase_value) / new_stock
    else:
        new_wac = unit_price

    locked_item.current_stock = new_stock
    locked_item.moving_average_cost = new_wac.quantize(Decimal('0.0001'))
    locked_item.save(update_fields=['current_stock', 'moving_average_cost', 'updated_at'])

    ledger_kwargs = {
        'item': locked_item,
        'quantity_changed': quantity,
        'transaction_type': TransactionType.IN,
        'contact': vendor,
        'generic_reference_id': reference_id,
        'unit_cost': unit_price,
        'total_cost': new_purchase_value,
        'narration': f"Purchase receipt from {vendor.company_name} (PO Ref: {reference_id or 'N/A'})"
    }
    if timestamp:
        ledger_kwargs['timestamp'] = timestamp

    # Append-only ledger recording
    ledger_entry = InventoryLedger.objects.create(**ledger_kwargs)

    return ledger_entry


@transaction.atomic
def process_manufacturing_order(
    finished_good: Item,
    quantity_to_build: Decimal,
    reference_id: str = "",
    employee: Optional[Employee] = None
) -> Dict[str, Any]:
    """
    Executes a discrete production work order:
    1. Validates and resolves the active Bill of Materials (BOM) for the finished good.
    2. Calculates required raw material quantities (including scrap allowance).
    3. Acquires row-level locks on all raw materials in deterministic order to prevent deadlocks.
    4. Asserts stock sufficiency for all required raw materials, raising InsufficientStockError if short.
    5. Deducts required raw material stock and logs 'Out' consumption entries in InventoryLedger.
    6. Increases finished good stock, updates finished good Moving Average Cost with actual production cost,
       and logs a 'Manufactured' entry in InventoryLedger.
    7. Creates a ProductionLog record for the assigned employee operator.
    """
    quantity_to_build = Decimal(str(quantity_to_build))
    if quantity_to_build <= Decimal('0'):
        raise ValidationError("Manufacturing quantity to build must be strictly greater than zero.")

    if finished_good.type != ItemType.FINISHED_GOOD:
        raise ValidationError(
            f"Item '{finished_good.name}' (SKU: {finished_good.sku}) is not classified as a Finished Good."
        )

    # Resolve active BOM
    bom = BillOfMaterials.objects.filter(
        finished_good=finished_good,
        is_active=True
    ).prefetch_related('components__raw_material').first()

    if not bom:
        raise BOMNotFoundError(
            f"No active Bill of Materials found for finished good '{finished_good.name}' (SKU: {finished_good.sku})."
        )

    components = list(bom.components.select_related('raw_material').all())
    if not components:
        raise ValidationError(
            f"Bill of Materials for '{finished_good.name}' does not contain any active components."
        )

    # Compute gross requirement for each component (including scrap factor)
    required_quantities: Dict[int, Decimal] = {}
    for comp in components:
        scrap_multiplier = Decimal('1') + (comp.scrap_percentage / Decimal('100'))
        needed = (comp.quantity * quantity_to_build * scrap_multiplier).quantize(Decimal('0.0001'))
        required_quantities[comp.raw_material_id] = needed

    # Lock raw materials in sorted ID order to guarantee deadlock freedom
    sorted_material_ids = sorted(required_quantities.keys())
    locked_materials = {
        item.id: item
        for item in Item.objects.select_for_update().filter(id__in=sorted_material_ids)
    }

    # Verify inventory availability across all required raw materials
    for comp in components:
        rm = locked_materials[comp.raw_material_id]
        needed = required_quantities[comp.raw_material_id]
        if rm.current_stock < needed:
            raise InsufficientStockError(
                f"Insufficient stock for raw material '{rm.name}' (SKU: {rm.sku}). "
                f"Required: {needed:.4f} {rm.unit_of_measure}, Available: {rm.current_stock:.4f} {rm.unit_of_measure}",
                item_sku=rm.sku,
                required_qty=needed,
                available_qty=rm.current_stock
            )

    # Deduct raw material inventory and record consumption in ledger
    total_production_cost = Decimal('0.0000')
    consumed_records = []

    for comp in components:
        rm = locked_materials[comp.raw_material_id]
        needed = required_quantities[comp.raw_material_id]
        cost_contribution = (needed * rm.moving_average_cost).quantize(Decimal('0.0001'))
        total_production_cost += cost_contribution

        rm.current_stock -= needed
        rm.save(update_fields=['current_stock', 'updated_at'])

        ledger_out = InventoryLedger.objects.create(
            item=rm,
            quantity_changed=-needed,
            transaction_type=TransactionType.OUT,
            unit_cost=rm.moving_average_cost,
            total_cost=cost_contribution,
            generic_reference_id=reference_id,
            narration=(
                f"Consumed {needed:.4f} {rm.unit_of_measure} for manufacturing {quantity_to_build} "
                f"{finished_good.name} (WO Ref: {reference_id or 'N/A'})"
            )
        )
        consumed_records.append({
            'raw_material': rm,
            'quantity_consumed': needed,
            'unit_cost': rm.moving_average_cost,
            'total_cost': cost_contribution,
            'ledger_id': ledger_out.id
        })

    # Lock and update Finished Good inventory
    fg_locked = Item.objects.select_for_update().get(pk=finished_good.pk)
    unit_production_cost = (total_production_cost / quantity_to_build).quantize(Decimal('0.0001'))

    old_fg_stock = fg_locked.current_stock
    old_fg_val = old_fg_stock * fg_locked.moving_average_cost
    new_fg_stock = old_fg_stock + quantity_to_build

    if new_fg_stock > Decimal('0'):
        new_fg_wac = ((old_fg_val + total_production_cost) / new_fg_stock).quantize(Decimal('0.0001'))
    else:
        new_fg_wac = unit_production_cost

    fg_locked.current_stock = new_fg_stock
    fg_locked.moving_average_cost = new_fg_wac
    fg_locked.save(update_fields=['current_stock', 'moving_average_cost', 'updated_at'])

    # Record finished good production inward entry
    fg_ledger = InventoryLedger.objects.create(
        item=fg_locked,
        quantity_changed=quantity_to_build,
        transaction_type=TransactionType.MANUFACTURED,
        unit_cost=unit_production_cost,
        total_cost=total_production_cost,
        generic_reference_id=reference_id,
        narration=f"Manufactured {quantity_to_build} {fg_locked.name} via BOM v{bom.version} (WO Ref: {reference_id or 'N/A'})"
    )

    # Record workforce productivity log if an employee is specified
    production_log = None
    if employee is not None:
        production_log = ProductionLog.objects.create(
            employee=employee,
            item=fg_locked,
            quantity=int(quantity_to_build)
        )

    return {
        'finished_good': fg_locked,
        'quantity_built': quantity_to_build,
        'unit_production_cost': unit_production_cost,
        'total_production_cost': total_production_cost,
        'consumed_components': consumed_records,
        'output_ledger_id': fg_ledger.id,
        'production_log': production_log,
    }


@transaction.atomic
def process_sales_order(
    client: Contact,
    item: Item,
    quantity: Decimal,
    selling_price: Decimal,
    reference_id: str = "",
    timestamp: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Executes a sales order fulfillment & stock dispatch:
    1. Locks the item row using select_for_update().
    2. Validates stock availability, raising InsufficientStockError if quantity exceeds on-hand balance.
    3. Deducts the physical current_stock.
    4. Computes COGS based on current moving_average_cost and gross revenue based on selling_price.
    5. Records an immutable 'Out' entry in InventoryLedger.
    6. Returns a dictionary containing financial metrics: revenue, cogs, gross_profit, and remaining_stock.
    """
    quantity = Decimal(str(quantity))
    selling_price = Decimal(str(selling_price))

    if quantity <= Decimal('0'):
        raise ValidationError("Sales order quantity must be strictly greater than zero.")
    if selling_price < Decimal('0'):
        raise ValidationError("Selling price cannot be negative.")

    # Pessimistic row-level locking on Item
    locked_item = Item.objects.select_for_update().get(pk=item.pk)

    if locked_item.current_stock < quantity:
        raise InsufficientStockError(
            f"Insufficient stock for '{locked_item.name}' (SKU: {locked_item.sku}). "
            f"Requested: {quantity:.4f} {locked_item.unit_of_measure}, Available: {locked_item.current_stock:.4f} {locked_item.unit_of_measure}",
            item_sku=locked_item.sku,
            required_qty=quantity,
            available_qty=locked_item.current_stock
        )

    # Cost of Goods Sold (COGS) and Revenue computation
    cogs = (quantity * locked_item.moving_average_cost).quantize(Decimal('0.0001'))
    revenue = (quantity * selling_price).quantize(Decimal('0.0001'))
    gross_profit = (revenue - cogs).quantize(Decimal('0.0001'))

    # Deduct stock
    locked_item.current_stock -= quantity
    locked_item.save(update_fields=['current_stock', 'updated_at'])

    ledger_kwargs = {
        'item': locked_item,
        'quantity_changed': -quantity,
        'transaction_type': TransactionType.OUT,
        'contact': client,
        'generic_reference_id': reference_id,
        'unit_cost': locked_item.moving_average_cost,
        'selling_price': selling_price,
        'total_cost': cogs,
        'narration': f"Dispatched {quantity:.4f} {locked_item.unit_of_measure} to {client.company_name} (SO Ref: {reference_id or 'N/A'})"
    }
    if timestamp:
        ledger_kwargs['timestamp'] = timestamp

    # Append-only ledger recording
    ledger_entry = InventoryLedger.objects.create(**ledger_kwargs)

    return {
        'revenue': revenue,
        'cogs': cogs,
        'gross_profit': gross_profit,
        'quantity': quantity,
        'unit_selling_price': selling_price,
        'unit_cost': locked_item.moving_average_cost,
        'remaining_stock': locked_item.current_stock,
        'ledger_id': ledger_entry.id
    }
