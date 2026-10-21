from decimal import Decimal
from typing import Dict, Any, Optional
from datetime import date, datetime

from django.db import models
from django.db.models import Sum, F, DecimalField
from django.db.models.functions import Abs

from inventory.models import Item, ItemType
from transactions.models import InventoryLedger, TransactionType


class FinancialDashboardService:
    """
    Analytics and managerial accounting service:
    Calculates executive KPIs including Gross Revenue, Cost of Goods Sold (COGS),
    Gross Profit, and Total Inventory Valuation on hand.
    """

    @classmethod
    def get_financial_metrics(
        cls,
        start_date: Optional[Any] = None,
        end_date: Optional[Any] = None
    ) -> Dict[str, Decimal]:
        """
        Calculates financial performance metrics over a date range:
        - total_revenue: Total monetary value of fulfilled sales orders.
        - total_cogs: Total cost of finished goods sold based on moving average cost at dispatch.
        - gross_profit: total_revenue - total_cogs.
        - total_inventory_value: Real-time balance sheet value of all inventory on hand.

        Returns Decimal values defaulted to Decimal('0.00') if no records match.
        """
        # Query InventoryLedger filtering by 'Out' transactions representing commercial sales
        sales_qs = InventoryLedger.objects.filter(
            transaction_type=TransactionType.OUT
        ).filter(
            models.Q(selling_price__gt=Decimal('0.0000')) | models.Q(contact__isnull=False)
        )

        # Apply temporal filtering if start_date / end_date are provided
        if start_date is not None:
            if hasattr(start_date, 'hour'):
                sales_qs = sales_qs.filter(timestamp__gte=start_date)
            else:
                sales_qs = sales_qs.filter(timestamp__date__gte=start_date)

        if end_date is not None:
            if hasattr(end_date, 'hour'):
                sales_qs = sales_qs.filter(timestamp__lte=end_date)
            else:
                sales_qs = sales_qs.filter(timestamp__date__lte=end_date)

        # Aggregate Revenue and COGS using Sum and F expressions
        sales_aggregation = sales_qs.aggregate(
            revenue_sum=Sum(
                Abs(F('quantity_changed')) * F('selling_price'),
                output_field=DecimalField(max_digits=18, decimal_places=4)
            ),
            cogs_sum=Sum(
                Abs(F('quantity_changed')) * F('unit_cost'),
                output_field=DecimalField(max_digits=18, decimal_places=4)
            )
        )

        raw_revenue = sales_aggregation.get('revenue_sum')
        total_revenue = Decimal('0.00') if raw_revenue is None else raw_revenue.quantize(Decimal('0.01'))

        raw_cogs = sales_aggregation.get('cogs_sum')
        total_cogs = Decimal('0.00') if raw_cogs is None else raw_cogs.quantize(Decimal('0.01'))

        gross_profit = (total_revenue - total_cogs).quantize(Decimal('0.01'))

        # Aggregate current on-hand inventory valuation across all items
        inventory_aggregation = Item.objects.aggregate(
            inventory_val_sum=Sum(
                F('current_stock') * F('moving_average_cost'),
                output_field=DecimalField(max_digits=18, decimal_places=4)
            )
        )

        raw_inv_val = inventory_aggregation.get('inventory_val_sum')
        total_inventory_value = Decimal('0.00') if raw_inv_val is None else raw_inv_val.quantize(Decimal('0.01'))

        return {
            'total_revenue': total_revenue,
            'total_cogs': total_cogs,
            'gross_profit': gross_profit,
            'total_inventory_value': total_inventory_value,
        }
