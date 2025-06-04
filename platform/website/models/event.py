from django.db import models
from django.utils.text import slugify
from ._base import BaseModel


class Event(BaseModel):
    title = models.CharField(max_length=255)
    description = models.TextField()
    banner = models.ImageField(upload_to='event_banners/')
    
    event_date = models.DateField()
    event_time = models.TimeField(null=True, blank=True)

    display_start = models.DateField()
    display_end = models.DateField()

    location = models.CharField(max_length=255, blank=True)
    link_more_info = models.URLField(blank=True, null=True)

    slug = models.SlugField(unique=True, blank=True, max_length=200)

    def __str__(self):
        return self.title

    def is_visible(self):
        from django.utils import timezone
        today = timezone.now().date()
        return self.display_start <= today <= self.display_end
    
    def save(self, *args, **kwargs):
        if not self.slug:
            truncated_name = self.title[:50]
            base_slug = slugify(truncated_name)
            slug = base_slug
            num = 1

            while Event.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{num}"
                num += 1
            self.slug = slug[:50]

        super().save(*args, **kwargs)
