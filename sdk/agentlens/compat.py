"""
Version shims.

The SDK advertises Python 3.9+, so it has to actually work there. This
module holds the handful of places where a newer stdlib signature would
otherwise quietly break an older interpreter — the CI matrix exists to keep
this honest.
"""

from __future__ import annotations

import sys
import traceback
from typing import Optional


def format_exception(error: BaseException) -> str:
    """
    Render a traceback from an exception object.

    `traceback.format_exception(exc)` only accepts a single argument from
    3.10 onward; before that it needs the (type, value, traceback) triple.
    """
    if sys.version_info >= (3, 10):
        return "".join(traceback.format_exception(error)).strip()
    return "".join(traceback.format_exception(type(error), error, error.__traceback__)).strip()


def exception_message(error: BaseException, limit: Optional[int] = None) -> str:
    text = f"{type(error).__name__}: {error}"
    return text if limit is None else text[:limit]
