from decimal import Decimal
from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class ItemType(models.TextChoices):
    RAW_MATERIAL = 'Raw Material', _('Raw Material')
    FINISHED_GOOD = 'Finished Good', _('Finished Good')


class UnitOfMeasure(models.TextChoices):
    KILOGRAM = 'kg', _('Kilogram (kg)')
    GRAM = 'g', _('Gram (g)')
    LITER = 'l', _('Liter (l)')
    MILLILITER = 'ml', _('Milliliter (ml)')
    METER = 'm', _('Meter (m)')
    PIECE = 'pcs', _('Pieces (pcs)')
    BOX = 'box', _('Box (box)')
    ROLL = 'roll', _('Roll (roll)')


class Item(models.Model):
    """
    Core item catalog representing Raw Materials and Finished Goods.
    Maintains real-time stock balances and Moving Weighted Average Cost (WAC).
    """
    type = models.CharField(
        max_length=20,
        choices=ItemType.choices,
        default=ItemType.RAW_MATERIAL,
        db_index=True,
        help_text=_("Categorizes whether this item is procured as raw stock or produced in-house.")
    )
    sku = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text=_("Stock Keeping Unit - unique alphanumeric item identifier.")
    )
    name = models.CharField(
        max_length=255,
        db_index=True,
        help_text=_("Descriptive name of the inventory item.")
    )
    description = models.TextField(
        blank=True,
        help_text=_("Detailed engineering or procurement specifications.")
    )
    unit_of_measure = models.CharField(
        max_length=20,
        choices=UnitOfMeasure.choices,
        default=UnitOfMeasure.PIECE,
        help_text=_("Standard base unit used for tracking and billing.")
    )
    current_stock = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        default=Decimal('0.0000'),
        help_text=_("Real-time physical stock on hand. Controlled via InventoryLedger transactions.")
    )
    weight = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name=_('Weight (kg)'),
        help_text=_("Unit weight of the product / core box in kilograms (kg).")
    )
    moving_average_cost = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        default=Decimal('0.0000'),
        help_text=_("Moving Weighted Average Cost per unit in base currency.")
    )
    is_active = models.BooleanField(
        default=True,
        help_text=_("Determines if this item is active for procurement, production, and sales.")
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = _('Item')
        verbose_name_plural = _('Items')
        constraints = [
            models.CheckConstraint(
                condition=models.Q(current_stock__gte=0),
                name='item_current_stock_non_negative'
            ),
            models.CheckConstraint(
                condition=models.Q(weight__gte=0),
                name='item_weight_non_negative'
            ),
            models.CheckConstraint(
                condition=models.Q(moving_average_cost__gte=0),
                name='item_moving_avg_cost_non_negative'
            )
        ]
        indexes = [
            models.Index(fields=['sku', 'type']),
            models.Index(fields=['created_at']),
        ]

    @property
    def total_weight(self) -> Decimal:
        """
        Total weight of physical on-hand stock in kilograms (kg).
        """
        if self.weight and self.current_stock:
            return (self.current_stock * self.weight).quantize(Decimal('0.001'))
        return Decimal('0.000')

    def clean(self):
        super().clean()
        if self.sku:
            self.sku = self.sku.strip().upper()

    def __str__(self) -> str:
        return f"[{self.sku}] {self.name} ({self.get_type_display()})"


class BillOfMaterials(models.Model):
    """
    Bill of Materials (BOM) specifying the recipe of raw materials required
    to produce one unit of a finished good.
    """
    finished_good = models.ForeignKey(
        Item,
        on_delete=models.CASCADE,
        related_name='bill_of_materials',
        limit_choices_to={'type': ItemType.FINISHED_GOOD},
        help_text=_("The finished good produced by this Bill of Materials.")
    )
    version = models.CharField(
        max_length=20,
        default='1.0',
        help_text=_("Revision or version code (e.g. 1.0, 2.0).")
    )
    description = models.TextField(
        blank=True,
        help_text=_("Engineering change notes or assembly instructions.")
    )
    raw_materials = models.ManyToManyField(
        Item,
        through='BOMComponent',
        related_name='used_in_boms',
        help_text=_("Raw materials required for assembly.")
    )
    is_active = models.BooleanField(
        default=True,
        help_text=_("Designates whether this BOM version is currently approved for production.")
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_active', 'finished_good__name', 'version']
        verbose_name = _('Bill of Materials')
        verbose_name_plural = _('Bills of Materials')
        constraints = [
            models.UniqueConstraint(
                fields=['finished_good', 'version'],
                name='unique_bom_version_per_finished_good'
            )
        ]

    def clean(self):
        super().clean()
        if self.finished_good and self.finished_good.type != ItemType.FINISHED_GOOD:
            raise ValidationError({
                'finished_good': _("A Bill of Materials can only be created for items of type 'Finished Good'.")
            })

    def __str__(self) -> str:
        return f"BOM: {self.finished_good.name} (v{self.version})"


class BOMComponent(models.Model):
    """
    Through-model defining the specific quantity of a raw material required
    per unit of the finished good in a Bill of Materials.
    """
    bom = models.ForeignKey(
        BillOfMaterials,
        on_delete=models.CASCADE,
        related_name='components',
        help_text=_("Parent Bill of Materials specification.")
    )
    raw_material = models.ForeignKey(
        Item,
        on_delete=models.PROTECT,
        related_name='bom_usages',
        limit_choices_to={'type': ItemType.RAW_MATERIAL},
        help_text=_("Raw material component to consume during manufacturing.")
    )
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        help_text=_("Quantity of raw material required per unit of the finished good.")
    )
    scrap_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text=_("Expected machining loss or scrap percentage (e.g. 2.50 for 2.5%).")
    )

    class Meta:
        ordering = ['bom', 'raw_material__name']
        verbose_name = _('BOM Component')
        verbose_name_plural = _('BOM Components')
        constraints = [
            models.UniqueConstraint(
                fields=['bom', 'raw_material'],
                name='unique_raw_material_per_bom'
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name='bom_component_quantity_positive'
            ),
            models.CheckConstraint(
                condition=models.Q(scrap_percentage__gte=0),
                name='bom_component_scrap_non_negative'
            )
        ]

    def clean(self):
        super().clean()
        if self.bom_id and self.raw_material_id:
            if self.raw_material == self.bom.finished_good:
                raise ValidationError({
                    'raw_material': _("A finished good cannot use itself as a raw material component.")
                })
            if self.raw_material.type != ItemType.RAW_MATERIAL:
                raise ValidationError({
                    'raw_material': _("Only items of type 'Raw Material' can be used as BOM components.")
                })

    def __str__(self) -> str:
        return f"{self.quantity} {self.raw_material.get_unit_of_measure_display()} x {self.raw_material.name}"


