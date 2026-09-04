"""Test-suite-wide network guard (Gate-1 preparation, 2026-09-04).

Every real network call this codebase can make -- every `LLMAdapter` (`adapters/openai.py`,
`adapters/nvidia.py`, `adapters/gemini.py`) and `job_intake/services/fetch.py`'s URL fetch --
goes through exactly two call sites: `requests.get`/`requests.post`. There is no SDK client
constructed anywhere (this project depends only on the `requests` library, never `openai`/
`google-generativeai`/etc.), so patching those two functions for the lifetime of `manage.py test`
is a structural guarantee, not a per-test convention: no test can reach a live provider (or any
other external host) even if it forgets to substitute `FakeAdapter`, even if a real credential
happens to be present in the environment.

A test that needs to exercise the real HTTP call path (e.g. `job_intake/tests/test_fetch.py`)
still can -- `unittest.mock.patch("requests.get"/"requests.post")` at the test level always nests
correctly on top of this runner-level guard: it swaps in its own `MagicMock` for the duration of
that test, then restores back to *this* guard afterward, never to the true unpatched function.
"""

from __future__ import annotations

import requests
from django.test.runner import DiscoverRunner


class BlockedNetworkCallError(RuntimeError):
    """Raised instead of ever making a real outbound HTTP call during `manage.py test`."""


def _blocked_get(*args, **kwargs):
    raise BlockedNetworkCallError(
        "A test attempted a real requests.get(...) call. The default test suite must never reach "
        "a live provider or external host -- use FakeAdapter (llm_provider.adapters.fake."
        "FakeAdapter) for an LLM stage, or mock.patch('requests.get') explicitly for a test that "
        "means to exercise the real HTTP call path (see job_intake/tests/test_fetch.py)."
    )


def _blocked_post(*args, **kwargs):
    raise BlockedNetworkCallError(
        "A test attempted a real requests.post(...) call. The default test suite must never reach "
        "a live LLM provider, even when a real credential is configured in the environment -- use "
        "FakeAdapter (llm_provider.adapters.fake.FakeAdapter) or a StageModelAssignment(FAKE) "
        "instead. Opt-in smoke tests (python manage.py smoke_test_<provider>) are the only "
        "sanctioned way to make a real call, and are never invoked by manage.py test."
    )


class NetworkGuardedTestRunner(DiscoverRunner):
    """The project's `TEST_RUNNER` (see `config/settings.py`) -- identical to Django's default
    `DiscoverRunner`, except `requests.get`/`requests.post` are replaced with functions that raise
    `BlockedNetworkCallError` for the entire test run, restored only on teardown."""

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self._real_requests_get = requests.get
        self._real_requests_post = requests.post
        requests.get = _blocked_get
        requests.post = _blocked_post

    def teardown_test_environment(self, **kwargs):
        requests.get = self._real_requests_get
        requests.post = self._real_requests_post
        super().teardown_test_environment(**kwargs)
