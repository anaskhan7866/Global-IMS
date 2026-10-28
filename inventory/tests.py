import datetime
from decimal import Decimal
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from inventory.models import (
    Item, ItemType, UnitOfMeasure, BillOfMaterials, BOMComponent,
    Employee, ProductionLog
)
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
        from django.contrib.auth.models import User
        import datetime
        from inventory.models import Employee, ProductionLog, Item, ItemType, UnitOfMeasure
        from transactions.models import Contact, ContactType, PurchaseOrder

        self.user = User.objects.create_user(username='testoperator', password='TestPassword123!')
        self.client.force_login(self.user)

        self.today = timezone.localdate()
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
        self.assertEqual(context['total_month_units'], 25)
        self.assertEqual(context['active_days'], 1)
        self.assertEqual(context['daily_average'], 25.0)

        # Add a second active day for emp1 to verify multi-day average division
        yesterday = self.today - datetime.timedelta(days=1)
        ProductionLog.objects.create(
            employee=self.emp1,
            item=self.item,
            quantity=15,
            production_date=yesterday
        )
        context_multi = view.get_context_data()
        self.assertEqual(context_multi['total_month_units'], 40)
        self.assertEqual(context_multi['active_days'], 2)
        self.assertEqual(context_multi['daily_average'], 20.0)

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

    def test_streamlined_employee_table_features(self):
        """
        Validates that the Employee table strictly provides the requested features:
        Name of Employee, Boxes, Current Month Boxes, Daily Log, and Delete.
        """
        from django.urls import reverse
        from inventory.models import Employee, ProductionLog

        # 1. GET /employees/ renders exactly the 5 features
        response = self.client.get(reverse('employee-list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Name of Employee')
        self.assertContains(response, 'Boxes')
        self.assertContains(response, 'Current Month Boxes')
        self.assertContains(response, 'Daily Log')
        self.assertContains(response, 'Delete')
        self.assertContains(response, 'openDeleteEmployeeModal')

        # Clutter removed
        self.assertNotContains(response, "Today's Manufactured Boxes")
        self.assertNotContains(response, "Active Operators")
        self.assertNotContains(response, "End-of-Day Batch Sheet")

        # 2. Test logging boxes for an employee
        initial_emp1_boxes = self.emp1.production_logs.count()
        log_res = self.client.post(reverse('employee-list'), {
            'action': 'log_production',
            'employee_id': self.emp1.pk,
            'quantity': '12',
            'item_id': self.item.pk
        })
        self.assertEqual(log_res.status_code, 302)
        self.assertEqual(self.emp1.production_logs.count(), initial_emp1_boxes + 1)

        # 3. Test deleting an employee
        delete_emp = Employee.objects.create(
            first_name='ToDelete',
            last_name='Worker',
            is_active=True
        )
        del_res = self.client.post(reverse('employee-list'), {
            'action': 'delete',
            'employee_id': delete_emp.pk
        })
        self.assertEqual(del_res.status_code, 302)
        self.assertFalse(Employee.objects.filter(pk=delete_emp.pk, is_active=True).exists())


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

    def test_daily_logs_single_and_multiple_box_types_breakdown(self):
        """
        Validates:
        1. When multiple different box types are produced on the same day
           (e.g. 50 core box, 10 polish box, 10 plastic box),
           the daily log shows the day's total (70) and the breakdown of each box type.
        2. When only one type of box is produced on a day (e.g. 40 core box),
           it shows the total (40) and details of that single box type.
        """
        import datetime
        from django.urls import reverse
        from inventory.models import Item, ItemType, UnitOfMeasure, ProductionLog

        # Create multiple distinct box types
        core_box = self.item  # Test GI Core Box
        polish_box = Item.objects.create(
            sku='FG-POLISH-BOX',
            name='Polish Box',
            type=ItemType.FINISHED_GOOD,
            unit_of_measure=UnitOfMeasure.PIECE,
            weight=Decimal('2.100')
        )
        plastic_box = Item.objects.create(
            sku='FG-PLASTIC-BOX',
            name='Plastic Box',
            type=ItemType.FINISHED_GOOD,
            unit_of_measure=UnitOfMeasure.PIECE,
            weight=Decimal('1.500')
        )

        target_date_single = self.today - datetime.timedelta(days=2)

        # Clear existing logs for emp1 for clean isolated test assertions
        self.emp1.production_logs.all().delete()

        # Day 1: Multiple different types: 50 Core Box, 10 Polish Box, 10 Plastic Box (Total 70)
        ProductionLog.objects.create(employee=self.emp1, item=core_box, quantity=50)
        ProductionLog.objects.create(employee=self.emp1, item=polish_box, quantity=10)
        ProductionLog.objects.create(employee=self.emp1, item=plastic_box, quantity=10)

        # Day 2: Single type: 40 Core Box (Total 40)
        log_single = ProductionLog.objects.create(employee=self.emp1, item=core_box, quantity=40)
        log_single.production_date = target_date_single
        log_single.save(update_fields=['production_date'])

        # GET employee detail view for current month
        response = self.client.get(reverse('employee-detail', kwargs={'pk': self.emp1.pk, 'month': self.current_month}))
        self.assertEqual(response.status_code, 200)

        # Day 1 (Multiple types): Total = 70, contains all 3 items and quantities
        self.assertContains(response, '70')
        self.assertContains(response, '3 different box types')
        self.assertContains(response, 'Polish Box')
        self.assertContains(response, 'Plastic Box')

        # Day 2 (Single type): Total = 40, shows single box type detail
        self.assertContains(response, '40')
        self.assertContains(response, 'Single box type')

        # Test POST directly to employee-detail to log output
        post_res = self.client.post(reverse('employee-detail', kwargs={'pk': self.emp1.pk, 'month': self.current_month}), {
            'action': 'log_production',
            'quantity': '15',
            'item_id': polish_box.pk,
            'production_date': self.today.strftime('%Y-%m-%d')
        })
        self.assertEqual(post_res.status_code, 302)
        total_polish = ProductionLog.objects.filter(employee=self.emp1, item=polish_box).count()
        self.assertEqual(total_polish, 2)

    def test_manual_box_type_entry_field(self):
        """
        Validates that box selection dropdown has been replaced with a manual type field,
        and manual entry resolves or auto-registers the typed box type.
        """
        from django.urls import reverse
        from inventory.models import Item, ProductionLog

        # 1. GET /employees/ does NOT have select for item_id, has text input for box_name
        list_res = self.client.get(reverse('employee-list'))
        self.assertEqual(list_res.status_code, 200)
        self.assertContains(list_res, 'name="box_name"')
        self.assertNotContains(list_res, '<select name="item_id"')

        # 2. GET /employees/<pk>/<month>/ does NOT have select for item_id, has text input for box_name
        detail_res = self.client.get(reverse('employee-detail', kwargs={'pk': self.emp1.pk, 'month': self.current_month}))
        self.assertEqual(detail_res.status_code, 200)
        self.assertContains(detail_res, 'name="box_name"')
        self.assertNotContains(detail_res, '<select name="item_id"')

        # 3. Log boxes on employee-list by manually typing a new box name
        log_res = self.client.post(reverse('employee-list'), {
            'action': 'log_production',
            'employee_id': self.emp1.pk,
            'box_name': 'Manual Diamond Box',
            'quantity': '28'
        })
        self.assertEqual(log_res.status_code, 302)
        diamond_item = Item.objects.filter(name='Manual Diamond Box').first()
        self.assertIsNotNone(diamond_item)
        log_entry = ProductionLog.objects.filter(employee=self.emp1, item=diamond_item).first()
        self.assertIsNotNone(log_entry)
        self.assertEqual(log_entry.quantity, 28)

    def test_delete_specific_date_from_daily_logs(self):
        """
        Validates deleting a specific date from employee daily logs:
        - Renders Delete Date button on daily logs table.
        - Deletes all logs for that employee on that specific date.
        - Preserves logs on other dates.
        """
        import datetime
        from django.urls import reverse
        from inventory.models import ProductionLog, Item

        # Create two distinct items
        item_a = Item.objects.create(
            name="Alpha Box",
            sku="FG-ALPHA",
            unit_of_measure="pcs"
        )
        item_b = Item.objects.create(
            name="Beta Box",
            sku="FG-BETA",
            unit_of_measure="pcs"
        )

        date_target = self.today - datetime.timedelta(days=2)
        date_other = self.today - datetime.timedelta(days=5)

        # Logs on target date to delete
        log_target_1 = ProductionLog.objects.create(
            employee=self.emp1,
            item=item_a,
            quantity=30,
            production_date=date_target
        )
        log_target_2 = ProductionLog.objects.create(
            employee=self.emp1,
            item=item_b,
            quantity=15,
            production_date=date_target
        )

        # Log on other date to preserve
        log_preserved = ProductionLog.objects.create(
            employee=self.emp1,
            item=item_a,
            quantity=50,
            production_date=date_other
        )

        # 1. GET employee detail page displays the Delete Date button
        url_detail = reverse('employee-detail', kwargs={'pk': self.emp1.pk, 'month': self.current_month})
        get_res = self.client.get(url_detail)
        self.assertEqual(get_res.status_code, 200)
        self.assertContains(get_res, 'Delete Date')
        self.assertContains(get_res, 'name="action" value="delete_date"')
        self.assertContains(get_res, f'value="{date_target.strftime("%Y-%m-%d")}"')

        # 2. POST delete_date for date_target
        post_res = self.client.post(url_detail, {
            'action': 'delete_date',
            'production_date': date_target.strftime('%Y-%m-%d'),
            'year': str(self.today.year)
        })
        self.assertEqual(post_res.status_code, 302)

        # Assert target date logs were deleted
        self.assertFalse(ProductionLog.objects.filter(pk=log_target_1.pk).exists())
        self.assertFalse(ProductionLog.objects.filter(pk=log_target_2.pk).exists())

        # Assert other date log is strictly preserved
        self.assertTrue(ProductionLog.objects.filter(pk=log_preserved.pk).exists())

        # 3. Test deleting via EmployeeListView
        log_for_list = ProductionLog.objects.create(
            employee=self.emp1,
            item=item_a,
            quantity=10,
            production_date=self.today
        )
        list_post_res = self.client.post(reverse('employee-list'), {
            'action': 'delete_date',
            'employee_id': self.emp1.pk,
            'production_date': self.today.strftime('%Y-%m-%d')
        })
        self.assertEqual(list_post_res.status_code, 302)
        self.assertFalse(ProductionLog.objects.filter(pk=log_for_list.pk).exists())

    def test_employee_production_logging_without_weight_input(self):
        """
        Validates employee production logging with weight input removed:
        - Employee list table renders type of box and quantity inputs without weight input.
        - Logging output with box_name and quantity records the item and production log.
        - Employee detail modal provides box type and quantity fields without weight input.
        """
        from decimal import Decimal
        from django.urls import reverse
        from inventory.models import Item, ProductionLog

        # 1. GET /employees/ does not render weight input
        list_res = self.client.get(reverse('employee-list'))
        self.assertEqual(list_res.status_code, 200)
        self.assertNotContains(list_res, 'placeholder="Weight (kg)"')
        self.assertNotContains(list_res, 'row-weight-input')
        self.assertNotContains(list_res, 'name="weight"')

        # 2. POST log_production with box_name and quantity
        log_res = self.client.post(reverse('employee-list'), {
            'action': 'log_production',
            'employee_id': self.emp1.pk,
            'box_name': 'Reinforced Heavy Box',
            'quantity': '24'
        })
        self.assertEqual(log_res.status_code, 302)

        heavy_box = Item.objects.filter(name='Reinforced Heavy Box').first()
        self.assertIsNotNone(heavy_box)

        log_entry = ProductionLog.objects.filter(employee=self.emp1, item=heavy_box).first()
        self.assertIsNotNone(log_entry)
        self.assertEqual(log_entry.quantity, 24)

        # 3. GET employee detail page does not render weight input in modal
        detail_url = reverse('employee-detail', kwargs={'pk': self.emp1.pk, 'month': self.current_month})
        detail_res = self.client.get(detail_url)
        self.assertEqual(detail_res.status_code, 200)
        self.assertNotContains(detail_res, 'id="modal_weight"')
        self.assertNotContains(detail_res, 'name="weight"')

        # 4. POST log_production via employee detail modal without weight
        detail_post_res = self.client.post(detail_url, {
            'action': 'log_production',
            'box_name': 'Precision Die Box',
            'quantity': '15',
            'production_date': self.today.strftime('%Y-%m-%d')
        })
        self.assertEqual(detail_post_res.status_code, 302)

        die_box = Item.objects.filter(name='Precision Die Box').first()
        self.assertIsNotNone(die_box)

        die_log = ProductionLog.objects.filter(employee=self.emp1, item=die_box).first()
        self.assertIsNotNone(die_log)
        self.assertEqual(die_log.quantity, 15)

    def test_vendor_orders_view_template_and_overdue_highlight(self):
        """
        Validates VendorOrderListView template rendering and overdue highlight.
        """
        import datetime
        from django.urls import reverse
        from transactions.models import PurchaseOrder

        # Create an overdue order
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
        self.assertContains(response, 'Due')
        self.assertContains(response, 'bg-rose-950/20')

    def test_vendor_orders_add_order_deliver_and_clear_workflow(self):
        """
        Validates the streamlined Company/Vendor Order workflow:
        1. Add order from a company specifying quantity, core box type, and delivery date.
        2. Verify table renders clean columns (Company, Box, Quantity, Delivery Date, Remove button).
        3. Click button to remove that line from the vendor order.
        """
        import datetime
        from django.urls import reverse
        from transactions.models import PurchaseOrder, PurchaseOrderStatus
        from inventory.models import Item

        # 1. POST add_order: Company 'Steel Corp' needs 8 boxes of 'Test GI Core Box' with weight 7.500 kg
        future_date = (self.today + datetime.timedelta(days=5)).isoformat()
        add_res = self.client.post(reverse('vendor-order-list'), {
            'action': 'add_order',
            'company_name': 'Steel Corp',
            'box_name': 'Test GI Core Box',
            'quantity': '8',
            'weight': '7.500',
            'expected_delivery_date': future_date
        })
        self.assertEqual(add_res.status_code, 302)

        order = PurchaseOrder.objects.filter(vendor__company_name='Steel Corp').first()
        self.assertIsNotNone(order)
        self.assertEqual(order.quantity, 8)
        self.assertEqual(order.item, self.item)
        self.assertEqual(order.item.weight, Decimal('7.500'))
        self.assertEqual(order.expected_delivery_date.isoformat(), future_date)

        # 2. GET /vendor-orders/ renders the order without weight column and with remove action button
        get_res = self.client.get(reverse('vendor-order-list'))
        self.assertEqual(get_res.status_code, 200)
        self.assertContains(get_res, 'Steel Corp')
        self.assertContains(get_res, 'Test GI Core Box')
        self.assertNotContains(get_res, '<th scope="col" class="px-3 py-3.5">Weight</th>')
        self.assertContains(get_res, 'openRemoveOrderModal')
        self.assertContains(get_res, 'openEditOrderModal')
        self.assertContains(get_res, 'editOrderModal')
        # Confirm Total Boxes Demanded is removed and obsolete buttons are absent
        self.assertNotContains(get_res, 'Total Boxes Demanded')
        self.assertNotContains(get_res, 'openAddVendorModal')
        self.assertNotContains(get_res, 'openDeliverModal')

        # 3. POST remove_order: Clicking the remove button clears the line from vendor orders
        remove_res = self.client.post(reverse('vendor-order-list'), {
            'action': 'remove_order',
            'order_id': order.pk
        })
        self.assertEqual(remove_res.status_code, 302)
        self.assertFalse(PurchaseOrder.objects.filter(pk=order.pk).exists())

    def test_vendor_orders_edit_order(self):
        """
        Validates editing an existing company/vendor order:
        modifying company name, box type, weight, quantity, and delivery date.
        """
        import datetime
        from django.urls import reverse
        from transactions.models import PurchaseOrder

        # 1. Create an initial order
        init_date = (self.today + datetime.timedelta(days=3)).isoformat()
        create_res = self.client.post(reverse('vendor-order-list'), {
            'action': 'add_order',
            'company_name': 'Original Corp',
            'box_name': 'Original Box',
            'weight': '6.000',
            'quantity': '10',
            'expected_delivery_date': init_date
        })
        self.assertEqual(create_res.status_code, 302)

        order = PurchaseOrder.objects.filter(vendor__company_name='Original Corp').first()
        self.assertIsNotNone(order)
        self.assertEqual(order.quantity, 10)
        self.assertEqual(order.item.weight, Decimal('6.000'))

        # 2. POST edit_order: update company, box name, weight, quantity, delivery date
        new_date = (self.today + datetime.timedelta(days=14)).isoformat()
        edit_res = self.client.post(reverse('vendor-order-list'), {
            'action': 'edit_order',
            'order_id': order.pk,
            'company_name': 'Apex Electricals Updated',
            'box_name': 'Super Core Box',
            'weight': '8.250',
            'quantity': '45',
            'expected_delivery_date': new_date
        })
        self.assertEqual(edit_res.status_code, 302)

        order.refresh_from_db()
        self.assertEqual(order.vendor.company_name, 'Apex Electricals Updated')
        self.assertEqual(order.item.name, 'Super Core Box')
        self.assertEqual(order.item.weight, Decimal('8.250'))
        self.assertEqual(order.quantity, 45)
        self.assertEqual(order.expected_delivery_date.isoformat(), new_date)

        # 3. GET /vendor-orders/ renders updated details, edit modal, and no Total Boxes Demanded
        get_res = self.client.get(reverse('vendor-order-list'))
        self.assertEqual(get_res.status_code, 200)
        self.assertContains(get_res, 'Apex Electricals Updated')
        self.assertContains(get_res, 'Super Core Box')
        self.assertContains(get_res, '45')
        self.assertContains(get_res, 'openEditOrderModal')
        self.assertContains(get_res, 'editOrderModal')
        self.assertNotContains(get_res, 'Total Boxes Demanded')

    def test_vendor_orders_auto_create_company_on_the_fly(self):
        """
        Validates that adding an order for a new company automatically creates
        the company contact record without needing a separate Add Vendor feature.
        """
        import datetime
        from django.urls import reverse
        from transactions.models import Contact, ContactType, PurchaseOrder

        future_date = (self.today + datetime.timedelta(days=10)).isoformat()
        response = self.client.post(reverse('vendor-order-list'), {
            'action': 'add_order',
            'company_name': 'Precision Engineering Works',
            'box_name': 'New Cast Box',
            'quantity': '25',
            'expected_delivery_date': future_date
        })
        self.assertEqual(response.status_code, 302)

        vendor = Contact.objects.filter(company_name='Precision Engineering Works').first()
        self.assertIsNotNone(vendor)
        self.assertEqual(vendor.type, ContactType.VENDOR)

        order = PurchaseOrder.objects.filter(vendor=vendor).first()
        self.assertIsNotNone(order)
        self.assertEqual(order.quantity, 25)
        self.assertEqual(order.item.name, 'New Cast Box')

        # Remove line
        del_res = self.client.post(reverse('vendor-order-list'), {
            'action': 'remove_order',
            'order_id': order.pk
        })
        self.assertEqual(del_res.status_code, 302)
        self.assertFalse(PurchaseOrder.objects.filter(pk=order.pk).exists())

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

    def test_today_boxes_resets_on_new_day_and_updates_accurately(self):
        """
        Validates that when an employee makes boxes on Day 1 (yesterday),
        on Day 2 (today / tomorrow):
        1. It does NOT show yesterday's boxes as today's boxes.
        2. When boxes are added on Day 2, it updates today's count to strictly
           what was manufactured on Day 2.
        3. Month total reflects both days combined.
        """
        from datetime import timedelta
        from django.urls import reverse
        from inventory.models import Employee, ProductionLog
        from inventory.views import EmployeeListView

        worker = Employee.objects.create(
            first_name='DayWorker',
            last_name='Test',
            is_active=True
        )

        yesterday = self.today - timedelta(days=1)

        # Day 1: Employee manufactured 20 boxes yesterday
        ProductionLog.objects.create(
            employee=worker,
            item=self.item,
            quantity=20,
            production_date=yesterday
        )

        # On Day 2 (today): Check EmployeeListView before adding any boxes today
        view = EmployeeListView()
        qs = view.get_queryset()
        worker_state = qs.get(pk=worker.pk)

        # Today boxes must be 0 (NOT 20 from yesterday!)
        self.assertEqual(worker_state.today_boxes, 0)
        # Monthly total includes yesterday's 20 boxes (if same month)
        if yesterday.month == self.today.month:
            self.assertEqual(worker_state.total_boxes, 20)

        # UI GET /employees/ must not show (20 today) for DayWorker
        res = self.client.get(reverse('employee-list'))
        self.assertEqual(res.status_code, 200)
        self.assertNotContains(res, '(20 today)')

        # Day 2: Now add 15 boxes today
        post_res = self.client.post(reverse('employee-list'), {
            'action': 'log_production',
            'employee_id': worker.pk,
            'quantity': '15',
            'box_name': 'Test GI Core Box'
        })
        self.assertEqual(post_res.status_code, 302)

        # Today's boxes must now be strictly 15 (NOT 20, and NOT 35!)
        qs_after = view.get_queryset()
        worker_after = qs_after.get(pk=worker.pk)
        self.assertEqual(worker_after.today_boxes, 15)

        # If in same month, total boxes is 20 + 15 = 35
        if yesterday.month == self.today.month:
            self.assertEqual(worker_after.total_boxes, 35)

        # UI GET /employees/ must now display (15 today)
        res_after = self.client.get(reverse('employee-list'))
        self.assertEqual(res_after.status_code, 200)
        self.assertContains(res_after, '(15 today)')

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
        Validates the table renders only Name of Product, Quantity, Weight, and Action,
        without Classification or total weight calculations.
        """
        from django.urls import reverse

        self.item.weight = Decimal('3.500')
        self.item.save(update_fields=['weight'])

        response = self.client.get(reverse('add-inventory'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'All Products Inventory Details')
        self.assertContains(response, 'Test GI Core Box')
        self.assertContains(response, 'Name of Product')
        self.assertContains(response, 'Quantity')
        self.assertContains(response, 'Action')
        # Weight calculations and column must NOT be in the table
        self.assertNotContains(response, '<th class="px-3 py-3.5">Weight</th>')
        self.assertNotContains(response, 'Classification')
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

    def test_remove_stock_with_name_weight_and_quantity_only(self):
        """
        Validates removing stock using specifically Name of Product, Weight of Core Box,
        and Quantity only, correctly distinguishing between different weights.
        """
        from django.urls import reverse
        from inventory.models import Item, ItemType

        box1 = Item.objects.create(
            name='Precision Casting Box',
            sku='FG-BOX-PC-01',
            type=ItemType.FINISHED_GOOD,
            unit_of_measure='pcs',
            current_stock=Decimal('20.0000'),
            weight=Decimal('3.500')
        )
        box2 = Item.objects.create(
            name='Precision Casting Box',
            sku='FG-BOX-PC-02',
            type=ItemType.FINISHED_GOOD,
            unit_of_measure='pcs',
            current_stock=Decimal('30.0000'),
            weight=Decimal('5.200')
        )

        response = self.client.post(reverse('add-inventory'), {
            'action': 'remove_stock',
            'name': 'Precision Casting Box',
            'weight': '3.500',
            'quantity': '6'
        })
        self.assertEqual(response.status_code, 302)

        box1.refresh_from_db()
        box2.refresh_from_db()
        self.assertEqual(box1.current_stock, Decimal('14.0000'))
        self.assertEqual(box2.current_stock, Decimal('30.0000'))

        # Verify page does not contain obsolete removal reason fields
        get_res = self.client.get(reverse('add-inventory'))
        self.assertEqual(get_res.status_code, 200)
        self.assertNotContains(get_res, 'Reason for Stock Removal')
        self.assertNotContains(get_res, 'Reason for Removal')

    def test_delete_product_when_stock_is_zero_succeeds(self):
        """
        Validates that a product with zero stock can be completely removed from inventory.
        """
        from django.urls import reverse
        from inventory.models import Item, ItemType, UnitOfMeasure

        zero_item = Item.objects.create(
            sku='CB-ZERO-TEST',
            name='Zero Stock Core Box',
            type=ItemType.FINISHED_GOOD,
            unit_of_measure=UnitOfMeasure.PIECE,
            current_stock=Decimal('0.0000'),
            weight=Decimal('2.500'),
            is_active=True
        )

        response = self.client.post(reverse('add-inventory'), {
            'action': 'delete_product',
            'item_id': zero_item.pk
        })
        self.assertEqual(response.status_code, 302)

        # Must no longer be active in inventory
        active_items = Item.objects.filter(is_active=True, pk=zero_item.pk)
        self.assertFalse(active_items.exists())

        # First GET consumes the success flash message confirming removal
        first_get = self.client.get(reverse('add-inventory'))
        self.assertContains(first_get, 'has been completely removed from inventory')

        # Subsequent GET confirms the item is completely absent from the page
        second_get = self.client.get(reverse('add-inventory'))
        self.assertNotContains(second_get, 'Zero Stock Core Box')
        self.assertNotContains(second_get, 'CB-ZERO-TEST')

    def test_delete_product_when_stock_greater_than_zero_fails(self):
        """
        Validates that attempting to remove a product with stock > 0 fails and keeps the product.
        """
        from django.urls import reverse
        from inventory.models import Item

        response = self.client.post(reverse('add-inventory'), {
            'action': 'delete_product',
            'item_id': self.item.pk  # Has stock 20
        })
        self.assertEqual(response.status_code, 302)

        self.item.refresh_from_db()
        self.assertTrue(self.item.is_active)
        self.assertEqual(self.item.current_stock, Decimal('20.0000'))

    def test_table_shows_delete_button_for_zero_stock_product(self):
        """
        Validates that the table renders the Remove from Inventory action when product stock is 0,
        and does not show Add or Edit buttons in that row.
        """
        from django.urls import reverse
        from inventory.models import Item, ItemType, UnitOfMeasure

        zero_item = Item.objects.create(
            sku='CB-DISP-ZERO',
            name='Empty Core Box',
            type=ItemType.FINISHED_GOOD,
            unit_of_measure=UnitOfMeasure.PIECE,
            current_stock=Decimal('0.0000'),
            weight=Decimal('4.000'),
            is_active=True
        )

        response = self.client.get(reverse('add-inventory'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Empty Core Box')
        self.assertContains(response, 'openDeleteProductModal')
        self.assertContains(response, 'Remove from Inventory')

    def test_remove_stock_reaching_zero_removes_product_completely(self):
        """
        Validates that removing stock to 0 completely removes the product from inventory.
        """
        from django.urls import reverse
        from inventory.models import Item, ItemType

        active_box = Item.objects.create(
            name='Depletable Box',
            sku='FG-DEPLETE-01',
            type=ItemType.FINISHED_GOOD,
            unit_of_measure='pcs',
            current_stock=Decimal('10.0000'),
            weight=Decimal('2.500'),
            is_active=True
        )

        response = self.client.post(reverse('add-inventory'), {
            'action': 'remove_stock',
            'item_id': active_box.pk,
            'quantity': '10'
        })
        self.assertEqual(response.status_code, 302)

        # Product is completely removed from active inventory
        self.assertFalse(Item.objects.filter(pk=active_box.pk, is_active=True).exists())

        # Page confirms zero stock removal
        get_res = self.client.get(reverse('add-inventory'))
        self.assertContains(get_res, 'Stock reached zero and product has been completely removed from inventory')
        self.assertNotContains(get_res, 'FG-DEPLETE-01')

    def test_edit_product_quantity_to_zero_removes_product_completely(self):
        """
        Validates that editing product quantity to 0 completely removes the product from inventory.
        """
        from django.urls import reverse
        from inventory.models import Item, ItemType

        edit_box = Item.objects.create(
            name='Zero Out Box',
            sku='FG-ZERO-OUT-01',
            type=ItemType.FINISHED_GOOD,
            unit_of_measure='pcs',
            current_stock=Decimal('5.0000'),
            weight=Decimal('3.000'),
            is_active=True
        )

        response = self.client.post(reverse('add-inventory'), {
            'action': 'edit_product',
            'item_id': edit_box.pk,
            'name': 'Zero Out Box',
            'quantity': '0',
            'weight': '3.000'
        })
        self.assertEqual(response.status_code, 302)

        self.assertFalse(Item.objects.filter(pk=edit_box.pk, is_active=True).exists())

    def test_edit_product_success(self):
        """
        Validates modifying product details (name, quantity, weight) when mistake was made.
        """
        from django.urls import reverse
        from transactions.models import InventoryLedger, TransactionType

        # self.item initially has: name='Test GI Core Box', stock=20, weight=Decimal('0.000')
        response = self.client.post(reverse('add-inventory'), {
            'action': 'edit_product',
            'item_id': self.item.pk,
            'name': 'Corrected GI Core Box 110mm',
            'quantity': '35',
            'weight': '4.250'
        })
        self.assertEqual(response.status_code, 302)

        self.item.refresh_from_db()
        self.assertEqual(self.item.name, 'Corrected GI Core Box 110mm')
        self.assertEqual(self.item.current_stock, Decimal('35.0000'))
        self.assertEqual(self.item.weight, Decimal('4.250'))

        # Verify InventoryLedger entry for delta (+15)
        ledger = InventoryLedger.objects.filter(item=self.item, generic_reference_id='EDIT-CORRECTION').first()
        self.assertIsNotNone(ledger)
        self.assertEqual(ledger.quantity_changed, Decimal('15.0000'))
        self.assertEqual(ledger.transaction_type, TransactionType.IN)

    def test_edit_product_negative_values_rejected(self):
        """
        Validates that negative quantity or weight is rejected.
        """
        from django.urls import reverse

        initial_name = self.item.name
        initial_stock = self.item.current_stock

        response = self.client.post(reverse('add-inventory'), {
            'action': 'edit_product',
            'item_id': self.item.pk,
            'name': 'Negative Stock Test',
            'quantity': '-10',
            'weight': '3.000'
        })
        self.assertEqual(response.status_code, 302)

        self.item.refresh_from_db()
        self.assertEqual(self.item.name, initial_name)
        self.assertEqual(self.item.current_stock, initial_stock)

    def test_table_renders_edit_button_and_modal(self):
        """
        Validates that the Inventory table renders the Edit button and edit modal.
        """
        from django.urls import reverse

        response = self.client.get(reverse('add-inventory'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'openEditProductModal')
        self.assertContains(response, 'editProductModal')
        self.assertContains(response, 'Edit Product Details')

    def test_operational_views_require_authentication(self):
        """Assert unauthenticated requests to operational views redirect to admin login."""
        from django.urls import reverse
        self.client.logout()
        for route_name in ['inventory-home', 'add-inventory', 'employee-list', 'vendor-order-list']:
            res = self.client.get(reverse(route_name))
            self.assertEqual(res.status_code, 302)
            self.assertIn('/admin/login/', res.url)

    def test_inventory_cards_show_individual_box_totals(self):
        """
        Validates that add-inventory displays dynamic individual total stock cards
        for each type of box (e.g. 1 card for 1 box, 2 for 2, 3 for 3) instead
        of a single generic Total Units in Stock card.
        """
        from django.urls import reverse
        from inventory.models import Item, ItemType

        # 1. Case: 1 Box in Inventory
        res1 = self.client.get(reverse('add-inventory'))
        self.assertEqual(res1.status_code, 200)
        self.assertContains(res1, 'Test GI Core Box')
        self.assertContains(res1, '20')
        self.assertContains(res1, 'Total Stock')
        self.assertNotContains(res1, 'Total Units in Stock')

        # 2. Case: 2 Boxes in Inventory
        Item.objects.create(
            name='Precision Cast Box',
            sku='FG-BOX-PREC',
            type=ItemType.FINISHED_GOOD,
            unit_of_measure='pcs',
            current_stock=Decimal('35.0000'),
            weight=Decimal('5.200')
        )
        res2 = self.client.get(reverse('add-inventory'))
        self.assertEqual(res2.status_code, 200)
        self.assertContains(res2, 'Test GI Core Box')
        self.assertContains(res2, 'Precision Cast Box')
        self.assertContains(res2, '35')

        # 3. Case: 3 Boxes in Inventory
        Item.objects.create(
            name='Heavy Duty Core Box',
            sku='FG-BOX-HEAVY',
            type=ItemType.FINISHED_GOOD,
            unit_of_measure='pcs',
            current_stock=Decimal('12.0000'),
            weight=Decimal('9.800')
        )
        res3 = self.client.get(reverse('add-inventory'))
        self.assertEqual(res3.status_code, 200)
        self.assertContains(res3, 'Test GI Core Box')
        self.assertContains(res3, 'Precision Cast Box')
        self.assertContains(res3, 'Heavy Duty Core Box')
        self.assertContains(res3, '12')


class DailyCoreBoxFeatureTestCase(TestCase):
    """
    Tests for the Daily Core Box feature:
    - Adding companies
    - Logging daily core box dispatches (company, core box type, quantity, date)
    - Viewing all companies with total core boxes taken
    - Date-wise detail page for each company with total core box taken top card
    - Editing and deleting dispatches
    """

    def setUp(self):
        from django.contrib.auth.models import User
        from django.urls import reverse
        from inventory.models import Company, DailyCoreBoxDispatch, Item, ItemType

        self.user = User.objects.create_user(username='dispatchmgr', password='SecretPassword123!')
        self.client.force_login(self.user)

        self.item = Item.objects.create(
            name='GI Core Box Std',
            sku='FG-BOX-STD',
            type=ItemType.FINISHED_GOOD,
            unit_of_measure='pcs',
            current_stock=Decimal('50.0000')
        )

        self.company = Company.objects.create(name='Precision Auto Ltd')

    def test_daily_core_box_list_get(self):
        """Test GET request to /daily-core-box/ loads with company list and form."""
        from django.urls import reverse
        res = self.client.get(reverse('daily-core-box-list'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Daily Core Box Dispatches')
        self.assertContains(res, 'Add Company')
        self.assertContains(res, 'Record Daily Core Box Taken')
        self.assertContains(res, 'Precision Auto Ltd')

    def test_add_company_success_and_duplicate(self):
        """Test adding new company and preventing duplicates."""
        from django.urls import reverse
        from inventory.models import Company

        # Add unique company
        res = self.client.post(reverse('daily-core-box-list'), {
            'action': 'add_company',
            'company_name': 'Apex Foundry Corp'
        }, follow=True)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(Company.objects.filter(name='Apex Foundry Corp').exists())
        self.assertContains(res, "Apex Foundry Corp")
        self.assertContains(res, "added successfully")

        # Attempt to add same company name
        res_dup = self.client.post(reverse('daily-core-box-list'), {
            'action': 'add_company',
            'company_name': 'Apex Foundry Corp'
        }, follow=True)
        self.assertContains(res_dup, "already exists")

    def test_add_entry_with_existing_and_new_company(self):
        """Test recording daily core box dispatches."""
        from django.urls import reverse
        from inventory.models import DailyCoreBoxDispatch, Company

        # 1. Entry for existing company
        res = self.client.post(reverse('daily-core-box-list'), {
            'action': 'add_entry',
            'company_name': 'Precision Auto Ltd',
            'box_type': 'GI Core Box Std',
            'quantity': '25',
            'dispatch_date': '2026-10-28',
        }, follow=True)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Successfully recorded 25 boxes for Precision Auto Ltd")
        self.assertTrue(DailyCoreBoxDispatch.objects.filter(company=self.company, quantity=25).exists())

        # 2. Entry for brand new company (auto-creation)
        res_new = self.client.post(reverse('daily-core-box-list'), {
            'action': 'add_entry',
            'company_name': 'Brand New Castings',
            'box_type': 'Heavy Core Box 10kg',
            'quantity': '40',
            'dispatch_date': '2026-10-28',
        }, follow=True)
        self.assertEqual(res_new.status_code, 200)
        new_comp = Company.objects.filter(name='Brand New Castings').first()
        self.assertIsNotNone(new_comp)
        self.assertTrue(DailyCoreBoxDispatch.objects.filter(company=new_comp, quantity=40).exists())

    def test_add_entry_invalid_quantity(self):
        """Test rejection of non-positive quantities."""
        from django.urls import reverse
        res = self.client.post(reverse('daily-core-box-list'), {
            'action': 'add_entry',
            'company_name': 'Precision Auto Ltd',
            'box_type': 'GI Core Box Std',
            'quantity': '-5',
        }, follow=True)
        self.assertContains(res, "Quantity must be a positive number greater than 0")

    def test_company_detail_view_and_top_card(self):
        """Test company detail page shows total core box taken card and date-wise rows."""
        from django.urls import reverse
        from inventory.models import DailyCoreBoxDispatch

        # Create two date-wise dispatch entries
        DailyCoreBoxDispatch.objects.create(
            company=self.company,
            box_type='GI Core Box Std',
            quantity=30,
            dispatch_date=datetime.date(2026, 10, 27)
        )
        DailyCoreBoxDispatch.objects.create(
            company=self.company,
            box_type='Heavy Core Box',
            quantity=45,
            dispatch_date=datetime.date(2026, 10, 28)
        )

        res = self.client.get(reverse('daily-core-box-detail', kwargs={'pk': self.company.pk}))
        self.assertEqual(res.status_code, 200)
        # Total core boxes taken = 30 + 45 = 75
        self.assertContains(res, '75')
        self.assertContains(res, 'Total Core Box Taken')
        self.assertContains(res, 'GI Core Box Std')
        self.assertContains(res, 'Heavy Core Box')
        self.assertContains(res, '30 Boxes')
        self.assertContains(res, '45 Boxes')
        self.assertContains(res, '27 Oct 2026')
        self.assertContains(res, '28 Oct 2026')

    def test_company_detail_add_edit_delete_entry(self):
        """Test adding, editing, and deleting dispatches on the company detail page."""
        from django.urls import reverse
        from inventory.models import DailyCoreBoxDispatch

        # 1. Add entry
        res_add = self.client.post(reverse('daily-core-box-detail', kwargs={'pk': self.company.pk}), {
            'action': 'add_entry',
            'box_type': 'Pattern Core Box',
            'quantity': '60',
            'dispatch_date': '2026-10-28',
        }, follow=True)
        self.assertEqual(res_add.status_code, 200)
        entry = DailyCoreBoxDispatch.objects.filter(company=self.company, box_type='Pattern Core Box').first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.quantity, 60)

        # 2. Edit entry
        res_edit = self.client.post(reverse('daily-core-box-detail', kwargs={'pk': self.company.pk}), {
            'action': 'edit_entry',
            'entry_id': str(entry.pk),
            'box_type': 'Pattern Core Box Updated',
            'quantity': '85',
            'dispatch_date': '2026-10-28',
        }, follow=True)
        self.assertEqual(res_edit.status_code, 200)
        entry.refresh_from_db()
        self.assertEqual(entry.quantity, 85)
        self.assertEqual(entry.box_type, 'Pattern Core Box Updated')

        # 3. Delete entry
        res_del = self.client.post(reverse('daily-core-box-detail', kwargs={'pk': self.company.pk}), {
            'action': 'delete_entry',
            'entry_id': str(entry.pk),
        }, follow=True)
        self.assertEqual(res_del.status_code, 200)
        self.assertFalse(DailyCoreBoxDispatch.objects.filter(pk=entry.pk).exists())

