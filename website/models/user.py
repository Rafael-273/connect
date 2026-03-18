from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.db import models


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("O campo email é obrigatório")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        
        # Define a senha padrão se não for fornecida
        if password is None:
            password = "123"
            
        user.set_password(password)
        user.save()
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(email, password, **extra_fields)

class User(AbstractBaseUser, PermissionsMixin):
    USER_TYPE_CHOICES = (
        ('member', 'Membro (Sem acesso ao painel)'),
        ('admin', 'Administrador (Acesso total)'),
        ('visitors', 'Visitantes (Gerencia visitantes)'),
        ('consolidation', 'Consolidação (Gerencia acompanhamentos)'),
        ('events', 'Eventos (Gerencia eventos)'),
    )
    
    email = models.EmailField(unique=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    user_type = models.CharField(max_length=20, choices=USER_TYPE_CHOICES, default='member', verbose_name='Tipo de Usuário')

    first_name = models.CharField(max_length=50, blank=True, verbose_name='Nome')
    last_name = models.CharField(max_length=50, blank=True, verbose_name='Sobrenome')
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True, verbose_name='Foto de Perfil')
    phone = models.CharField(max_length=20, blank=True, verbose_name='Telefone')
    bio = models.TextField(blank=True, verbose_name='Sobre você')

    date_joined = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    def __str__(self):
        return self.email
    
    def has_admin_access(self):
        """Verifica se o usuário tem acesso ao painel de administração"""
        return self.is_staff or self.user_type in ['admin', 'visitors', 'consolidation', 'events']
    
    def has_module_permission(self, module):
        """Verifica se o usuário tem permissão para acessar um módulo específico"""
        if self.is_superuser or self.user_type == 'admin':
            return True
            
        module_permissions = {
            'visitors': ['visitors'],
            'consolidation': ['consolidation', 'followup'],
            'events': ['events'],
        }
        
        return module in module_permissions.get(self.user_type, [])
