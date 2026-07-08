"""Notification delivery backends."""

from avcardtool.notifications.backends.email_smtp import EmailBackend

# Registry of backend name -> class, mirroring the UPLOADERS pattern.
# Adding a backend is one module plus one entry here.
BACKENDS = {
    "email": EmailBackend,
}

__all__ = ["BACKENDS", "EmailBackend"]
