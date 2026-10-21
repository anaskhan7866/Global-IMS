from decimal import Decimal
from datetime import timedelta
from django.test import TestCase
from django.utils import timezone

from inventory.models import Item, ItemType, UnitOfMeasure
from transactions.models import Contact, ContactType, InventoryLedger, TransactionType
from transactions.services import process_purchase_order, process_sales_order
from dashboard.services import FinancialDashboardService


class FinancialDashboardServiceTestCase(TestCase):
    """
    Tests financial analytics calculations:
    - total_revenue, total_cogs, gross_profit, and total_inventory_value aggregations.
    - Default handling when database has zero matching records (None -> Decimal('0.00')).
    - Date filtering behavior across financial periods.
    """

    def setUp(self):
        self.vendor = Contact.objects.create(
            type=ContactType.VENDOR,
            company_name='Premier Steels Ltd',
            GST_number='07AABCP1122D1Z3'
        )
        self.client = Contact.objects.create(
            type=ContactType.CLIENT,
            company_name='Metro Infra Corp',
            GST_number='27AABCM3344E1Z7'
        )

    def test_empty_database_defaults_to_zero(self):
        """
        Asserts that an empty database or non-matching date range returns Decimal('0.00') for all metrics.
        """
        metrics = FinancialDashboardService.get_financial_metrics()

        self.assertEqual(metrics['total_revenue'], Decimal('0.00'))
        self.assertEqual(metrics['total_cogs'], Decimal('0.00'))
        self.assertEqual(metrics['gross_profit'], Decimal('0.00'))
        self.assertEqual(metrics['total_inventory_value'], Decimal('0.00'))

    def test_inventory_valuation_aggregation(self):
        """
        Asserts total_inventory_value aggregates sum(current_stock * moving_average_cost) across all items.
        """
        Item.objects.create(
            type=ItemType.RAW_MATERIAL,
            sku='RM-ALUM-01',
            name='Aluminum Ingot',
            unit_of_measure=UnitOfMeasure.KILOGRAM,
            current_stock=Decimal('100.0000'),
            moving_average_cost=Decimal('4.5000')  # 100 * 4.5 = $450.00
        )
        Item.objects.create(
            type=ItemType.FINISHED_GOOD,
            sku='FG-PANEL-01',
            name='Solar Mounting Panel',
            unit_of_measure=UnitOfMeasure.PIECE,
            current_stock=Decimal('20.0000'),
            moving_average_cost=Decimal('35.0000')  # 20 * 35 = $700.00
        )

        metrics = FinancialDashboardService.get_financial_metrics()
        self.assertEqual(metrics['total_inventory_value'], Decimal('1150.00'))

    def test_sales_financial_metrics_calculation(self):
        """
        Tests end-to-end sales transactions and verifies total_revenue, total_cogs, and gross_profit.
        """
        # 1. Create and stock a finished good
        panel = Item.objects.create(
            type=ItemType.FINISHED_GOOD,
            sku='FG-PANEL-HD',
            name='Heavy-Duty Panel',
            unit_of_measure=UnitOfMeasure.PIECE,
            current_stock=Decimal('50.0000'),
            moving_average_cost=Decimal('60.0000')
        )

        # 2. Execute sales order: Sell 10 units @ $150 each
        # Revenue = 10 * 150 = $1,500.00
        # COGS = 10 * 60 = $600.00
        # Gross Profit = 1500 - 600 = $900.00
        # Remaining Inventory Value = 40 * 60 = $2,400.00
        process_sales_order(
            client=self.client,
            item=panel,
            quantity=Decimal('10.0000'),
            selling_price=Decimal('150.0000'),
            reference_id='SO-FIN-01'
        )

        metrics = FinancialDashboardService.get_financial_metrics()

        self.assertEqual(metrics['total_revenue'], Decimal('1500.00'))
        self.assertEqual(metrics['total_cogs'], Decimal('600.00'))
        self.assertEqual(metrics['gross_profit'], Decimal('900.00'))
        self.assertEqual(metrics['total_inventory_value'], Decimal('2400.00'))

    def test_date_range_filtering(self):
        """
        Verifies that start_date and end_date accurately constrain the aggregated sales ledger records.
        """
        gear = Item.objects.create(
            type=ItemType.FINISHED_GOOD,
            sku='FG-GEAR-01',
            name='Helical Gear',
            unit_of_measure=UnitOfMeasure.PIECE,
            current_stock=Decimal('100.0000'),
            moving_average_cost=Decimal('20.0000')
        )

        # Create past transaction (30 days ago) using explicit timestamp
        past_time = timezone.now() - timedelta(days=30)
        process_sales_order(
            client=self.client,
            item=gear,
            quantity=Decimal('5.0000'),
            selling_price=Decimal('50.0000'),
            reference_id='SO-PAST',
            timestamp=past_time
        )

        # Create recent transaction (today)
        recent_sale = process_sales_order(
            client=self.client,
            item=gear,
            quantity=Decimal('10.0000'),
            selling_price=Decimal('50.0000'),
            reference_id='SO-RECENT'
        )

        # Query metrics for the last 7 days only
        last_week_start = timezone.now().date() - timedelta(days=7)
        metrics = FinancialDashboardService.get_financial_metrics(
            start_date=last_week_start,
            end_date=timezone.now().date()
        )

        # Only recent sale (10 * 50 = $500) should be included
        self.assertEqual(metrics['total_revenue'], Decimal('500.00'))
        self.assertEqual(metrics['total_cogs'], Decimal('200.00'))
        self.assertEqual(metrics['gross_profit'], Decimal('300.00'))


class DashboardHomeViewTestCase(TestCase):
    """
    Tests DashboardHomeView template rendering, HTTP status codes, and context variables.
    """

    def setUp(self):
        # Create an item with low stock (< 10)
        self.low_stock_item = Item.objects.create(
            type=ItemType.RAW_MATERIAL,
            sku='RM-CRITICAL-BOLT',
            name='Precision Bolt M4',
            unit_of_measure=UnitOfMeasure.PIECE,
            current_stock=Decimal('4.0000'),
            moving_average_cost=Decimal('0.8500')
        )
        # Create an item with sufficient stock (>= 10)
        self.normal_stock_item = Item.objects.create(
            type=ItemType.FINISHED_GOOD,
            sku='FG-BOX-STOCKED',
            name='Standard Junction Box',
            unit_of_measure=UnitOfMeasure.PIECE,
            current_stock=Decimal('50.0000'),
            moving_average_cost=Decimal('22.0000')
        )

    def test_dashboard_home_view_get(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'dashboard/home.html')

        # Verify context data
        context = response.context
        self.assertIn('financial_metrics', context)
        self.assertIn('total_revenue', context)
        self.assertIn('total_cogs', context)
        self.assertIn('gross_profit', context)
        self.assertIn('total_inventory_value', context)
        self.assertIn('low_stock_alerts', context)

        # Verify that only the low stock item is in the alerts queryset
        alerts = list(context['low_stock_alerts'])
        self.assertIn(self.low_stock_item, alerts)
        self.assertNotIn(self.normal_stock_item, alerts)

        # Verify HTML rendered content contains key elements
        content = response.content.decode('utf-8')
        self.assertIn('Global-IMS', content)
        self.assertIn('salesChart', content)
        self.assertIn('RM-CRITICAL-BOLT', content)
        self.assertIn('Precision Bolt M4', content)

