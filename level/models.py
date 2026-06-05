from django.db import models


class Level(models.Model):
    """Team skill level referential (e.g. discovery -> excellence).

    Catalog managed by admins. Each team optionally references one via
    Team.level. Mirrors the AttendanceStatus / Sport taxonomy pattern:
    code + translated label/description + order + is_active + soft delete.
    """

    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)  # translated
    description = models.CharField(max_length=255, blank=True)  # translated
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "code"]

    def __str__(self):
        return self.name
