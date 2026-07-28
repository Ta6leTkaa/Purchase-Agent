"""Expose local migration modules alongside the installed Alembic package."""

from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)
