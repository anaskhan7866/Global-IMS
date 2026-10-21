from django.urls import path
from .views import EmployeeListView, EmployeeDetailView, VendorOrderListView, AddInventoryView

urlpatterns = [
    # Employee Productivity Views
    path('employees/', EmployeeListView.as_view(), name='employee-list'),
    path('employees/<int:pk>/', EmployeeDetailView.as_view(), name='employee-detail-default'),
    path('employees/<int:pk>/<int:month>/', EmployeeDetailView.as_view(), name='employee-detail'),
    path('employees/<int:pk>/month/<int:month>/', EmployeeDetailView.as_view(), name='employee-detail-month'),

    # Vendor Purchase Orders View
    path('vendor-orders/', VendorOrderListView.as_view(), name='vendor-order-list'),

    # Add Inventory Stock View
    path('add-inventory/', AddInventoryView.as_view(), name='add-inventory'),
]
