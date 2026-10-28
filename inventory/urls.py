from django.urls import path
from .views import (
    EmployeeListView,
    EmployeeDetailView,
    VendorOrderListView,
    AddInventoryView,
    DailyCoreBoxListView,
    DailyCoreBoxDetailView,
)

urlpatterns = [
    # Primary Inventory Route (Application Root)
    path('', AddInventoryView.as_view(), name='inventory-home'),
    path('inventory/', AddInventoryView.as_view(), name='inventory'),
    path('add-inventory/', AddInventoryView.as_view(), name='add-inventory'),

    # Employee Productivity Views
    path('employees/', EmployeeListView.as_view(), name='employee-list'),
    path('employees/<int:pk>/', EmployeeDetailView.as_view(), name='employee-detail-default'),
    path('employees/<int:pk>/<int:month>/', EmployeeDetailView.as_view(), name='employee-detail'),
    path('employees/<int:pk>/month/<int:month>/', EmployeeDetailView.as_view(), name='employee-detail-month'),

    # Vendor Purchase Orders View
    path('vendor-orders/', VendorOrderListView.as_view(), name='vendor-order-list'),

    # Daily Core Box Dispatches (Company Daily Tracking)
    path('daily-core-box/', DailyCoreBoxListView.as_view(), name='daily-core-box-list'),
    path('daily-core-box/company/<int:pk>/', DailyCoreBoxDetailView.as_view(), name='daily-core-box-detail'),
]
