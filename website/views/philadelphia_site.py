from django.conf import settings
from django.views.generic import TemplateView


class PhiladelphiaSiteView(TemplateView):
    """Landing page pública do Sítio Filadélfia."""

    template_name = 'front/philadelphia_site.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Dados que dependem do projeto ficam centralizados aqui. Não preencha
        # com dados de pagamento, fotos ou renders que ainda não foram aprovados.
        context['campaign'] = {
            'deadline': 'Até o segundo domingo de outubro.',
            'pix_receiver': 'Igreja Batista Nova Filadélfia em Itaipuaçu',
            # Chave pública da doação; uma variável de ambiente ainda pode
            # substituí-la quando a página for configurada em outro ambiente.
            'pix_key': getattr(settings, 'PHILADELPHIA_PIX_KEY', '') or '24186513000105',
            'pix_qr_code': getattr(settings, 'PHILADELPHIA_PIX_QR_CODE', ''),
            'credit_card_url': getattr(settings, 'PHILADELPHIA_CREDIT_CARD_URL', ''),
            'video_url': getattr(settings, 'PHILADELPHIA_CAMPAIGN_VIDEO_URL', ''),
            'instagram_reel_url': (
                getattr(settings, 'PHILADELPHIA_INSTAGRAM_REEL_URL', '')
                or 'https://www.instagram.com/reel/DeIOCTbRi43/'
            ),
            'contribution_values': (
                '500 – 1.000',
                '1.000 – 2.500',
                '2.500 – 5.000',
                '5.000 – 10.000',
                'Acima de 10.000',
            ),
            # Use the same Unicode normalization as the asset filename. The
            # decomposed accent form caused a 404 after static files were
            # collected in production.
            'hero_image': 'philadelphia-site/Entrada do Parque Filadélfia em Dia Ensolarado.png',
            'structure_photos': (
                {'src': 'philadelphia-site/Imagem do ChatGPT 6 de out. de 2026, 10_20_42-1.png', 'alt': 'Piscina e paisagismo do Sítio Filadélfia', 'title': 'Área da piscina', 'description': 'Lazer, convivência e paisagismo.'},
                {'src': 'philadelphia-site/Imagem do ChatGPT 6 de out. de 2026, 10_20_43-2.png', 'alt': 'Fachada da casa principal do Sítio Filadélfia', 'title': 'Casa principal', 'description': 'Estrutura para hospedagem, reuniões e apoio.'},
                {'src': 'philadelphia-site/Imagem do ChatGPT 6 de out. de 2026, 10_20_49-9.png', 'alt': 'Salão de jogos do Sítio Filadélfia', 'title': 'Salão de jogos', 'description': 'Um espaço de convivência e entretenimento.'},
                {'src': 'philadelphia-site/Imagem do ChatGPT 6 de out. de 2026, 10_20_45-4.png', 'alt': 'Parquinho e área de lazer do Sítio Filadélfia', 'title': 'Parquinho e área esportiva', 'description': 'Ambientes preparados para crianças, jovens e famílias.'},
                {'src': 'philadelphia-site/Imagem do ChatGPT 6 de out. de 2026, 10_20_47-6.png', 'alt': 'Fachada de uma casa de apoio do Sítio Filadélfia', 'title': 'Casas de apoio', 'description': 'Mais estrutura para hospedagem e atividades.'},
                {'src': 'philadelphia-site/Imagem do ChatGPT 6 de out. de 2026, 10_20_46-5.png', 'alt': 'Quadra e área de convivência do Sítio Filadélfia', 'title': 'Quadra e convivência', 'description': 'Espaços complementares para diferentes necessidades.'},
            ),
            'site_photos': (
                {'src': 'philadelphia-site/Imagem do ChatGPT 6 de out. de 2026, 10_20_42-1.png', 'alt': 'Vista panorâmica da piscina e jardins do Sítio Filadélfia'},
                {'src': 'philadelphia-site/Imagem do ChatGPT 6 de out. de 2026, 10_20_43-2.png', 'alt': 'Fachada da casa principal do Sítio Filadélfia'},
                {'src': 'philadelphia-site/Imagem do ChatGPT 6 de out. de 2026, 10_20_44-3.png', 'alt': 'Jardins e paisagismo do Sítio Filadélfia'},
                {'src': 'philadelphia-site/Imagem do ChatGPT 6 de out. de 2026, 10_20_45-4.png', 'alt': 'Parquinho do Sítio Filadélfia'},
                {'src': 'philadelphia-site/Imagem do ChatGPT 6 de out. de 2026, 10_20_46-5.png', 'alt': 'Quadra esportiva e área de convivência'},
                {'src': 'philadelphia-site/Imagem do ChatGPT 6 de out. de 2026, 10_20_47-6.png', 'alt': 'Fachada de casa de apoio'},
                {'src': 'philadelphia-site/Imagem do ChatGPT 6 de out. de 2026, 10_20_47-7.png', 'alt': 'Sala e área de refeições da casa principal'},
                {'src': 'philadelphia-site/Imagem do ChatGPT 6 de out. de 2026, 10_20_48-8.png', 'alt': 'Varanda com vista para a área verde'},
                {'src': 'philadelphia-site/Imagem do ChatGPT 6 de out. de 2026, 10_20_49-9.png', 'alt': 'Salão de jogos'},
                {'src': 'philadelphia-site/Imagem do ChatGPT 6 de out. de 2026, 10_20_50-10.png', 'alt': 'Bar e área de jogos'},
            ),
            'future_images': (
                {'src': 'philadelphia-site/IMG_2564.PNG', 'alt': 'Perspectiva do estacionamento e entrada do Sítio Filadélfia'},
                {'src': 'philadelphia-site/IMG_2565.PNG', 'alt': 'Perspectiva do salão principal para mil pessoas'},
                {'src': 'philadelphia-site/IMG_2566.jpeg', 'alt': 'Perspectiva da área esportiva'},
                {'src': 'philadelphia-site/IMG_2567.PNG', 'alt': 'Perspectiva da área infantil'},
                {'src': 'philadelphia-site/IMG_2568.PNG', 'alt': 'Perspectiva do salão para jovens e adolescentes'},
            ),
        }
        return context
