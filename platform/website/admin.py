from django.contrib import admin
from .models import ministry, evangelism, follow_up, member, visitor

admin.site.register(ministry.Ministry)
admin.site.register(evangelism.Evangelism)
admin.site.register(follow_up.FollowUp)
admin.site.register(member.Member)
admin.site.register(visitor.Visitor)
