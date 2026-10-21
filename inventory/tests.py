from decimal import Decimal
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from inventory.models import Item, ItemType, UnitOfMeasure, BillOfMaterials, BOMComponent
from transactions.models import Contact, ContactType


class BillOfMaterialsRelationalIntegrityTestCase(TestCase):
    """
    Validates relational integrity, foreign key navigation, through-models,
    and business rule constraints for the Inventory and BOM domain.
    """

    def setUp(self):
        # 1. Create a Vendor
        self.vendor = Contact.objects.create(
            type=ContactType.VENDOR,
            company_name='Steel Suppliers Inc',
            contact_person='David Miller',
            email='procurement@steelsuppliers.com',
            phone='+1-555-0199',
            GST_number='07AABCS1429B1Z1',
            address='Industrial Area Phase 2, New Delhi'
        )

        # 2. Create a Raw Material
        self.raw_material = Item.objects.create(
            type=ItemType.RAW_MATERIAL,
            sku='RM-GI-SHEET-01',
            name='Galvanized Iron Sheet',
            description='0.8mm Hot-Dip Galvanized Iron Sheet 4x8 ft',
            unit_of_measure=UnitOfMeasure.PIECE,
            current_stock=Decimal('100.0000'),
            moving_average_cost=Decimal('45.5000')
        )

        # 3. Create a Finished Good
        self.finished_good = Item.objects.create(
            type=ItemType.FINISHED_GOOD,
            sku='FG-CORE-BOX-01',
            name='GI Core Box',
            description='Enclosed industrial electrical core box',
            unit_of_measure=UnitOfMeasure.PIECE,
            current_stock=Decimal('0.0000'),
            moving_average_cost=Decimal('0.0000')
        )

    def test_bom_creation_and_relational_queries(self):
        """
        Tests creating a Bill of Materials linking 1 'GI Core Box' to 2 'Galvanized Iron Sheets',
        and validates seamless reverse/forward query traversal without IntegrityError
        or RelatedObjectDoesNotExist exceptions.
        """
        # 4. Create BillOfMaterials and BOMComponent
        bom = BillOfMaterials.objects.create(
            finished_good=self.finished_good,
            version='1.0',
            description='Production standard BOM specification for GI Core Box',
            is_active=True
        )

        bom_component = BOMComponent.objects.create(
            bom=bom,
            raw_material=self.raw_material,
            quantity=Decimal('2.0000'),
            scrap_percentage=Decimal('1.00')
        )

        # 5. Query BOM starting from Finished Good
        retrieved_bom = self.finished_good.bill_of_materials.get(version='1.0', is_active=True)
        self.assertEqual(retrieved_bom.id, bom.id)
        self.assertEqual(retrieved_bom.finished_good.name, 'GI Core Box')

        # 6. Verify components via through-model relationship
        components = retrieved_bom.components.select_related('raw_material').all()
        self.assertEqual(components.count(), 1)

        first_component = components.first()
        self.assertIsNotNone(first_component)
        self.assertEqual(first_component.raw_material, self.raw_material)
        self.assertEqual(first_component.raw_material.sku, 'RM-GI-SHEET-01')
        self.assertEqual(first_component.raw_material.name, 'Galvanized Iron Sheet')
        self.assertEqual(first_component.quantity, Decimal('2.0000'))
        self.assertEqual(first_component.scrap_percentage, Decimal('1.00'))

        # 7. Query reverse M2M: Verify that raw material lists the BOMs it is used in
        boms_using_raw_material = self.raw_material.used_in_boms.all()
        self.assertIn(bom, boms_using_raw_material)

        # 8. Direct ManyToMany query through BOM
        raw_materials_in_bom = retrieved_bom.raw_materials.all()
        self.assertIn(self.raw_material, raw_materials_in_bom)

    def test_vendor_record_integrity(self):
        """
        Validates Vendor contact creation and attributes.
        """
        vendor = Contact.objects.get(company_name='Steel Suppliers Inc')
        self.assertEqual(vendor.type, ContactType.VENDOR)
        self.assertEqual(vendor.GST_number, '07AABCS1429B1Z1')
        self.assertTrue(vendor.is_active)

    def test_duplicate_bom_version_constraint(self):
        """
        Ensures UniqueConstraint prevents duplicate versions of BOM for the same finished good.
        """
        BillOfMaterials.objects.create(
            finished_good=self.finished_good,
            version='1.0'
        )
        with self.assertRaises(IntegrityError):
            BillOfMaterials.objects.create(
                finished_good=self.finished_good,
                version='1.0'
            )

    def test_duplicate_raw_material_in_same_bom_constraint(self):
        """
        Ensures UniqueConstraint prevents adding the same raw material twice to the same BOM.
        """
        bom = BillOfMaterials.objects.create(
            finished_good=self.finished_good,
            version='2.0'
        )
        BOMComponent.objects.create(
            bom=bom,
            raw_material=self.raw_material,
            quantity=Decimal('2.0000')
        )
        with self.assertRaises(IntegrityError):
            BOMComponent.objects.create(
                bom=bom,
                raw_material=self.raw_material,
                quantity=Decimal('4.0000')
            )


