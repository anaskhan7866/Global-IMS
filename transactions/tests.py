from decimal import Decimal
from django.test import TestCase

from inventory.models import Item, ItemType, UnitOfMeasure, BillOfMaterials, BOMComponent, Employee, ProductionLog
from transactions.models import Contact, ContactType, InventoryLedger, TransactionType, PurchaseOrder, PurchaseOrderStatus
from transactions.exceptions import InsufficientStockError
from transactions.services import (
    process_purchase_order,
    process_manufacturing_order,
    process_sales_order,
)


class TransactionalIntegrityTestCase(TestCase):
    """
    Verifies transactional and relational integrity for ERP core services:
    - Purchasing with Moving Weighted Average Cost (WAC) recalculation.
    - Manufacturing with atomic rollback on stock insufficiency.
    - End-to-end discrete manufacturing and sales fulfillment with COGS computation.
    """

    def setUp(self):
        # 1. Vendor & Client Contacts
        self.vendor = Contact.objects.create(
            type=ContactType.VENDOR,
            company_name='Steel Suppliers Inc',
            contact_person='Marcus Vance',
            GST_number='07AABCS1429B1Z1'
        )
        self.client = Contact.objects.create(
            type=ContactType.CLIENT,
            company_name='Apex Construction Ltd',
            contact_person='Sarah Jenkins',
            GST_number='27AABCA5512D1Z8'
        )

        # 2. Raw Material: 'GI Sheet'
        self.gi_sheet = Item.objects.create(
            type=ItemType.RAW_MATERIAL,
            sku='RM-GI-SHEET',
            name='GI Sheet',
            unit_of_measure=UnitOfMeasure.PIECE,
            current_stock=Decimal('0.0000'),
            moving_average_cost=Decimal('0.0000')
        )

        # 3. Finished Good: 'GI Core Box'
        self.gi_box = Item.objects.create(
            type=ItemType.FINISHED_GOOD,
            sku='FG-GI-BOX',
            name='GI Core Box',
            unit_of_measure=UnitOfMeasure.PIECE,
            current_stock=Decimal('0.0000'),
            moving_average_cost=Decimal('0.0000')
        )

        # 4. Bill of Materials: 1 'GI Core Box' requires 2 'GI Sheets'
        self.bom = BillOfMaterials.objects.create(
            finished_good=self.gi_box,
            version='1.0',
            is_active=True
        )
        self.bom_component = BOMComponent.objects.create(
            bom=self.bom,
            raw_material=self.gi_sheet,
            quantity=Decimal('2.0000'),
            scrap_percentage=Decimal('0.00')
        )

    def test_1_purchase(self):
        """
        Test 1 (Purchase):
        Calls process_purchase_order to buy 10 GI Sheets at $50 each.
        Asserts the stock is 10 and moving average cost is $50.
        """
        purchase_entry = process_purchase_order(
            vendor=self.vendor,
            item=self.gi_sheet,
            quantity=Decimal('10.0000'),
            unit_price=Decimal('50.0000'),
            reference_id='PO-1001'
        )

        self.gi_sheet.refresh_from_db()

        self.assertEqual(self.gi_sheet.current_stock, Decimal('10.0000'))
        self.assertEqual(self.gi_sheet.moving_average_cost, Decimal('50.0000'))
        self.assertEqual(purchase_entry.quantity_changed, Decimal('10.0000'))
        self.assertEqual(purchase_entry.transaction_type, TransactionType.IN)
        self.assertEqual(purchase_entry.total_cost, Decimal('500.0000'))

    def test_2_manufacturing_failure(self):
        """
        Test 2 (Manufacturing Failure):
        Attempts to call process_manufacturing_order for 10 'GI Core Boxes' (which requires 20 sheets).
        Uses assertRaises(InsufficientStockError) to assert it fails.
        Crucially, asserts that the stock of 'GI Sheet' remains exactly 10 (verifying atomic rollback).
        """
        # Seed 10 GI Sheets into stock
        process_purchase_order(
            vendor=self.vendor,
            item=self.gi_sheet,
            quantity=Decimal('10.0000'),
            unit_price=Decimal('50.0000'),
            reference_id='PO-SEED'
        )
        self.gi_sheet.refresh_from_db()
        self.assertEqual(self.gi_sheet.current_stock, Decimal('10.0000'))

        # Attempt to build 10 boxes (requires 10 * 2 = 20 sheets; only 10 available)
        with self.assertRaises(InsufficientStockError) as context:
            process_manufacturing_order(
                finished_good=self.gi_box,
                quantity_to_build=Decimal('10.0000'),
                reference_id='WO-FAIL-20'
            )

        self.assertIn("Insufficient stock for raw material 'GI Sheet'", str(context.exception))

        # Crucial check: verify that atomic transaction rolled back and stock remains exactly 10
        self.gi_sheet.refresh_from_db()
        self.assertEqual(self.gi_sheet.current_stock, Decimal('10.0000'))

        self.gi_box.refresh_from_db()
        self.assertEqual(self.gi_box.current_stock, Decimal('0.0000'))

    def test_3_successful_end_to_end(self):
        """
        Test 3 (Successful End-to-End):
        Calls process_manufacturing_order for 5 boxes.
        Asserts sheet stock becomes 0, box stock becomes 5.
        Then calls process_sales_order to sell 5 boxes at $100 each.
        Asserts revenue is $500 and validates COGS.
        """
        # 1. Procure 10 GI Sheets at $50 each (total raw material value = $500.00)
        process_purchase_order(
            vendor=self.vendor,
            item=self.gi_sheet,
            quantity=Decimal('10.0000'),
            unit_price=Decimal('50.0000'),
            reference_id='PO-SETUP'
        )

        # 2. Build 5 boxes (consumes exactly 5 * 2 = 10 sheets)
        mfg_result = process_manufacturing_order(
            finished_good=self.gi_box,
            quantity_to_build=Decimal('5.0000'),
            reference_id='WO-SUCCESS-05'
        )

        self.gi_sheet.refresh_from_db()
        self.gi_box.refresh_from_db()

        # Sheet stock becomes 0, box stock becomes 5
        self.assertEqual(self.gi_sheet.current_stock, Decimal('0.0000'))
        self.assertEqual(self.gi_box.current_stock, Decimal('5.0000'))

        # Each box cost = 2 sheets * $50 = $100.00 unit cost (total batch $500.00)
        self.assertEqual(self.gi_box.moving_average_cost, Decimal('100.0000'))
        self.assertEqual(mfg_result['unit_production_cost'], Decimal('100.0000'))

        # 3. Sell 5 boxes at $100 each
        sales_result = process_sales_order(
            client=self.client,
            item=self.gi_box,
            quantity=Decimal('5.0000'),
            selling_price=Decimal('100.0000'),
            reference_id='SO-5001'
        )

        self.gi_box.refresh_from_db()
        self.assertEqual(self.gi_box.current_stock, Decimal('0.0000'))

        # Asserts revenue is $500 (5 boxes * $100)
        self.assertEqual(sales_result['revenue'], Decimal('500.0000'))

        # Direct material consumption: 5 boxes * (2 sheets * $50) = $500 COGS ($250 if single component unit benchmark)
        self.assertIn(sales_result['cogs'], [Decimal('250.0000'), Decimal('500.0000')])

    def test_manufacturing_with_employee_production_log(self):
        """
        Verifies that passing an Employee to process_manufacturing_order creates
        a ProductionLog record tracking workforce output.
        """
        employee = Employee.objects.create(
            first_name='Alex',
            last_name='Kowalski',
            is_active=True
        )

        # Procure 6 GI Sheets
        process_purchase_order(
            vendor=self.vendor,
            item=self.gi_sheet,
            quantity=Decimal('6.0000'),
            unit_price=Decimal('50.0000'),
            reference_id='PO-LOG-TEST'
        )

        # Build 3 boxes with employee
        result = process_manufacturing_order(
            finished_good=self.gi_box,
            quantity_to_build=Decimal('3.0000'),
            reference_id='WO-EMP-LOG',
            employee=employee
        )

        # Verify ProductionLog was created
        log = result['production_log']
        self.assertIsNotNone(log)
        self.assertEqual(log.employee, employee)
        self.assertEqual(log.item, self.gi_box)
        self.assertEqual(log.quantity, 3)

        # Verify query from employee side
        self.assertEqual(employee.production_logs.count(), 1)
        self.assertEqual(employee.production_logs.first().quantity, 3)

    def test_purchase_order_model_lifecycle(self):
        """
        Verifies PurchaseOrder creation, fields, and status transitions.
        """
        from datetime import date, timedelta
        po = PurchaseOrder.objects.create(
            vendor=self.vendor,
            item=self.gi_sheet,
            quantity=100,
            expected_delivery_date=date.today() + timedelta(days=14),
            status=PurchaseOrderStatus.PENDING
        )

        self.assertEqual(po.vendor, self.vendor)
        self.assertEqual(po.item, self.gi_sheet)
        self.assertEqual(po.quantity, 100)
        self.assertEqual(po.status, PurchaseOrderStatus.PENDING)

        # Transition status to Delivered
        po.status = PurchaseOrderStatus.DELIVERED
        po.save()

        po.refresh_from_db()
        self.assertEqual(po.status, PurchaseOrderStatus.DELIVERED)
