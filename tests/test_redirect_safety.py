import os
import threading
import unittest
from contextlib import contextmanager
from functools import partial
from http.server import ThreadingHTTPServer

try:
    import requests  # noqa: F401
    from bs4 import BeautifulSoup  # noqa: F401

    from tendercheck.models import STATUS_CANCELLED, STATUS_PENDING
    from tendercheck.scraper import (
        BlockedRedirectError,
        HttpFetcher,
        TenderScraper,
        UrlMapper,
        url_origin,
    )
except ModuleNotFoundError:  # requests/beautifulsoup4 are optional for core tests
    TenderScraper = None

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_site")


@contextmanager
def patched_env(**values):
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update({key: str(value) for key, value in values.items()})
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if TenderScraper is None:  # pragma: no cover - dependency guard

    @unittest.skip("scraper dependencies are not installed")
    class RedirectSafetyTests(unittest.TestCase):
        pass

else:
    from sample_site_server import SampleSiteHandler

    class RecordingHandler(SampleSiteHandler):
        """Sample-site handler that records every request it receives."""

        def do_GET(self):  # noqa: N802 - stdlib naming
            self.server.request_log.append(self.path)
            super().do_GET()

    def start_server(directory=ROOT):
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            partial(RecordingHandler, directory=directory),
        )
        server.request_log = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    @contextmanager
    def running_servers(env=None):
        with patched_env(**(env or {})):
            main, main_thread = start_server()
            alt, alt_thread = start_server()
            with patched_env(SAMPLE_SITE_ALT_PORT=str(alt.server_port)):
                try:
                    yield main, alt
                finally:
                    for server, thread in ((main, main_thread), (alt, alt_thread)):
                        server.shutdown()
                        server.server_close()
                        thread.join(timeout=5)

    class RedirectSafetyTests(unittest.TestCase):
        def test_same_site_redirect_is_followed(self):
            with running_servers() as (main, alt):
                url = f"http://127.0.0.1:{main.server_port}/jump/same-site.html"
                result = TenderScraper(project_no="GSB-2026-001", list_url=url).run()
                self.assertEqual(len(result.pages), 3)
                self.assertNotEqual(result.status, STATUS_PENDING)
                self.assertEqual(result.deadline.strftime("%Y-%m-%d %H:%M"), "2026-11-27 17:00")
                self.assertFalse(result.listing_incomplete)
                self.assertEqual(alt.request_log, [])

        def test_absolute_same_site_redirect_is_followed(self):
            with running_servers() as (main, alt):
                public = f"http://127.0.0.1:{main.server_port}"
                with patched_env(SAMPLE_SITE_PUBLIC_URL=public):
                    url = f"{public}/jump/same-site-absolute.html"
                    result = TenderScraper(project_no="GSB-2026-001", list_url=url).run()
                self.assertEqual(len(result.pages), 3)
                self.assertFalse(result.listing_incomplete)

        def test_cross_host_detail_redirect_is_blocked_before_request(self):
            with running_servers() as (main, alt):
                url = f"http://127.0.0.1:{main.server_port}/notices/jump-demo.html"
                result = TenderScraper(project_no="GSB-2026-006", list_url=url).run()
                self.assertEqual(result.status, STATUS_PENDING)
                self.assertNotEqual(result.status, STATUS_CANCELLED)
                blocked = [item for item in result.announcements if item.blocked_redirect]
                self.assertEqual(len(blocked), 1)
                self.assertEqual(blocked[0].kind, "cancellation")
                # The in-site original link must be preserved for manual review.
                self.assertIn("/jump/cross-host-cancel.html", blocked[0].url)
                self.assertFalse(blocked[0].fetch_ok)
                self.assertIsNone(blocked[0].deadline)
                self.assertIsNone(result.cancellation_notice)
                self.assertIsNone(result.deadline)
                self.assertTrue(
                    any("跨站跳转被阻止" in issue for issue in result.issues), result.issues
                )
                # The external target must never be requested.
                external = "/detail/d-006-cancel-external.html"
                self.assertFalse(any(external in entry for entry in main.request_log))
                self.assertFalse(any(external in entry for entry in alt.request_log))

        def test_cross_port_pagination_redirect_keeps_collected_pages(self):
            with running_servers() as (main, alt):
                url = f"http://127.0.0.1:{main.server_port}/notices/redirect-demo.html"
                result = TenderScraper(project_no="GSB-2026-007", list_url=url).run()
                self.assertEqual(len(result.pages), 1)
                self.assertTrue(result.listing_incomplete)
                self.assertNotEqual(result.status, STATUS_CANCELLED)
                self.assertEqual(result.status, STATUS_PENDING)
                self.assertIsNone(result.cancellation_notice)
                self.assertTrue(
                    any("列表不完整" in issue for issue in result.issues), result.issues
                )
                # The alternate port must never be contacted.
                self.assertEqual(alt.request_log, [])
                self.assertFalse(
                    any("redirect-page-2" in entry for entry in main.request_log)
                )

        def test_cross_scheme_redirect_is_blocked(self):
            class HttpsRedirectHandler(SampleSiteHandler):
                def do_GET(self):  # noqa: N802 - stdlib naming
                    self.server.request_log.append(self.path)
                    if self.path == "/to-https":
                        self.send_response(302)
                        self.send_header(
                            "Location",
                            f"https://{self.headers.get('Host', '')}/secret.html",
                        )
                        self.end_headers()
                        return
                    super().do_GET()

            server = ThreadingHTTPServer(
                ("127.0.0.1", 0), partial(HttpsRedirectHandler, directory=ROOT)
            )
            server.request_log = []
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/to-https"
                with self.assertRaises(BlockedRedirectError) as ctx:
                    HttpFetcher().fetch(
                        url, origin=("http", f"127.0.0.1:{server.server_port}")
                    )
                self.assertIn("同一站点", str(ctx.exception))
                self.assertTrue(ctx.exception.target_url.startswith("https://"))
                # Only the first hop was requested; the https target never was.
                self.assertEqual(server.request_log, ["/to-https"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

        def test_url_mapper_round_trip_for_compose_addresses(self):
            with patched_env(
                SAMPLE_SITE_PUBLIC_URL="http://localhost:8080",
                SAMPLE_SITE_INTERNAL_URL="http://sample-site:8080",
            ):
                mapper = UrlMapper()
                fetch_url = mapper.to_fetch_url(
                    "http://localhost:8080/notices/page-1.html"
                )
                self.assertEqual(
                    fetch_url, "http://sample-site:8080/notices/page-1.html"
                )
                self.assertEqual(
                    mapper.to_logical_url(fetch_url),
                    "http://localhost:8080/notices/page-1.html",
                )
                self.assertEqual(
                    mapper.to_fetch_url("http://127.0.0.1:8080/jump/same-site.html"),
                    "http://sample-site:8080/jump/same-site.html",
                )
                # A different port must not be mapped onto the sample site.
                self.assertEqual(
                    mapper.to_fetch_url("http://localhost:8081/notices/page-2.html"),
                    "http://localhost:8081/notices/page-2.html",
                )

        def test_compose_mapping_still_allows_same_site_redirects(self):
            with running_servers() as (main, alt):
                public = f"http://localhost:{main.server_port}"
                internal = f"http://127.0.0.1:{main.server_port}"
                with patched_env(
                    SAMPLE_SITE_PUBLIC_URL=public,
                    SAMPLE_SITE_INTERNAL_URL=internal,
                ):
                    result = TenderScraper(
                        project_no="GSB-2026-001",
                        list_url=f"{public}/jump/same-site.html",
                    ).run()
                self.assertEqual(len(result.pages), 3)
                self.assertTrue(all("localhost" in page for page in result.pages))
                self.assertNotEqual(result.status, STATUS_PENDING)

        def test_compose_mapping_blocks_cross_port_after_public_internal_mapping(self):
            with running_servers() as (main, alt):
                public = f"http://localhost:{main.server_port}"
                internal = f"http://127.0.0.1:{main.server_port}"
                with patched_env(
                    SAMPLE_SITE_PUBLIC_URL=public,
                    SAMPLE_SITE_INTERNAL_URL=internal,
                    SAMPLE_SITE_ALT_PORT="1",
                ):
                    result = TenderScraper(
                        project_no="GSB-2026-007",
                        list_url=f"{public}/notices/redirect-demo.html",
                    ).run()
                self.assertEqual(len(result.pages), 1)
                self.assertTrue(result.listing_incomplete)
                self.assertNotEqual(result.status, STATUS_CANCELLED)


if __name__ == "__main__":
    unittest.main()
