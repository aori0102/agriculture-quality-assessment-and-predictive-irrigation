"""Standalone read-only Blynk MCP OAuth probe. Never calls mutating tools."""

import argparse
import asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer
import logging
from pathlib import Path
import sys
import time
from urllib.parse import parse_qs, urlsplit
import webbrowser

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud_edge.oauth_store import FileTokenStorage

ENDPOINT = "https://blynk.cloud/mcp"
TEMPLATE_ID = "TMPL6FGqaSpR3"
REDIRECT = "http://127.0.0.1:3030/callback"


def callback_server():
    result = asyncio.get_running_loop().create_future()
    loop = asyncio.get_running_loop()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path != "/callback":
                self.send_error(404)
                return
            params = parse_qs(parsed.query)
            values = {key: params.get(key, [None])[0] for key in ("code", "state", "iss")}
            valid = bool(values["code"] and values["state"])
            self.send_response(200 if valid else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Authorization received. Return to the terminal." if valid else b"Authorization failed.")
            if not result.done():
                loop.call_soon_threadsafe(result.set_result, values if valid else None)

        def log_message(self, *_args):
            pass  # A callback URL contains the authorization code.

    server = HTTPServer(("127.0.0.1", 3030), Handler)
    server.timeout = 1
    return server, result


async def probe(args):
    # SDK errors may include authorization URLs or token responses in tracebacks.
    logging.disable(logging.CRITICAL)
    try:
        import httpx2
        from pydantic import AnyUrl
        from mcp import Client
        from mcp.client.auth import AuthorizationCodeResult, OAuthClientProvider
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
    except ImportError:
        print("Install requirements-mcp-poc.txt in a virtual environment.", file=sys.stderr)
        return 2

    storage = FileTokenStorage(args.store, OAuthToken, OAuthClientInformationFull)
    server = None

    async def receive_callback():
        if server is None:
            raise RuntimeError("OAuth callback server was not started.")
        try:
            for _ in range(180):
                await asyncio.to_thread(server.handle_request)
                # The handler passes only the values needed by the SDK.
                if callback_result.done():
                    values = callback_result.result()
                    if values is None:
                        raise RuntimeError("Authorization callback failed.")
                    return AuthorizationCodeResult(**values)
            raise TimeoutError("Browser authorization timed out.")
        finally:
            server.server_close()

    callback_result = None

    async def browser_handler(url):
        nonlocal server, callback_result
        if args.unattended:
            raise RuntimeError("Browser authorization required; unattended check failed.")
        server, callback_result = callback_server()
        print("Opening browser for Blynk authorization. Complete the consent there.")
        webbrowser.open(url)

    oauth = OAuthClientProvider(
        server_url=ENDPOINT,
        client_metadata=OAuthClientMetadata(
            client_name="AIoT Edge MCP OAuth proof",
            redirect_uris=[AnyUrl(REDIRECT)],
        ),
        storage=storage,
        redirect_handler=browser_handler,
        callback_handler=receive_callback,
    )
    # SDK 2.3.0 reloads tokens on restart but does not restore their expiry time.
    # Restore it from the protected store so expired access tokens refresh first.
    remaining = storage.seconds_until_expiry()
    if remaining is not None:
        oauth.context.token_expiry_time = time.time() - 1 if args.force_refresh else time.time() + remaining
    try:
        async with httpx2.AsyncClient(auth=oauth, timeout=httpx2.Timeout(30.0, read=300.0)) as http_client:
            transport = streamable_http_client(ENDPOINT, http_client=http_client)
            async with Client(transport) as client:
                tools = await client.list_tools()
                names = {tool.name for tool in tools.tools}
                if "get_template" not in names:
                    raise RuntimeError("Blynk MCP did not expose get_template.")
                response = await client.call_tool("get_template", {"uri": f"template://{TEMPLATE_ID}"})
                if response.is_error:
                    raise RuntimeError("BTL template lookup failed.")
                data = response.structured_content
                if not isinstance(data, dict) or data.get("templateId") != TEMPLATE_ID or data.get("name") != "BTL":
                    raise RuntimeError("BTL template identity could not be verified.")
                print(f"Read-only BTL lookup succeeded; template ID {TEMPLATE_ID}.")
                print("No regional device host or Device Auth Token is provided by get_template.")
        print("OAuth client registration and token are persisted in the private store.")
        return 0
    except Exception as error:
        def types_only(exc):
            if isinstance(exc, BaseExceptionGroup):
                return [name for child in exc.exceptions for name in types_only(child)]
            frame = exc.__traceback__
            locations = []
            while frame is not None:
                locations.append(f"{Path(frame.tb_frame.f_code.co_filename).name}:{frame.tb_lineno}")
                frame = frame.tb_next
            return [type(exc).__name__ + " at " + ",".join(locations[-4:])]

        print(f"MCP probe failed ({', '.join(types_only(error))}). No credentials printed.", file=sys.stderr)
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=Path(__file__).resolve().parents[1] / "runtime" / "mcp_oauth.json")
    parser.add_argument("--unattended", action="store_true", help="Fail if browser interaction is required")
    parser.add_argument("--force-refresh", action="store_true", help="Simulate an expired access token to test refresh; do not change stored credentials")
    return asyncio.run(probe(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
