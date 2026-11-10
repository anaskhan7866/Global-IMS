from decimal import Decimal
from typing import Dict, Any, Optional

from django.db import models
from django.db.models import Sum, F, DecimalField
from django.db.models.functions import Abs, TruncMonth
from django.utils import timezone

from inventory.models import Item
from transactions.models import InventoryLedger, TransactionType


class FinancialDashboardService:
    """Compute top-line financial performance metrics and inventory valuation."""

    @classmethod
    def get_financial_metrics(
        cls,
        start_date: Optional[Any] = None,
        end_date: Optional[Any] = None
    ) -> Dict[str, Decimal]:
        """Aggregate revenue, cost of goods sold, profit, and stock valuation for a date window."""
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

    @classmethod
    def get_monthly_trend(cls, months: int = 6, today: Optional[Any] = None) -> Dict[str, list]:
        """
        Revenue and COGS for each of the trailing `months` calendar months (oldest first),
        computed from real sales ledger entries in one grouped query. Months without sales
        are reported as zero.
        """
        today = today or timezone.localdate()

        # First day of each month in the window, oldest first
        month_starts = []
        year, month = today.year, today.month
        for _ in range(months):
            month_starts.append(today.replace(year=year, month=month, day=1))
            month -= 1
            if month == 0:
                year, month = year - 1, 12
        month_starts.reverse()

        rows = (
            InventoryLedger.objects.filter(
                transaction_type=TransactionType.OUT,
                timestamp__date__gte=month_starts[0],
                timestamp__date__lte=today,
            )
            .filter(models.Q(selling_price__gt=Decimal('0.0000')) | models.Q(contact__isnull=False))
            .annotate(month=TruncMonth('timestamp'))
            .values('month')
            .annotate(
                revenue=Sum(
                    Abs(F('quantity_changed')) * F('selling_price'),
                    output_field=DecimalField(max_digits=18, decimal_places=4)
                ),
                cogs=Sum(
                    Abs(F('quantity_changed')) * F('unit_cost'),
                    output_field=DecimalField(max_digits=18, decimal_places=4)
                ),
            )
        )
        by_month = {}
        for row in rows:
            m = row['month']
            if hasattr(m, 'hour') and timezone.is_aware(m):
                m = timezone.localtime(m)
            by_month[(m.year, m.month)] = row

        labels, revenue, cogs = [], [], []
        for start in month_starts:
            row = by_month.get((start.year, start.month), {})
            labels.append(start.strftime('%b %Y'))
            revenue.append(float((row.get('revenue') or Decimal('0')).quantize(Decimal('0.01'))))
            cogs.append(float((row.get('cogs') or Decimal('0')).quantize(Decimal('0.01'))))
        return {'labels': labels, 'revenue': revenue, 'cogs': cogs}
