from .models import AuditLog


def record_audit(*, actor, action, target="", ip=None):
    """Append-only security/audit entry (Section 28). Never pass raw pairing
    codes or credential material as `action`/`target`."""
    return AuditLog.objects.create(
        actor=actor,
        action=action,
        target=target,
        ip=ip or "",
    )
