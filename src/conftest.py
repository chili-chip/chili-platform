"""pytest glue so VS Code's test explorer matches `manage.py test`.

pytest-django ignores settings.TEST_RUNNER, so repeat the throttle-cache reset
that app.test_runner.CacheResetRunner performs.
"""

import pytest


@pytest.fixture(autouse=True)
def _clear_cache():
    from django.core.cache import cache

    cache.clear()
    yield
