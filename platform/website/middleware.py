"""
Middleware para limpar mensagens automaticamente entre views diferentes
"""

from django.contrib import messages
from django.urls import resolve


class MessageCleanupMiddleware:
    """
    Middleware que limpa mensagens automaticamente quando o usuário navega
    entre diferentes views do admin panel para evitar vazamento de mensagens
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        
    def __call__(self, request):
        # Processar antes da view
        self.process_request(request)
        
        # Chamar a view
        response = self.get_response(request)
        
        return response
    
    def process_request(self, request):
        """
        Processa a requisição antes de chamar a view
        """
        # Só processar para views GET do admin panel
        if request.method != 'GET':
            return
            
        # Só processar para URLs do admin panel
        if not request.path.startswith('/admin-panel/'):
            return
            
        try:
            # Resolver a URL para obter o nome da view
            resolved = resolve(request.path)
            view_name = resolved.url_name
            
            # Lista de views que devem ter mensagens limpas
            admin_edit_views = [
                'admin_member_create',
                'admin_member_edit', 
                'admin_visitor_create',
                'admin_visitor_edit',
                'admin_event_create',
                'admin_event_edit',
                'admin_ministry_create',
                'admin_ministry_edit',
                'admin_neighborhood_create',
                'admin_neighborhood_edit'
            ]
            
            # Se é uma view de edição/criação, limpar mensagens
            if view_name in admin_edit_views:
                # Forçar limpeza total das mensagens
                storage = messages.get_messages(request)
                for message in storage:
                    pass  # Iterate para consumir todas as mensagens
                storage.used = True
                    
        except Exception as e:
            # Se houver erro, não quebrar a aplicação
            # Log minimal apenas em caso de erro crítico
            pass
