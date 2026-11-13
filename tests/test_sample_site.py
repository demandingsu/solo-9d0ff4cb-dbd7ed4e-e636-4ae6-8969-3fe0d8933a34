import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[1] / "sample_site"
INTENTIONALLY_MISSING = {"/detail/d-004-correction.html"}
# Paths served by sample_site_server.py as redirects rather than static files.
DYNAMIC_REDIRECT_PATHS = {
    "/jump/same-site.html",
    "/jump/same-site-absolute.html",
    "/jump/cross-host-cancel.html",
    "/jump/cross-port-pagination.html",
}


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for key in ("href", "src"):
            if key in attrs:
                self.links.append(attrs[key])


class SampleSiteTests(unittest.TestCase):
    def parse_links(self, relative_path):
        parser = LinkParser()
        parser.feed((ROOT / relative_path).read_text(encoding="utf-8"))
        return parser.links

    def test_local_links_exist_except_intentional_404(self):
        for page in [
            "index.html",
            "redirects.html",
            "notices/page-1.html",
            "notices/page-2.html",
            "notices/page-3.html",
            "notices/jump-demo.html",
            "notices/redirect-demo.html",
        ]:
            with self.subTest(page=page):
                for link in self.parse_links(page):
                    if link.startswith(("http://", "https://", "mailto:", "#")):
                        continue
                    absolute = urljoin(f"/{page}", link)
                    path = urlsplit(absolute).path
                    if path in INTENTIONALLY_MISSING or path in DYNAMIC_REDIRECT_PATHS:
                        continue
                    self.assertTrue((ROOT / path.lstrip("/")).exists(), f"{page} -> {path}")

    def test_redirect_fixture_entry_pages_exist(self):
        for path in [
            "detail/d-006-original.html",
            "detail/d-006-cancel-external.html",
            "detail/d-007-original.html",
            "detail/d-007-cancel.html",
            "notices/redirect-page-2.html",
        ]:
            self.assertTrue((ROOT / path).exists(), path)

    def test_jump_demo_displays_blocking_links(self):
        jump_demo = (ROOT / "notices" / "jump-demo.html").read_text(encoding="utf-8")
        self.assertIn("/jump/cross-host-cancel.html", jump_demo)
        redirect_demo = (ROOT / "notices" / "redirect-demo.html").read_text(encoding="utf-8")
        self.assertIn("/jump/cross-port-pagination.html", redirect_demo)
        redirects_page = (ROOT / "redirects.html").read_text(encoding="utf-8")
        for path in sorted(DYNAMIC_REDIRECT_PATHS):
            self.assertIn(path, redirects_page)

    def test_pagination_contains_all_three_pages(self):
        first_page_links = set(self.parse_links("notices/page-1.html"))
        self.assertIn("page-2.html", first_page_links)
        self.assertIn("page-3.html", first_page_links)

    def test_duplicate_announcement_appears_across_pages(self):
        page_two = "\n".join(self.parse_links("notices/page-2.html"))
        page_three = "\n".join(self.parse_links("notices/page-3.html"))
        self.assertIn("d-001-original.html", page_two)
        self.assertIn("d-001-original.html", page_three)


if __name__ == "__main__":
    unittest.main()
