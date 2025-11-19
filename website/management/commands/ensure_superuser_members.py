from django.core.management.base import BaseCommand
from website.models.user import User
from website.models.member import Member


class Command(BaseCommand):
    help = 'Garante que todos os superusuários tenham um registro de membro associado'

    def handle(self, *args, **kwargs):
        superusers = User.objects.filter(is_superuser=True)
        
        created_count = 0
        updated_count = 0
        
        for user in superusers:
            # Verifica se já existe um membro associado
            if not hasattr(user, 'member'):
                # Cria um registro de membro para o superusuário
                member = Member.objects.create(
                    user=user,
                    name=user.first_name or user.email.split('@')[0],
                    phone=user.phone or '',
                    is_available_to_consolidate=True,
                    is_available_to_disciple=True
                )
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f'✓ Membro criado para superusuário: {user.email}')
                )
            else:
                # Atualiza o tipo de usuário para admin se necessário
                if user.user_type != 'admin':
                    user.user_type = 'admin'
                    user.save()
                    updated_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(f'✓ Tipo de usuário atualizado para admin: {user.email}')
                    )
        
        if created_count == 0 and updated_count == 0:
            self.stdout.write(
                self.style.SUCCESS('✓ Todos os superusuários já possuem membros associados')
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'\n✓ Processo concluído: {created_count} membros criados, {updated_count} atualizados'
                )
            )
