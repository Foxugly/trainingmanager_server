from django.urls import path

from .views import DeviceListView, DeviceRegisterView, DeviceUnregisterView

urlpatterns = [
    path("devices/register/", DeviceRegisterView.as_view(), name="device-register"),
    path("devices/unregister/", DeviceUnregisterView.as_view(), name="device-unregister"),
    path("devices/", DeviceListView.as_view(), name="device-list"),
]
