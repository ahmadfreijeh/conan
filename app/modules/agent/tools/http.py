"""Shared HTTP helper for the agent's tools.

Every tool that calls a web API makes one ApiClient for it. The client handles what all
tools need: base URL, timeout, User-Agent, default query params, a wait between calls
(free APIs ask for it), a small in-memory cache of answers, and turning any failure into
one ToolError with a short message.

Cache: the same path + params returns the saved answer, with no request and no wait. Only
successful answers are saved; the oldest is dropped when it is full. It lives in memory, so
it is emptied when the process ends.

Tools catch ToolError and return {"error": ...}, so the agent can try another query.
"""

import json
import threading
import time
from collections import OrderedDict

import httpx

from app.core.config import get_settings

USER_AGENT = "conan-geolocation/0.1"


def user_agent() -> str:
    """App name plus the TOOLS_CONTACT setting (Wikipedia and OSM require a contact)."""
    contact = get_settings().tools_contact
    return f"{USER_AGENT} ({contact})" if contact else USER_AGENT


class ToolError(Exception):
    """A lookup failed (network, timeout, bad status, bad JSON). The message is short."""


class ApiClient:
    def __init__(
        self,
        base_url: str,
        timeout: float = 10.0,
        min_interval: float = 0.0,
        params: dict | None = None,
        headers: dict | None = None,
        cache_size: int = 256,
    ):
        """
        Args:
            base_url: e.g. "https://nominatim.openstreetmap.org".
            timeout: seconds per request.
            min_interval: seconds to wait between two requests (0 = no wait).
            params: query params sent with every request (e.g. format=json).
            headers: extra headers (e.g. an API key); User-Agent is always sent.
            cache_size: answers to keep in memory (0 = no cache).
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.min_interval = min_interval
        self.params = params or {}
        self.headers = {"User-Agent": user_agent(), **(headers or {})}
        self._lock = threading.Lock()  # one request at a time, so the wait holds
        self._last_call = 0.0
        self.cache_size = cache_size
        self._cache: OrderedDict[str, dict | list] = OrderedDict()

    def get_json(self, path: str, params: dict | None = None) -> dict | list:
        """GET base_url/path and return the JSON body. Raises ToolError on any failure."""
        path = path.lstrip("/")
        params = {**self.params, **(params or {})}
        key = path + "?" + json.dumps(params, sort_keys=True, default=str)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)  # recently used, drop it last
                return self._cache[key]
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            try:
                resp = httpx.get(
                    f"{self.base_url}/{path}",
                    params=params,
                    headers=self.headers,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()
            except httpx.TimeoutException:
                raise ToolError(f"timed out after {self.timeout}s") from None
            except httpx.HTTPStatusError as exc:
                raise ToolError(f"service answered {exc.response.status_code}") from None
            except (httpx.HTTPError, ValueError) as exc:
                raise ToolError(f"lookup failed: {exc}") from None
            finally:
                self._last_call = time.monotonic()
            if self.cache_size > 0:
                self._cache[key] = data
                if len(self._cache) > self.cache_size:
                    self._cache.popitem(last=False)  # drop the oldest
            return data
