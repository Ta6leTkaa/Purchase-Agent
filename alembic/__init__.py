"""Expose local migration modules alongside the installed Alembic package."""

from pkgutil import extend_path
from typing import Any

__path__ = extend_path(__path__, __name__)

# Alembic exposes this dynamically; declaring it preserves the runtime import
# behavior while giving migration tests a stable static interface.
op: Any
