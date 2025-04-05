from django.db import models
from PIL import Image
from io import BytesIO
from django.core.files.base import ContentFile
from ._base import BaseModel
from .ministry import Ministry
from .neighborhood import Neighborhood


class Member(BaseModel):
    CONVERSION_CHOICES = [
        ('new_convert', 'Novo Convertido'),
        ('reconciled', 'Reconciliado'),
        ('from_another_church', 'Vindo de outra Igreja'),
    ]

    SPIRITUAL_MATURITY_CHOICES = [
        ('immature', 'Imaturo'),
        ('intermediate', 'Intermediário'),
        ('mature', 'Maduro'),
    ]

    GENDER_CHOICES = [
        ('male', 'Masculino'),
        ('female', 'Feminino')
    ]

    MARITAL_STATUS_CHOICES = [
        ('single', 'Solteiro(a)'),
        ('married', 'Casado(a)'),
        ('divorced', 'Divorciado(a)'),
        ('widowed', 'Viúvo(a)'),
    ]

    user = models.OneToOneField('User', on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(max_length=150)
    profile_picture = models.ImageField(
        upload_to='profile_pictures/',
        blank=True,
        null=True
    )
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    neighborhood = models.ForeignKey(Neighborhood, on_delete=models.SET_NULL, blank=True, null=True)
    birth_date = models.DateField(blank=True, null=True)
    tags = models.CharField(max_length=255, blank=True, null=True)

    conversion = models.CharField(max_length=20, choices=CONVERSION_CHOICES, blank=True, null=True)
    conversion_date = models.DateField(blank=True, null=True)
    spiritual_maturity = models.CharField(max_length=20, choices=SPIRITUAL_MATURITY_CHOICES, blank=True, null=True)

    personality_type = models.CharField(max_length=50, blank=True, null=True)
    interests = models.TextField(blank=True, null=True)
    available_days = models.CharField(max_length=100, blank=True, null=True)

    marital_status = models.CharField(max_length=20, choices=MARITAL_STATUS_CHOICES, blank=True, null=True)
    spouse = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='partner')

    testimony = models.TextField(blank=True, null=True)
    initial_challenges = models.TextField(blank=True, null=True)

    ministry = models.ForeignKey(Ministry, on_delete=models.SET_NULL, blank=True, null=True)
    is_active = models.BooleanField(default=True)

    is_available_to_consolidate = models.BooleanField(default=False)
    is_available_to_disciple = models.BooleanField(default=False)

    @property
    def email(self):
        return self.user.email if self.user else None

    @property
    def is_admin(self):
        return self.user.is_staff if self.user else False
    
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        if self.profile_picture:
            try:
                img = Image.open(self.profile_picture)

                max_size = (500, 500)
                img.thumbnail(max_size, Image.ANTIALIAS)

                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")

                buffer = BytesIO()
                img.save(buffer, format='JPEG', quality=85)

                self.profile_picture.save(
                    f'{self.pk}_profile.jpg',
                    ContentFile(buffer.getvalue()),
                    save=False
                )

                buffer.close()
                super().save(update_fields=['profile_picture'])
            except Exception as e:
                print(f"Erro ao processar imagem do membro {self.name}: {e}")

    def __str__(self):
        return self.name