class EmployeeAndVendorOrderViewsTestCase(TestCase):
    """
    Tests EmployeeListView, EmployeeDetailView, and VendorOrderListView backend logic,
    annotations (Count & Sum), daily breakdowns, and URL wiring.
    """

    def setUp(self):
        from django.utils import timezone
        import datetime
        from inventory.models import Employee, ProductionLog, Item, ItemType, UnitOfMeasure
        from transactions.models import Contact, ContactType, PurchaseOrder

        self.today = timezone.now().date()
        self.current_month = self.today.month
        self.current_year = self.today.year

        # Create Items
        self.item = Item.objects.create(
            sku='FG-BOX-TEST-01',
            name='Test GI Core Box',
            type=ItemType.FINISHED_GOOD,
            unit_of_measure=UnitOfMeasure.PIECE,
            current_stock=Decimal('20.0000'),
            moving_average_cost=Decimal('50.0000')
        )

        # Create Employees: 2 active, 1 inactive
        self.emp1 = Employee.objects.create(
            first_name='John',
            last_name='Doe',
            is_active=True
        )
        self.emp2 = Employee.objects.create(
            first_name='Jane',
            last_name='Smith',
            is_active=True
        )
        self.emp_inactive = Employee.objects.create(
            first_name='Bob',
            last_name='Inactive',
            is_active=False
        )

        # Create Production logs for emp1 in current month
        # Note: production_date has auto_now_add=True, so it defaults to today
        self.log1 = ProductionLog.objects.create(
            employee=self.emp1,
            item=self.item,
            quantity=15
        )
        self.log2 = ProductionLog.objects.create(
            employee=self.emp1,
            item=self.item,
            quantity=10
        )

        # Create Production log for emp2 in current month
        self.log3 = ProductionLog.objects.create(
            employee=self.emp2,
            item=self.item,
            quantity=8
        )

        # Create Vendor & Purchase Orders
        self.vendor = Contact.objects.create(
            type=ContactType.VENDOR,
            company_name='Apex Metals Ltd',
            GST_number='07AABCS9999Z1Z5'
        )
        self.po1 = PurchaseOrder.objects.create(
            vendor=self.vendor,
            item=self.item,
            quantity=50,
            expected_delivery_date=self.today + datetime.timedelta(days=7)
        )
        self.po2 = PurchaseOrder.objects.create(
            vendor=self.vendor,
            item=self.item,
            quantity=100,
            expected_delivery_date=self.today + datetime.timedelta(days=14)
        )

    def test_employee_list_view_queryset_and_annotations(self):
        """
        Validates that EmployeeListView fetches active employees and calculates
        total boxes manufactured and log count for the current month.
        """
        from inventory.views import EmployeeListView

        view = EmployeeListView()
        qs = view.get_queryset()

        # Inactive employee should be excluded
        self.assertNotIn(self.emp_inactive, qs)

        # Both active employees should be in queryset
        self.assertEqual(qs.count(), 2)

        emp1_annotated = qs.get(pk=self.emp1.pk)
        self.assertEqual(emp1_annotated.total_boxes, 25)
        self.assertEqual(emp1_annotated.production_count, 2)

        emp2_annotated = qs.get(pk=self.emp2.pk)
        self.assertEqual(emp2_annotated.total_boxes, 8)
        self.assertEqual(emp2_annotated.production_count, 1)

    def test_employee_detail_view_daily_breakdown(self):
        """
        Validates that EmployeeDetailView groups daily production using
        values('production_date').annotate(Sum('quantity')).
        """
        from inventory.views import EmployeeDetailView

        view = EmployeeDetailView()
        view.kwargs = {'pk': self.emp1.pk, 'month': self.current_month}
        view.object = self.emp1

        context = view.get_context_data()
        breakdown = list(context['daily_breakdown'])

        self.assertEqual(len(breakdown), 1)
        self.assertEqual(breakdown[0]['production_date'], self.today)
        self.assertEqual(breakdown[0]['quantity__sum'], 25)

    def test_vendor_order_list_view_queryset(self):
        """
        Validates that VendorOrderListView fetches all PurchaseOrder records
        with vendor and item relationships.
        """
        from inventory.views import VendorOrderListView

        view = VendorOrderListView()
        qs = view.get_queryset()

        self.assertEqual(qs.count(), 2)
        first_po = qs.first()
        self.assertIsNotNone(first_po.vendor.company_name)
        self.assertIsNotNone(first_po.item.sku)
        self.assertIsNotNone(first_po.quantity)
        self.assertIsNotNone(first_po.expected_delivery_date)

    def test_urls_reverse_and_resolve(self):
        """
        Validates that all new views are properly wired to URLs and resolve correctly.
        """
        from django.urls import reverse, resolve
        from inventory.views import EmployeeListView, EmployeeDetailView, VendorOrderListView

        # Reverse URLs
        url_emp_list = reverse('employee-list')
        url_emp_detail = reverse('employee-detail', kwargs={'pk': self.emp1.pk, 'month': self.current_month})
        url_vendor_orders = reverse('vendor-order-list')

        self.assertEqual(url_emp_list, '/employees/')
        self.assertEqual(url_emp_detail, f'/employees/{self.emp1.pk}/{self.current_month}/')
        self.assertEqual(url_vendor_orders, '/vendor-orders/')

        # Resolve URLs to views
        match_list = resolve('/employees/')
        self.assertEqual(match_list.func.view_class, EmployeeListView)

        match_detail = resolve(f'/employees/{self.emp1.pk}/{self.current_month}/')
        self.assertEqual(match_detail.func.view_class, EmployeeDetailView)

        match_orders = resolve('/vendor-orders/')
        self.assertEqual(match_orders.func.view_class, VendorOrderListView)

        match_add_inv = resolve('/add-inventory/')
        from inventory.views import AddInventoryView
        self.assertEqual(match_add_inv.func.view_class, AddInventoryView)

    def test_employee_list_view_template_and_actions(self):
        """
        Validates EmployeeListView template rendering, adding an employee, and deactivation.
        """
        from django.urls import reverse
        from inventory.models import Employee

        # 1. GET /employees/
        response = self.client.get(reverse('employee-list'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'inventory/employee_list.html')
        self.assertContains(response, 'John Doe')
        self.assertContains(response, 'Current Month Production')

        # 2. POST Add Employee
        add_response = self.client.post(reverse('employee-list'), {
            'action': 'add_employee',
            'first_name': 'Alex',
            'last_name': 'Turner',
            'is_active': 'on'
        })
        self.assertEqual(add_response.status_code, 302)
        new_emp = Employee.objects.get(first_name='Alex', last_name='Turner')
        self.assertTrue(new_emp.is_active)

        # 3. POST Deactivate Employee
        deact_response = self.client.post(reverse('employee-list'), {
            'action': 'deactivate',
            'employee_id': self.emp2.pk
        })
        self.assertEqual(deact_response.status_code, 302)
        self.emp2.refresh_from_db()
        self.assertFalse(self.emp2.is_active)

    def test_employee_detail_view_template(self):
        """
        Validates EmployeeDetailView template rendering and Daily Breakdown table.
        """
        from django.urls import reverse

        response = self.client.get(reverse('employee-detail', kwargs={'pk': self.emp1.pk, 'month': self.current_month}))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'inventory/employee_detail.html')
        self.assertContains(response, 'John Doe')
        self.assertContains(response, 'Daily Production Breakdown')
        self.assertContains(response, 'Test GI Core Box')

    def test_vendor_orders_view_template_and_overdue_highlight(self):
        """
        Validates VendorOrderListView template rendering and overdue light red highlight.
        """
        import datetime
        from django.urls import reverse
        from transactions.models import PurchaseOrder

        # Create an overdue pending order
        overdue_po = PurchaseOrder.objects.create(
            vendor=self.vendor,
            item=self.item,
            quantity=25,
            expected_delivery_date=self.today - datetime.timedelta(days=3),
            status='Pending'
        )

        response = self.client.get(reverse('vendor-order-list'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'inventory/vendor_orders.html')
        self.assertContains(response, 'Apex Metals Ltd')
        self.assertContains(response, 'Overdue (Pending)')
        # Row highlight class
        self.assertContains(response, 'bg-red-950/40 border-l-4 border-red-500')

    def test_add_inventory_view_get_and_post(self):
        """
        Validates AddInventoryView GET template and atomic stock inward POST.
        """
        from django.urls import reverse
        from transactions.models import InventoryLedger, TransactionType

        # 1. GET /add-inventory/
        response = self.client.get(reverse('add-inventory'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'inventory/add_inventory.html')
        self.assertContains(response, 'Add Inventory Stock')

        # 2. POST /add-inventory/
        initial_stock = self.item.current_stock
        post_response = self.client.post(reverse('add-inventory'), {
            'item_id': self.item.pk,
            'quantity': '30.00',
            'unit_cost': '60.00',
            'vendor_id': self.vendor.pk,
            'reference_id': 'TEST-INW-01',
            'narration': 'Test stock delivery inward'
        })
        self.assertEqual(post_response.status_code, 302)

        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, initial_stock + Decimal('30.00'))

        ledger = InventoryLedger.objects.filter(generic_reference_id='TEST-INW-01').first()
        self.assertIsNotNone(ledger)
        self.assertEqual(ledger.transaction_type, TransactionType.IN)
        self.assertEqual(ledger.quantity_changed, Decimal('30.00'))

    def test_employee_list_log_today_boxes(self):
        """
        Validates entering how many boxes an employee manufactured today and saving it.
        """
        from django.urls import reverse
        from inventory.models import ProductionLog

        initial_count = ProductionLog.objects.filter(employee=self.emp1, production_date=self.today).count()

        # POST /employees/ with action='log_production'
        response = self.client.post(reverse('employee-list'), {
            'action': 'log_production',
            'employee_id': self.emp1.pk,
            'quantity': '42',
            'item_id': self.item.pk
        })
        self.assertEqual(response.status_code, 302)

        # Verify new ProductionLog created
        new_logs = ProductionLog.objects.filter(employee=self.emp1, production_date=self.today)
        self.assertEqual(new_logs.count(), initial_count + 1)
        latest_log = new_logs.latest('id')
        self.assertEqual(latest_log.quantity, 42)
        self.assertEqual(latest_log.item, self.item)

        # Verify updated EmployeeListView shows the updated today and month totals
        get_response = self.client.get(reverse('employee-list'))
        self.assertEqual(get_response.status_code, 200)
        # Previous 25 + 42 = 67
        self.assertContains(get_response, '67')

    def test_employee_list_bulk_log_today_boxes(self):
        """
        Validates bulk end-of-day logging across multiple employees.
        """
        from django.urls import reverse
        from inventory.models import ProductionLog

        response = self.client.post(reverse('employee-list'), {
            'action': 'bulk_log_production',
            'item_id': self.item.pk,
            f'boxes_{self.emp1.pk}': '18',
            f'boxes_{self.emp2.pk}': '35',
        })
        self.assertEqual(response.status_code, 302)

        emp1_today = ProductionLog.objects.filter(employee=self.emp1, quantity=18, production_date=self.today)
        self.assertTrue(emp1_today.exists())

        emp2_today = ProductionLog.objects.filter(employee=self.emp2, quantity=35, production_date=self.today)
        self.assertTrue(emp2_today.exists())

    def test_add_inventory_new_product_with_three_questions(self):
        """
        Validates the simple 3-question form:
        1. Name of the product
        2. Quantity
        3. Weight of the product (core box)
        Successfully creates the new item, assigns weight, stock, and records in ledger.
        """
        from django.urls import reverse
        from inventory.models import Item, ItemType
        from transactions.models import InventoryLedger, TransactionType

        response = self.client.post(reverse('add-inventory'), {
            'name': 'GI Industrial Core Box 300mm',
            'quantity': '40',
            'weight': '6.250'
        })
        self.assertEqual(response.status_code, 302)

        # Verify Item created
        new_item = Item.objects.filter(name='GI Industrial Core Box 300mm').first()
        self.assertIsNotNone(new_item)
        self.assertEqual(new_item.current_stock, Decimal('40.0000'))
        self.assertEqual(new_item.weight, Decimal('6.250'))
        self.assertEqual(new_item.type, ItemType.FINISHED_GOOD)
        self.assertTrue(new_item.sku.startswith('CB-'))
        self.assertEqual(new_item.total_weight, Decimal('250.000'))  # 40 * 6.250

        # Verify ledger
        ledger = InventoryLedger.objects.filter(item=new_item).first()
        self.assertIsNotNone(ledger)
        self.assertEqual(ledger.transaction_type, TransactionType.IN)
        self.assertEqual(ledger.quantity_changed, Decimal('40.0000'))

    def test_add_same_product_same_weight_increments_stock(self):
        """
        Validates submitting the same core box name with the same unit weight increments stock.
        """
        from django.urls import reverse
        from inventory.models import Item

        self.item.weight = Decimal('4.800')
        self.item.save(update_fields=['weight'])
        initial_stock = self.item.current_stock  # 20.0000

        response = self.client.post(reverse('add-inventory'), {
            'name': 'Test GI Core Box',
            'quantity': '15',
            'weight': '4.800'
        })
        self.assertEqual(response.status_code, 302)

        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, initial_stock + Decimal('15'))
        self.assertEqual(self.item.weight, Decimal('4.800'))

    def test_add_same_core_box_different_weight_creates_distinct_item(self):
        """
        Validates adding the same core box name but with different weight creates
        a separate distinct product, not merged into one.
        """
        from django.urls import reverse
        from inventory.models import Item

        self.item.weight = Decimal('3.500')
        self.item.save(update_fields=['weight'])

        # Add same name 'Test GI Core Box' with DIFFERENT weight '5.200'
        response = self.client.post(reverse('add-inventory'), {
            'name': 'Test GI Core Box',
            'quantity': '25',
            'weight': '5.200'
        })
        self.assertEqual(response.status_code, 302)

        # Must have two distinct items for 'Test GI Core Box'
        matching_items = Item.objects.filter(name='Test GI Core Box')
        self.assertEqual(matching_items.count(), 2)

        item1 = matching_items.get(weight=Decimal('3.500'))
        self.assertEqual(item1.current_stock, Decimal('20.0000'))

        item2 = matching_items.get(weight=Decimal('5.200'))
        self.assertEqual(item2.current_stock, Decimal('25.0000'))
        self.assertNotEqual(item1.sku, item2.sku)

    def test_inventory_table_displays_weight_of_one_core_box_without_total_weight(self):
        """
        Validates the table renders weight of one core box, without total weight calculations.
        """
        from django.urls import reverse

        self.item.weight = Decimal('3.500')
        self.item.save(update_fields=['weight'])

        response = self.client.get(reverse('add-inventory'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'All Products Inventory Details')
        self.assertContains(response, 'Test GI Core Box')
        self.assertContains(response, 'Weight (One Core Box)')
        self.assertContains(response, '3.500')
        # Total weight calculations must NOT be in the response
        self.assertNotContains(response, 'Total Weight on Hand')
        self.assertNotContains(response, 'Total Stock Weight')
        self.assertNotContains(response, 'Total Batch Weight')

    def test_remove_stock_success(self):
        """
        Validates deducting stock from an existing product.
        """
        from django.urls import reverse
        from transactions.models import InventoryLedger, TransactionType

        initial_stock = self.item.current_stock  # 20.0000

        response = self.client.post(reverse('add-inventory'), {
            'action': 'remove_stock',
            'item_id': self.item.pk,
            'quantity': '5',
            'reason': 'Customer Dispatch / Sale'
        })
        self.assertEqual(response.status_code, 302)

        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, initial_stock - Decimal('5'))

        ledger = InventoryLedger.objects.filter(item=self.item, transaction_type=TransactionType.OUT).first()
        self.assertIsNotNone(ledger)
        self.assertEqual(ledger.quantity_changed, Decimal('-5.0000'))
        self.assertEqual(ledger.generic_reference_id, 'STOCK-REMOVAL')

    def test_remove_stock_insufficient_stock_fails(self):
        """
        Validates that attempting to remove more stock than available fails gracefully.
        """
        from django.urls import reverse

        initial_stock = self.item.current_stock  # 20.0000

        response = self.client.post(reverse('add-inventory'), {
            'action': 'remove_stock',
            'item_id': self.item.pk,
            'quantity': '50',  # Exceeds 20
        })
        self.assertEqual(response.status_code, 302)

        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, initial_stock)  # Stock untouched

    def test_remove_stock_by_product_name(self):
        """
        Validates removing stock by matching product name.
        """
        from django.urls import reverse

        initial_stock = self.item.current_stock  # 20.0000

        response = self.client.post(reverse('add-inventory'), {
            'action': 'remove_stock',
            'name': 'Test GI Core Box',
            'quantity': '8'
        })
        self.assertEqual(response.status_code, 302)

        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, initial_stock - Decimal('8'))


