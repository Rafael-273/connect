from django.db import models
from ._base import BaseModel


class Music(BaseModel):
    name = models.CharField(max_length=200)
    singer = models.CharField(max_length=200)
    chord_sheet = models.FileField(
        upload_to='chord_sheet/',
        null=True,
        blank=True
    )

    def __str__(self):
        return f"{self.name} - {self.singer}"
