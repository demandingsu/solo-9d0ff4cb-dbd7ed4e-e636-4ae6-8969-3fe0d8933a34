import os
import unittest

try:
    from app import app
except ModuleNotFoundError:  # flask is optional for core tests
    app = None


class TemplateRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if app is None:
            raise unittest.SkipTest("flask is not installed")
        app.config["TESTING"] = True
        cls.client = app.test_client()

    def test_home_page_renders_redirect_demo_entries(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("302 跳转样例", body)
        self.assertIn("GSB-2026-006", body)
        self.assertIn("GSB-2026-007", body)

    def test_healthz(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

    def test_invalid_url_shows_input_error(self):
        response = self.client.post("/", data={"project_no": "GSB-2026-001", "list_url": "ftp://x"})
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("完整的 http:// 或 https:// 地址", body)


if __name__ == "__main__":
    unittest.main()