class Employee(models.Model):
    """
    Workforce personnel and machine operators for manufacturing and shop-floor productivity tracking.
    """
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    is_active = models.BooleanField(
        default=True,
        help_text=_("Designates whether this worker is actively employed.")
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['last_name', 'first_name']
        verbose_name = _('Employee')
        verbose_name_plural = _('Employees')
        indexes = [
            models.Index(fields=['last_name', 'first_name']),
            models.Index(fields=['is_active']),
        ]

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    def __str__(self) -> str:
        return self.full_name


class ProductionLog(models.Model):
    """
    Tracks daily manufacturing output and workforce productivity per employee.
    """
    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name='production_logs',
        help_text=_("The operator who executed the production order.")
    )
    item = models.ForeignKey(
        Item,
        on_delete=models.PROTECT,
        related_name='production_logs',
        help_text=_("The item (typically Finished Good) manufactured.")
    )
    quantity = models.IntegerField(
        help_text=_("Number of completed units produced.")
    )
    production_date = models.DateField(
        default=timezone.localdate,
        db_index=True,
        help_text=_("Date when production was completed.")
    )

    class Meta:
        ordering = ['-production_date', '-id']
        verbose_name = _('Production Log')
        verbose_name_plural = _('Production Logs')
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name='production_log_quantity_positive'
            )
        ]
        indexes = [
            models.Index(fields=['employee', 'production_date']),
            models.Index(fields=['item', 'production_date']),
        ]

    def __str__(self) -> str:
        return f"[{self.production_date}] {self.employee.full_name}: {self.quantity} x {self.item.sku}"
