from decimal import Decimal
from typing import Any, Dict

from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView
from django.utils import timezone

from inventory.models import Item
from dashboard.services import FinancialDashboardService


class DashboardHomeView(LoginRequiredMixin, TemplateView):
    """Display current-month financial metrics, revenue analytics, and low-stock warnings."""
    template_name = 'dashboard/home.html'

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        context = super().get_context_data(**kwargs)

        today = timezone.localdate()
        month_start = today.replace(day=1)

        # Retrieve current-month financial metrics from service layer
        financial_metrics = FinancialDashboardService.get_financial_metrics(
            start_date=month_start,
            end_date=today
        )

        # Query items with current stock strictly below 10 units
        low_stock_alerts = list(
            Item.objects.filter(current_stock__lt=Decimal('10.0000'))
            .order_by('current_stock', 'name')
        )

        # Real trailing 6-month revenue / COGS trend for the chart
        sales_trend = FinancialDashboardService.get_monthly_trend(months=6, today=today)

        context.update({
            'financial_metrics': financial_metrics,
            'total_revenue': financial_metrics['total_revenue'],
            'total_cogs': financial_metrics['total_cogs'],
            'gross_profit': financial_metrics['gross_profit'],
            'total_inventory_value': financial_metrics['total_inventory_value'],
            'low_stock_alerts': low_stock_alerts,
            'low_stock_count': len(low_stock_alerts),
            'sales_trend': sales_trend,
            'current_month_name': today.strftime('%B %Y'),
            'today': today,
        })
        return context
