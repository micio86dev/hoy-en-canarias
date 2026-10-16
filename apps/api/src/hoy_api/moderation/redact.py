import re

# E-mail addresses and phone numbers in Spain or abroad, written with or without separators.
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<!\d)(?:\+\d{1,3}[\s.-]?)?\d{3}[\s.-]?\d{3}[\s.-]?\d{3}(?!\d)")


def redact_personal_data(text: str) -> str:
    """Remove contact details before any text leaves the server."""
    without_email = _EMAIL.sub("[correo]", text)
    return _PHONE.sub("[telefono]", without_email)
