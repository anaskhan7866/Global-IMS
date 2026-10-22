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

admin.site.site_header = "Global-IMS Administration"
admin.site.site_title = "Global-IMS Admin Portal"
admin.site.index_title = "MRP & Production Control Center"
