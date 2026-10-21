"""
URL configuration for core_erp project.

Routes:
- /admin/ -> Django Admin Interface
- / -> Inventory Home & Stock Management
"""

from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('inventory.urls')),
    path('dashboard/', include('dashboard.urls', namespace='dashboard')),
]
