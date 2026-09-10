from django.core.management.base import BaseCommand
from website.models.follow_up import FollowUpTemplate, FollowUpTemplateStep

class Command(BaseCommand):
    help = 'Cria templates padrão de consolidação'

    def handle(self, *args, **options):
        # Template Básico - 4 Semanas
        template_basico, created = FollowUpTemplate.objects.get_or_create(
            name="Template Básico - 4 Semanas",
            defaults={
                'description': 'Acompanhamento fundamental para novos convertidos com foco em fundamentos da fé e integração na igreja.'
            }
        )
        
        if created:
            self.stdout.write(f'Template "{template_basico.name}" criado.')
            
            # Etapas do template básico
            etapas_basico = [
                (1, "Primeira visita - Conhecer o novo convertido e sua família. Apresentar-se, orar juntos e explicar o processo de consolidação."),
                (2, "Estudo bíblico - Fundamentos da salvação. Abordar temas como novo nascimento, perdão e vida eterna."),
                (3, "Integração - Apresentar os ministérios da igreja. Mostrar oportunidades de servir e se conectar com outros membros."),
                (4, "Avaliação - Verificar crescimento espiritual. Conversar sobre próximos passos no discipulado.")
            ]
            
            for week, task in etapas_basico:
                FollowUpTemplateStep.objects.create(
                    template=template_basico,
                    week=week,
                    title=f'Semana {week}',
                    description=task,
                )
            
            self.stdout.write(f'{len(etapas_basico)} etapas criadas para o template básico.')
        
        # Template Intensivo - 8 Semanas
        template_intensivo, created = FollowUpTemplate.objects.get_or_create(
            name="Template Intensivo - 8 Semanas",
            defaults={
                'description': 'Acompanhamento aprofundado com estudo bíblico, discipulado e preparação para ministérios.'
            }
        )
        
        if created:
            self.stdout.write(f'Template "{template_intensivo.name}" criado.')
            
            # Etapas do template intensivo
            etapas_intensivo = [
                (1, "Primeira visita - Apresentação pessoal, oração e explicação do processo completo de consolidação."),
                (2, "Fundamentos da fé - Estudo sobre salvação, novo nascimento e certeza da vida eterna."),
                (3, "Batismo - Importância do batismo nas águas e preparação para este passo de obediência."),
                (4, "Vida de oração - Como desenvolver uma vida de oração consistente e eficaz."),
                (5, "Estudo da Bíblia - Métodos de estudo bíblico e importância da Palavra de Deus."),
                (6, "Vida em comunidade - Integração na igreja, relacionamentos cristãos e prestação de contas."),
                (7, "Dons espirituais - Descoberta e desenvolvimento dos dons espirituais pessoais."),
                (8, "Discipulado - Preparação para discipular outros e multiplicação na fé.")
            ]
            
            for week, task in etapas_intensivo:
                FollowUpTemplateStep.objects.create(
                    template=template_intensivo,
                    week=week,
                    title=f'Semana {week}',
                    description=task,
                )
            
            self.stdout.write(f'{len(etapas_intensivo)} etapas criadas para o template intensivo.')
        
        # Template Jovens - 6 Semanas
        template_jovens, created = FollowUpTemplate.objects.get_or_create(
            name="Template Jovens - 6 Semanas",
            defaults={
                'description': 'Consolidação específica para jovens com linguagem e atividades adaptadas para esta faixa etária.'
            }
        )
        
        if created:
            self.stdout.write(f'Template "{template_jovens.name}" criado.')
            
            # Etapas do template jovens
            etapas_jovens = [
                (1, "Conectar-se - Conhecer o jovem, suas paixões, sonhos e história de vida. Criar um ambiente de confiança."),
                (2, "Identidade - Quem sou eu em Cristo? Trabalhar questões de identidade cristã e autoestima."),
                (3, "Propósito - Para que Deus me chamou? Descobrir o propósito divino para a vida do jovem."),
                (4, "Relacionamentos - Como viver relacionamentos saudáveis segundo os princípios bíblicos."),
                (5, "Desafios - Como enfrentar as pressões do mundo mantendo-se firme na fé."),
                (6, "Multiplicação - Como influenciar outros jovens e ser um discipulador em potencial.")
            ]
            
            for week, task in etapas_jovens:
                FollowUpTemplateStep.objects.create(
                    template=template_jovens,
                    week=week,
                    title=f'Semana {week}',
                    description=task,
                )
            
            self.stdout.write(f'{len(etapas_jovens)} etapas criadas para o template jovens.')
        
        total_templates = FollowUpTemplate.objects.count()
        total_steps = FollowUpTemplateStep.objects.count()
        
        self.stdout.write(
            self.style.SUCCESS(
                f'Concluído! {total_templates} templates e {total_steps} etapas disponíveis no sistema.'
            )
        )