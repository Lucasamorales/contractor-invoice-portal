"""An httpx-backed transport for ``google.auth``.

``google.auth.transport.requests`` pulls in the ``requests`` library. The stack
already carries ``httpx``, and this adapter is small enough that adding a
second HTTP client to fetch one JSON document of public keys is not worth it.

Implements the ``google.auth.transport.Request`` interface: callable, returning
an object with ``status``, ``headers`` and ``data``.
"""

from __future__ import annotations

from typing import Any

import httpx
from google.auth.transport import Request as GoogleAuthRequest
from google.auth.transport import Response as GoogleAuthResponse

DEFAULT_TIMEOUT = 10.0


class _Response(GoogleAuthResponse):
    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    @property
    def status(self) -> int:
        return self._response.status_code

    @property
    def headers(self) -> dict[str, str]:
        return dict(self._response.headers)

    @property
    def data(self) -> bytes:
        return self._response.content


class HttpxRequest(GoogleAuthRequest):
    """The transport google-auth calls to fetch IAP's public keys."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._client = httpx.Client(timeout=timeout, follow_redirects=False)

    def __call__(
        self,
        url: str,
        method: str = "GET",
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> _Response:
        response = self._client.request(
            method,
            url,
            content=body,
            headers=headers,
            timeout=timeout if timeout is not None else DEFAULT_TIMEOUT,
        )
        return _Response(response)

    def close(self) -> None:
        self._client.close()
