from django.urls import path

from . import views

# No trailing slashes: matches the paths in the assessment PDF.
urlpatterns = [
    path("junctions", views.JunctionList.as_view()),
    path("junctions/<str:junction_id>", views.JunctionDetail.as_view()),
    path("junctions/<str:junction_id>/status", views.JunctionDetail.as_view()),
    path("junctions/<str:junction_id>/commands", views.Commands.as_view()),
    path("junctions/<str:junction_id>/history", views.History.as_view()),
    path("sensor-events", views.SensorEvents.as_view()),
    path("controller-events", views.ControllerEvents.as_view()),
    path("junctions/<str:junction_id>/simulator", views.Simulator.as_view()),
]
