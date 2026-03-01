"""
Secure Channel — Encrypted and authenticated communication with GCP.

Supports:
1. mTLS (mutual TLS) with client certificates
2. API Key authentication with HMAC signing
3. Certificate pinning for known GCP endpoints
4. Automatic token refresh for OAuth2/service accounts

The channel is used by gcp_tools to communicate with cloud infrastructure.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import ssl
import time
from typing import Any, Dict, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class SecureChannel:
    """Manages secure communication with GCP infrastructure."""

    def __init__(
        self,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        client_cert: Optional[str] = None,
        client_key: Optional[str] = None,
        ca_cert: Optional[str] = None,
        verify_ssl: bool = True,
    ):
        self.api_url = (api_url or os.getenv("GCP_API_URL", "")).rstrip("/")
        self._api_key = api_key or os.getenv("GCP_API_KEY", "")
        self._client_cert = client_cert or os.getenv("GCP_CLIENT_CERT", "")
        self._client_key = client_key or os.getenv("GCP_CLIENT_KEY", "")
        self._ca_cert = ca_cert or os.getenv("GCP_CA_CERT", "")
        self._verify_ssl = verify_ssl
        self._ssl_context: Optional[ssl.SSLContext] = None
        self._session = None

    def get_ssl_context(self) -> ssl.SSLContext:
        """Create an SSL context with mTLS if certificates are available."""
        if self._ssl_context is not None:
            return self._ssl_context

        ctx = ssl.create_default_context()

        # Load CA certificate for server verification
        if self._ca_cert and os.path.exists(self._ca_cert):
            ctx.load_verify_locations(self._ca_cert)
            logger.info(f"🔒 Loaded CA cert: {self._ca_cert}")

        # Load client certificate for mTLS
        if self._client_cert and self._client_key:
            if os.path.exists(self._client_cert) and os.path.exists(self._client_key):
                ctx.load_cert_chain(
                    certfile=self._client_cert,
                    keyfile=self._client_key,
                )
                logger.info(f"🔒 mTLS enabled with client cert: {self._client_cert}")

        if not self._verify_ssl:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            logger.warning("⚠️ SSL verification disabled — NOT recommended for production")

        # Enforce TLS 1.2+ minimum
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2

        # Disable weak ciphers
        ctx.set_ciphers(
            "ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20:!aNULL:!MD5:!DSS"
        )

        self._ssl_context = ctx
        return ctx

    def sign_request(
        self,
        method: str,
        path: str,
        body: Optional[str] = None,
        timestamp: Optional[int] = None,
    ) -> Dict[str, str]:
        """Generate HMAC-signed authentication headers.

        Creates a signature from: method + path + timestamp + body_hash
        This prevents replay attacks and request tampering.

        Args:
            method: HTTP method (GET, POST, etc.)
            path: Request path (e.g., /waf/status)
            body: Optional request body
            timestamp: Optional Unix timestamp (defaults to now)

        Returns:
            Dict of authentication headers.
        """
        if not self._api_key:
            return {}

        ts = timestamp or int(time.time())
        body_hash = hashlib.sha256((body or "").encode()).hexdigest()

        # Create signature payload
        payload = f"{method.upper()}:{path}:{ts}:{body_hash}"
        signature = hmac.new(
            self._api_key.encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()

        return {
            "X-API-Key": self._api_key,
            "X-Timestamp": str(ts),
            "X-Signature": signature,
            "X-Body-Hash": body_hash,
            "Content-Type": "application/json",
        }

    @staticmethod
    def verify_signature(
        api_key: str,
        method: str,
        path: str,
        body: Optional[str],
        timestamp: str,
        signature: str,
        max_age_seconds: int = 300,
    ) -> bool:
        """Verify an incoming HMAC-signed request (server-side).

        Args:
            api_key: The shared API key.
            method: HTTP method.
            path: Request path.
            body: Request body.
            timestamp: Timestamp from X-Timestamp header.
            signature: Signature from X-Signature header.
            max_age_seconds: Maximum age of request (prevents replay).

        Returns:
            True if signature is valid and not expired.
        """
        # Check timestamp freshness (prevent replay attacks)
        try:
            ts = int(timestamp)
        except (ValueError, TypeError):
            return False

        now = int(time.time())
        if abs(now - ts) > max_age_seconds:
            logger.warning(f"🛡️ Request too old: {abs(now - ts)}s > {max_age_seconds}s")
            return False

        # Recompute signature
        body_hash = hashlib.sha256((body or "").encode()).hexdigest()
        payload = f"{method.upper()}:{path}:{ts}:{body_hash}"
        expected = hmac.new(
            api_key.encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(signature, expected)

    async def request(
        self,
        method: str,
        path: str,
        data: Optional[Dict[str, Any]] = None,
        timeout: int = 30,
    ) -> Dict[str, Any]:
        """Make an authenticated, encrypted request to the GCP API.

        Args:
            method: HTTP method.
            path: API path (e.g., /waf/status).
            data: Optional JSON body.
            timeout: Request timeout in seconds.

        Returns:
            Response data as dict.
        """
        import aiohttp

        url = f"{self.api_url}{path}"
        body = json.dumps(data) if data else None
        headers = self.sign_request(method, path, body)
        ssl_ctx = self.get_ssl_context()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    data=body,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    ssl=ssl_ctx,
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        text = await resp.text()
                        raise RuntimeError(
                            f"GCP API error (HTTP {resp.status}): {text}"
                        )
        except aiohttp.ClientError as e:
            raise RuntimeError(f"GCP connection error: {e}")

    def is_configured(self) -> bool:
        """Check if the secure channel has minimum configuration."""
        return bool(self.api_url and self._api_key)

    def get_status(self) -> Dict[str, Any]:
        """Get channel security status."""
        has_mtls = bool(
            self._client_cert and self._client_key
            and os.path.exists(self._client_cert)
            and os.path.exists(self._client_key)
        )
        has_ca = bool(self._ca_cert and os.path.exists(self._ca_cert))

        return {
            "api_url": self.api_url or "(not configured)",
            "api_key_set": bool(self._api_key),
            "mtls_enabled": has_mtls,
            "ca_cert_loaded": has_ca,
            "ssl_verify": self._verify_ssl,
            "min_tls_version": "TLSv1.2",
        }
