from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from django.db.models import F
from .models import User, Identity, ParentChild
@receiver(pre_save, sender=User)
def revoke_user(sender, instance, **kwargs):
    if instance.pk:
        old = User.objects.filter(pk=instance.pk).first()
        if old and any(getattr(old, k) != getattr(instance, k) for k in ["password", "is_active", "is_staff", "is_superuser", "is_parent", "is_student"]):
            instance.session_epoch = old.session_epoch + 1
@receiver(post_save, sender=Identity)
@receiver(post_delete, sender=Identity)
def revoke_identity(sender, instance, **kwargs):
    User.objects.filter(pk=instance.user_id).update(session_epoch=F("session_epoch") + 1)
@receiver(pre_save, sender=Identity)
def revoke_old_identity(sender, instance, **kwargs):
    if instance.pk:
        old = Identity.objects.filter(pk=instance.pk).first()
        if old: User.objects.filter(pk=old.user_id).update(session_epoch=F("session_epoch") + 1)
@receiver(post_save, sender=ParentChild)
@receiver(post_delete, sender=ParentChild)
def revoke_relation(sender, instance, **kwargs):
    User.objects.filter(pk=instance.parent_id).update(session_epoch=F("session_epoch") + 1)

from django.contrib.auth.signals import user_logged_in
from django.utils import timezone
from datetime import timedelta
@receiver(user_logged_in)
def stamp_session(sender, request, user, **kwargs):
    request.session['epoch'] = user.session_epoch
    request.session.set_expiry(timezone.now() + timedelta(days=7))
from django.db.models.signals import m2m_changed
@receiver(m2m_changed, sender=User.groups.through)
@receiver(m2m_changed, sender=User.user_permissions.through)
def revoke_permissions(sender, instance, action, reverse, pk_set, **kwargs):
    if action in ('post_add','post_remove','post_clear'):
        if isinstance(instance,User): User.objects.filter(pk=instance.pk).update(session_epoch=F('session_epoch')+1)
        elif pk_set: User.objects.filter(pk__in=pk_set).update(session_epoch=F('session_epoch')+1)
