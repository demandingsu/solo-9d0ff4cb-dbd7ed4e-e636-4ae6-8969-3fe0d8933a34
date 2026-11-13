#!/usr/bin/env python3
"""Static sample-site server with a handful of explicit redirect fixtures.

The plain ``http.server`` cannot issue redirects, so the demo sample site is
served through this small wrapper. Only the fixed ``/jump/`` paths below
redirect; every other request is handled as a normal static file.

Redirect fixtures (browser-operable):

* ``/jump/same-site.html``          -> relative 302 to a real list page
* ``/jump/same-site-absolute.html`` -> absolute 302 to SAMPLE_SITE_PUBLIC_URL
* ``/jump/cross-host-cancel.html``  -> 302 to the *other* loopback hostname
* ``/jump/cross-port-pagination.html`` -> 302 to the same host on another port

Environment variables:

* ``SAMPLE_SITE_ALT_PORT`` (default ``8081``) - alternate port for the
  cross-port fixture.
* ``SAMPLE_SITE_ALT_HOST`` (default ``127.0.0.1``) - fallback host for the
  cross-host fixture when the request host is not a loopback spelling.
* ``SAMPLE_SITE_PUBLIC_URL`` - browser-facing base used by the absolute
  same-site fixture.
"""

from __future__ import annotations

import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

DEFAULT_ALT_PORT = 8081
DEFAULT_ALT_HOST = "127.0.0.1"


def _public_hostname() -> str | None:
    public_base = os.getenv("SAMPLE_SITE_PUBLIC_URL", "").strip()
    if not public_base:
        return None
    return (urlsplit(public_base).hostname or "").lower() or None


def _public_port(default: str) -> str:
    public_base = os.getenv("SAMPLE_SITE_PUBLIC_URL", "").strip()
    if public_base:
        try:
            port = urlsplit(public_base).port
        except ValueError:
            port = None
        if port:
            return str(port)
    return default


def _alternate_host(hostname: str) -> str:
    lowered = hostname.lower().strip("[]")
    if lowered == "localhost":
        return "127.0.0.1"
    if lowered in {"127.0.0.1", "::1"}:
        return "localhost"
    return os.getenv("SAMPLE_SITE_ALT_HOST", DEFAULT_ALT_HOST)


def _alternate_port(current_port: str) -> str:
    alternate = int(os.getenv("SAMPLE_SITE_ALT_PORT", str(DEFAULT_ALT_PORT)))
    if str(alternate) == str(current_port):
        return str(alternate + 1)
    return str(alternate)


def resolve_redirect(path: str, host_header: str) -> tuple[int, str] | None:
    """Return ``(status, location)`` for a known fixture path, else ``None``."""

    path = urlsplit(path).path
    host = host_header or "localhost:80"
    hostname, sep, port = host.partition(":")
    port = port if sep and port else "80"
    # Browser-facing spellings when compose maps public <-> internal names.
    public_host = _public_hostname()
    public_port = _public_port(port)

    if path == "/jump/same-site.html":
        return 302, "/notices/page-1.html"

    if path == "/jump/same-site-absolute.html":
        public_base = os.getenv("SAMPLE_SITE_PUBLIC_URL", "").rstrip("/")
        if public_base:
            return 302, f"{public_base}/notices/page-1.html"
        return 302, "/notices/page-1.html"

    if path == "/jump/cross-host-cancel.html":
        target_host = _alternate_host(public_host or hostname)
        return 302, (
            f"http://{target_host}:{public_port}"
            "/detail/d-006-cancel-external.html?from=cross-host-jump"
        )

    if path == "/jump/cross-port-pagination.html":
        target_host = public_host or hostname
        return 302, (
            f"http://{target_host}:{_alternate_port(public_port)}"
            "/notices/redirect-page-2.html?from=cross-port-jump"
        )

    return None


class SampleSiteHandler(SimpleHTTPRequestHandler):
    server_version = "TenderSampleSite/1.0"
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
    }

    def _serve_redirect(self) -> bool:
        host_header = self.headers.get("Host", "")
        target = resolve_redirect(self.path, host_header)
        if not target:
            return False
        status, location = target
        self.send_response(status)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        return True

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        if self._serve_redirect():
            return
        super().do_GET()

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib naming
        if self._serve_redirect():
            return
        super().do_HEAD()

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        # Keep the demo output quiet; the app has its own logging.
        pass


def build_server(
    host: str = "0.0.0.0",
    port: int = 8080,
    directory: str = "sample_site",
) -> ThreadingHTTPServer:
    handler = lambda *args, **kwargs: SampleSiteHandler(  # noqa: E731
        *args, directory=directory, **kwargs
    )
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    host = os.getenv("SAMPLE_SITE_HOST", "0.0.0.0")
    port = int(os.getenv("SAMPLE_SITE_PORT", "8080"))
    directory = os.getenv("SAMPLE_SITE_DIR", "sample_site")
    extra_ports = [
        int(value)
        for value in os.getenv("SAMPLE_SITE_EXTRA_PORTS", "8081").split(",")
        if value.strip()
    ]

    servers = [build_server(host=host, port=port, directory=directory)]
    print(f"sample site listening on http://{host}:{port} (dir={directory})")
    for extra_port in extra_ports:
        if extra_port == port:
            continue
        try:
            server = build_server(host=host, port=extra_port, directory=directory)
        except OSError as exc:
            print(f"skip extra port {extra_port}: {exc}")
            continue
        servers.append(server)
        print(f"sample site (alternate port) listening on http://{host}:{extra_port}")

    threads: list[threading.Thread] = []
    for server in servers[1:]:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        threads.append(thread)

    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for server in servers:
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)


if __name__ == "__main__":
    main()
