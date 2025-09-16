from website.models.event import Event

# Atualizar o tipo de link para cada evento
eventos = {
    'Culto de Domingo': 'more_info',
    'Culto de Jovens': 'instagram',
    'Reunião de Oração': 'more_info',
    'Estudo Bíblico': 'more_info',
    'Grupo de Mulheres': 'contact',
    'Ensaio do Coral': 'registration',
    'Grupo de Intercessão Matinal': 'more_info'
}

# Atualizar cada evento
for nome, tipo_link in eventos.items():
    try:
        evento = Event.objects.get(title=nome, is_recurring=True)
        evento.link_type = tipo_link
        evento.save()
        print(f"Evento '{nome}' atualizado com tipo de link '{tipo_link}'")
    except Event.DoesNotExist:
        print(f"Evento '{nome}' não encontrado")
    except Exception as e:
        print(f"Erro ao atualizar '{nome}': {e}")

print("Atualização concluída!")
