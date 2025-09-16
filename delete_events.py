from website.models.event import Event
from safedelete.models import HARD_DELETE

# Limpar eventos recorrentes existentes
print('Deletando eventos recorrentes existentes...')
deleted_count = Event.objects.filter(is_recurring=True).delete(force_policy=HARD_DELETE)
print(f'Eventos deletados: {deleted_count}')
