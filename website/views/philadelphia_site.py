from django.conf import settings
from django.views.generic import TemplateView


class PhiladelphiaSiteView(TemplateView):
    """Landing page pública da campanha do Sítio Filadélfia."""

    template_name = 'front/philadelphia_site.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Dados que dependem da campanha ficam centralizados aqui. Não preencha
        # com dados de pagamento, fotos ou renders que ainda não foram aprovados.
        context['campaign'] = {
            'deadline': 'Até o segundo domingo de outubro.',
            'pix_receiver': 'Igreja Batista Nova Filadélfia em Itaipuaçu',
            'pix_key': getattr(settings, 'PHILADELPHIA_PIX_KEY', ''),
            'pix_qr_code': getattr(settings, 'PHILADELPHIA_PIX_QR_CODE', ''),
            'credit_card_url': getattr(settings, 'PHILADELPHIA_CREDIT_CARD_URL', ''),
            'video_url': getattr(settings, 'PHILADELPHIA_CAMPAIGN_VIDEO_URL', ''),
            'contribution_values': (
                '500 – 1.000',
                '1.000 – 2.500',
                '2.500 – 5.000',
                '5.000 – 10.000',
                'Acima de 10.000',
            ),
            'hero_image': 'philadelphia-site/01_area_piscina_01.png',
            'structure_photos': (
                {'src': 'philadelphia-site/01_area_piscina_02.png', 'alt': 'Piscina e paisagismo do Sítio Filadélfia', 'title': 'Área da piscina', 'description': 'Lazer, convivência e paisagismo.'},
                {'src': 'philadelphia-site/03_casa_principal_fachada.png', 'alt': 'Fachada da casa principal do Sítio Filadélfia', 'title': 'Casa principal', 'description': 'Estrutura para hospedagem, reuniões e apoio.'},
                {'src': 'philadelphia-site/04_salao_jogos_01.png', 'alt': 'Salão de jogos do Sítio Filadélfia', 'title': 'Salão de jogos e cinema', 'description': 'Um espaço de convivência e entretenimento.'},
                {'src': 'philadelphia-site/02_parquinho_01.png', 'alt': 'Parquinho e área de lazer do Sítio Filadélfia', 'title': 'Parquinho e área esportiva', 'description': 'Ambientes preparados para crianças, jovens e famílias.'},
                {'src': 'philadelphia-site/06_casa_234_fachada.png', 'alt': 'Casa de apoio do Sítio Filadélfia', 'title': 'Casas de apoio', 'description': 'Mais estrutura para hospedagem e atividades.'},
                {'src': 'philadelphia-site/05_academia_01.png', 'alt': 'Academia do Sítio Filadélfia', 'title': 'Academia e salão de festas', 'description': 'Espaços complementares para diferentes necessidades.'},
            ),
            'site_photos': (
                {'src': 'philadelphia-site/01_area_piscina_01.png', 'alt': 'Vista panorâmica da piscina e jardins do Sítio Filadélfia'},
                {'src': 'philadelphia-site/07_entrada_area_externa_01.png', 'alt': 'Vista da área externa e piscina do Sítio Filadélfia'},
                {'src': 'philadelphia-site/03_casa_principal_fachada.png', 'alt': 'Fachada da casa principal'},
                {'src': 'philadelphia-site/01_area_piscina_02.png', 'alt': 'Área da piscina do Sítio Filadélfia'},
                {'src': 'philadelphia-site/02_area_externa_apoio_01.png', 'alt': 'Área externa de apoio'},
                {'src': 'philadelphia-site/02_parquinho_01.png', 'alt': 'Parquinho do Sítio Filadélfia'},
                {'src': 'philadelphia-site/02_quadra_01.png', 'alt': 'Quadra esportiva do Sítio Filadélfia'},
                {'src': 'philadelphia-site/03_casa_principal_banheiro.png', 'alt': 'Banheiro da casa principal'},
                {'src': 'philadelphia-site/03_casa_principal_quarto.png', 'alt': 'Quarto da casa principal'},
                {'src': 'philadelphia-site/03_casa_principal_sala_01.png', 'alt': 'Sala da casa principal'},
                {'src': 'philadelphia-site/03_casa_principal_sala_02.png', 'alt': 'Outra sala da casa principal'},
                {'src': 'philadelphia-site/03_casa_principal_varanda.png', 'alt': 'Varanda da casa principal'},
                {'src': 'philadelphia-site/04_cinema_01.png', 'alt': 'Sala de cinema'},
                {'src': 'philadelphia-site/04_salao_jogos_01.png', 'alt': 'Salão de jogos'},
                {'src': 'philadelphia-site/04_salao_jogos_02.png', 'alt': 'Área do salão de jogos'},
                {'src': 'philadelphia-site/04_salao_jogos_03.png', 'alt': 'Detalhe do salão de jogos'},
                {'src': 'philadelphia-site/05_academia_01.png', 'alt': 'Academia'},
                {'src': 'philadelphia-site/05_salao_festas_fachada.png', 'alt': 'Fachada do salão de festas'},
                {'src': 'philadelphia-site/05_salao_festas_interior.png', 'alt': 'Interior do salão de festas'},
                {'src': 'philadelphia-site/06_casa_234_banheiro.png', 'alt': 'Banheiro de casa de apoio'},
                {'src': 'philadelphia-site/06_casa_234_fachada.png', 'alt': 'Fachada de casa de apoio'},
                {'src': 'philadelphia-site/06_casa_234_quarto.png', 'alt': 'Quarto de casa de apoio'},
                {'src': 'philadelphia-site/06_casa_234_sala.png', 'alt': 'Sala de casa de apoio'},
                {'src': 'philadelphia-site/07_area_externa_02.png', 'alt': 'Área externa do sítio'},
                {'src': 'philadelphia-site/07_area_externa_03.png', 'alt': 'Jardins e área externa'},
                {'src': 'philadelphia-site/07_entrada_portao.png', 'alt': 'Entrada do Sítio Filadélfia'},
            ),
            'future_images': (),
        }
        return context
