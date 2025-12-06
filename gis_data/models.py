from django.contrib.gis.db import models

class PointOfInterest(models.Model):
    name = models.CharField(max_length=255, verbose_name="Nome del Punto")
    category = models.CharField(max_length=100, verbose_name="Categoria")
    # srid=4326 is the standard WGS 84 used by GPS.
    location = models.PointField(srid=4326, verbose_name="Posizione GPS")
    description = models.TextField(blank=True, null=True, verbose_name="Descrizione")

    class Meta:
        verbose_name = "Point of Interest"
        verbose_name_plural = "Point of Interests"

    def __str__(self):
        return self.name

class ScenicArea(models.Model):
    name = models.CharField(max_length=255, verbose_name="Nome dell'Area")
    area_type = models.CharField(max_length=100, verbose_name="Tipo di Area")
    bonus_value = models.IntegerField(default=0, verbose_name="Valore Bonus")
    area = models.MultiPolygonField(srid=4326, verbose_name="Area Geografica")

    class Meta:
        verbose_name = "Scenic Area"
        verbose_name_plural = "Scenic Areas"

    def __str__(self):
        return self.name