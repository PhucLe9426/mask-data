import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "app" / "frontend"


class FrontendIntegrityTests(unittest.TestCase):
    def test_html_ids_are_unique_and_assets_exist(self):
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        ids = re.findall(r'id="([^"]+)"', html)
        self.assertEqual(len(ids), len(set(ids)))

        asset_paths = re.findall(r'(?:src|href)="(/static/[^"]+)"', html)
        self.assertGreater(len(asset_paths), 0)
        for asset in asset_paths:
            relative = asset.split("?", 1)[0].removeprefix("/static/")
            with self.subTest(asset=asset):
                self.assertTrue((FRONTEND / relative).is_file())

    def test_required_chat_controls_exist(self):
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        for element_id in (
            "chat-input",
            "chat-send",
            "settings-dialog",
            "model-options",
            "load-models",
            "app-confirm-dialog",
            "auth-form",
            "auth-email",
            "auth-password",
            "auth-password-confirm",
            "logout-button",
        ):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', html)

    def test_frontend_does_not_persist_api_key(self):
        source = (FRONTEND / "js" / "settings.js").read_text(encoding="utf-8")
        save_function = source.split("function saveModelConfig()", 1)[1].split(
            "function openModelSettings", 1
        )[0]

        self.assertNotIn("api-key", save_function)
        self.assertNotIn("apiKey", save_function)
        self.assertIn("localStorage.setItem", save_function)

    def test_authentication_token_is_not_saved_in_browser_storage(self):
        source = (FRONTEND / "js" / "auth.js").read_text(encoding="utf-8")
        self.assertNotIn("localStorage", source)
        self.assertNotIn("sessionStorage", source)


if __name__ == "__main__":
    unittest.main()
