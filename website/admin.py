from django.contrib import admin
from .models import ministry, evangelism, follow_up, member, visitor, neighborhood, user, event, testimony

admin.site.register(ministry.Ministry)
admin.site.register(evangelism.Evangelized)
admin.site.register(follow_up.FollowUp)
admin.site.register(follow_up.FollowUpReport)
admin.site.register(member.Member)
admin.site.register(visitor.Visitor)
admin.site.register(neighborhood.Neighborhood)
admin.site.register(user.User)

@admin.register(event.Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'event_date', 'location', 'is_recurring', 'recurrence_pattern', 'is_visible')
    list_filter = ('is_recurring', 'recurrence_pattern', 'event_date')
    search_fields = ('title', 'description', 'location')
    prepopulated_fields = {'slug': ('title',)}
    fieldsets = (
        ('Informações Básicas', {
            'fields': ('title', 'description', 'banner', 'slug')
        }),
        ('Data e Horário', {
            'fields': ('event_date', 'event_time', 'display_start', 'display_end')
        }),
        ('Localização e Links', {
            'fields': ('location', 'link_more_info', 'link_type')
        }),
        ('Recorrência', {
            'fields': ('is_recurring', 'recurrence_pattern', 'recurrence_description'),
            'classes': ('collapse',),
            'description': 'Configure aqui se o evento se repete regularmente'
        }),
    )


@admin.register(testimony.Testimony)
class TestimonyAdmin(admin.ModelAdmin):
    list_display = ('author_name', 'category', 'is_approved', 'show_on_home', 'created_at')
    list_filter = ('is_approved', 'show_on_home', 'category')
    search_fields = ('author_name', 'content')
    list_editable = ('is_approved', 'show_on_home')
    fieldsets = (
        ('Informações do Testemunho', {
            'fields': ('author_name', 'member', 'photo', 'content', 'category', 'testimony_date')
        }),
        ('Instagram', {
            'fields': ('instagram_url',),
            'description': 'Cole a URL de um post ou reel do Instagram para exibir o vídeo na página de testemunhos'
        }),
        ('Exibição', {
            'fields': ('is_approved', 'show_on_home')
        }),
    )
