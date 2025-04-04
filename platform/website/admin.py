from django.contrib import admin
from .models import ministry, evangelism, follow_up, member, visitor, neighborhood, user

admin.site.register(ministry.Ministry)
admin.site.register(evangelism.Evangelized)
admin.site.register(follow_up.FollowUp)
admin.site.register(follow_up.FollowUpReport)
admin.site.register(member.Member)
admin.site.register(visitor.Visitor)
admin.site.register(neighborhood.Neighborhood)
admin.site.register(user.User)
