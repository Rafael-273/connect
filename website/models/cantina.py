from django.db import models
from django.utils import timezone
from ._base import BaseModel

class CanteenDebtor(BaseModel):
    name = models.CharField(max_length=255, verbose_name="Nome do Cliente")
    phone = models.CharField(max_length=20, blank=True, null=True, verbose_name="Número de WhatsApp")
    purchase_date = models.DateField(default=timezone.now, verbose_name="Data da Compra")
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Valor Devido")
    description = models.TextField(blank=True, null=True, verbose_name="Descrição dos Itens")
    paid = models.BooleanField(default=False, verbose_name="Pago")
    paid_date = models.DateField(blank=True, null=True, verbose_name="Data de Pagamento")
    notes = models.TextField(blank=True, null=True, verbose_name="Observações")
    
    def __str__(self):
        return f"{self.name} - R${self.amount} ({self.purchase_date})"
    
    class Meta:
        verbose_name = "Fiado da Cantina"
        verbose_name_plural = "Fiados da Cantina"
        ordering = ['-purchase_date']
