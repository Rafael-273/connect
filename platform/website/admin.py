from django.contrib import admin
from .models import ministry, evangelism, follow_up, member, visitor, neighborhood, user, event, escala

admin.site.register(ministry.Ministry)
admin.site.register(evangelism.Evangelized)
admin.site.register(follow_up.FollowUp)
admin.site.register(follow_up.FollowUpReport)
admin.site.register(member.Member)
admin.site.register(visitor.Visitor)
admin.site.register(neighborhood.Neighborhood)
admin.site.register(user.User)
admin.site.register(event.Event)


@admin.register(escala.Escala)
class EscalaAdmin(admin.ModelAdmin):
    list_display = ('title', 'ministry', 'date', 'get_assigned_members_count')
    list_filter = ('ministry', 'date')
    search_fields = ('title', 'description', 'ministry__name')
    filter_horizontal = ('assigned_members',)
    ordering = ['-date']
    
    def get_assigned_members_count(self, obj):
        return obj.assigned_members.count()
    get_assigned_members_count.short_description = 'Membros Escalados'
