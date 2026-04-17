from django.db import models
from django.utils import timezone


class CanteenDebtor(models.Model):
    """Registro de fiado na cantina."""

    name = models.CharField('Nome', max_length=200)
    phone = models.CharField('Telefone', max_length=30, blank=True, default='')
    purchase_date = models.DateField('Data da compra', default=timezone.now)
    amount = models.DecimalField('Valor (R$)', max_digits=10, decimal_places=2)
    description = models.TextField('Descrição', blank=True, default='')
    paid = models.BooleanField('Pago', default=False)
    paid_date = models.DateField('Data do pagamento', null=True, blank=True)
    notes = models.TextField('Observações', blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Fiado'
        verbose_name_plural = 'Fiados'
        ordering = ['-purchase_date']

    def __str__(self):
        status = 'pago' if self.paid else 'pendente'
        return f'{self.name} — R$ {self.amount} ({status})'
