from .models import Parent


class ParentOwnershipMixin:
    def get_parent(self):
        parent, _ = Parent.objects.get_or_create(user=self.request.user)
        return parent

    def get_queryset(self):
        return self.queryset.filter(parent=self.get_parent())
