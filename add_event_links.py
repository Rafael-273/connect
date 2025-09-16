from website.models.event import Event

# Adicionar links fictícios para cada evento
eventos = {
    'Culto de Domingo': 'https://exemplo.com/culto-domingo',
    'Culto de Jovens': 'https://instagram.com/jovens_igreja',
    'Reunião de Oração': 'https://exemplo.com/reuniao-oracao',
    'Estudo Bíblico': 'https://exemplo.com/estudo-biblico',
    'Grupo de Mulheres': 'https://exemplo.com/contato-mulheres',
    'Ensaio do Coral': 'https://exemplo.com/inscricao-coral',
    'Grupo de Intercessão Matinal': 'https://exemplo.com/intercessao-matinal'
}

# Atualizar cada evento
for nome, link in eventos.items():
    try:
        evento = Event.objects.get(title=nome, is_recurring=True)
        evento.link_more_info = link
        evento.save()
        print(f"Evento '{nome}' atualizado com link '{link}'")
    except Event.DoesNotExist:
        print(f"Evento '{nome}' não encontrado")
    except Exception as e:
        print(f"Erro ao atualizar '{nome}': {e}")

print("Links adicionados com sucesso!")
