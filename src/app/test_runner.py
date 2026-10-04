"""Clear the throttle cache before each test.

DRF stores counters in the process cache. A rolled-back test database reuses
primary keys, so without a reset the whole suite would share one user's budget.
"""

from __future__ import annotations

from django.test.runner import DiscoverRunner


class CacheResetRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        from django.core.cache import cache
        from django.test import SimpleTestCase

        if getattr(SimpleTestCase, "_chili_clears_cache", False):
            return
        original = SimpleTestCase._pre_setup.__func__

        def _pre_setup(cls):
            cache.clear()
            return original(cls)

        SimpleTestCase._pre_setup = classmethod(_pre_setup)
        SimpleTestCase._chili_clears_cache = True
