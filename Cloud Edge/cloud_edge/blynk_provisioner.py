"""Blynk MCP adapter used only by the background provisioning worker."""

import asyncio
import logging
from pathlib import Path
import time

from .oauth_store import FileTokenStorage

ENDPOINT = "https://blynk.cloud/mcp"
TEMPLATE_ID = "TMPL6FGqaSpR3"


class ProvisioningError(Exception):
    """Sanitized failure; never include raw MCP responses or URLs."""


class AuthorizationRequired(ProvisioningError):
    """Operator must renew the independent OAuth grant."""


class BlynkProvisioner:
    def __init__(self, oauth_store: Path):
        self.oauth_store = oauth_store

    async def _call(self, tool: str, arguments: dict):
        import httpx2
        from pydantic import AnyUrl
        from mcp import Client
        from mcp.client.auth import OAuthClientProvider
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

        storage = FileTokenStorage(self.oauth_store, OAuthToken, OAuthClientInformationFull)

        async def no_browser(_url):
            raise AuthorizationRequired("Blynk OAuth needs operator reauthorization.")

        async def no_callback():
            raise AuthorizationRequired("Blynk OAuth needs operator reauthorization.")

        oauth = OAuthClientProvider(
            server_url=ENDPOINT,
            client_metadata=OAuthClientMetadata(
                client_name="AIoT Edge MCP OAuth proof",
                redirect_uris=[AnyUrl("http://127.0.0.1:3030/callback")],
            ),
            storage=storage,
            redirect_handler=no_browser,
            callback_handler=no_callback,
        )
        remaining = storage.seconds_until_expiry()
        if remaining is None:
            raise AuthorizationRequired("Blynk OAuth authorization is missing.")
        oauth.context.token_expiry_time = time.time() + remaining if remaining else time.time() - 1
        # The SDK may log full OAuth URLs on exceptions. Keep its diagnostics private.
        logging.getLogger("mcp.client.auth.oauth2").disabled = True
        async with httpx2.AsyncClient(auth=oauth, timeout=httpx2.Timeout(15.0, read=30.0)) as http_client:
            transport = streamable_http_client(ENDPOINT, http_client=http_client)
            async with Client(transport) as client:
                await client.list_tools()
                result = await client.call_tool(tool, arguments)
                if result.is_error or not isinstance(result.structured_content, dict):
                    raise ProvisioningError("Blynk MCP tool rejected the request.")
                return result.structured_content

    def call(self, tool: str, arguments: dict):
        try:
            return asyncio.run(self._call(tool, arguments))
        except ProvisioningError:
            raise
        except Exception as error:
            def needs_authorization(exc):
                if isinstance(exc, AuthorizationRequired):
                    return True
                return isinstance(exc, BaseExceptionGroup) and any(needs_authorization(child) for child in exc.exceptions)
            if needs_authorization(error):
                raise AuthorizationRequired("Blynk OAuth needs operator reauthorization.") from None
            raise ProvisioningError("Blynk MCP request failed or its outcome is uncertain.") from None

    def find_device(self, stable_name: str):
        """Return matching metadata, None for confirmed absence, or raise on uncertainty."""
        page = 0
        matches = []
        seen = 0
        while True:
            data = self.call("search_devices", {"page": page})
            items, total = data.get("content"), data.get("totalElements")
            if not isinstance(items, list) or type(total) is not int:
                raise ProvisioningError("Blynk search response is incomplete.")
            matches += [item for item in items if isinstance(item, dict)
                        and item.get("name") == stable_name and item.get("templateId") == TEMPLATE_ID]
            seen += len(items)
            page += 1
            if len(items) == 0 and total > 0:
                raise ProvisioningError("Blynk search pagination is incomplete.")
            if seen >= total:
                break
            if page > 100:
                raise ProvisioningError("Blynk search exceeded its page bound.")
        if len(matches) > 1:
            raise ProvisioningError("Multiple Blynk devices match one node.")
        return matches[0] if matches else None

    def create_device(self, stable_name: str):
        data = self.call("create_device", {"deviceName": stable_name, "templateId": TEMPLATE_ID})
        if (data.get("name") != stable_name or data.get("templateId") != TEMPLATE_ID
                or type(data.get("id")) is not int or not isinstance(data.get("token"), str)
                or not data["token"]):
            raise ProvisioningError("Blynk create result lacks a usable binding; reconcile manually.")
        return data
