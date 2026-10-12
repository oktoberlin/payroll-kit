"""Settings used by pytest: local-dev behaviour, no HTTPS."""

import os

os.environ.setdefault("DJANGO_DEBUG", "1")

from config.settings import *  # noqa: E402,F403
