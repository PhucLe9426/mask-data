import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.backend import main, storage


class APITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_create_project_without_real_database(self):
        project = {
            "id": "00000000-0000-0000-0000-000000000001",
            "name": "AI",
            "description": "Test",
            "memory": "",
        }
        with (
            patch.object(main, "require_database", AsyncMock()),
            patch.object(storage, "create_project", AsyncMock(return_value=project)) as create,
        ):
            response = await self.client.post(
                "/projects",
                json={"name": "AI", "description": "Test", "memory": ""},
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["name"], "AI")
        create.assert_awaited_once_with("AI", "Test", "")

    async def test_reject_conflicting_conversation_filters(self):
        with patch.object(main, "require_database", AsyncMock()):
            response = await self.client.get(
                "/conversations",
                params={"project_id": "project-1", "unassigned_only": "true"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Không thể lọc đồng thời", response.json()["detail"])

    async def test_delete_missing_conversation_returns_404(self):
        with (
            patch.object(main, "require_database", AsyncMock()),
            patch.object(storage, "delete_conversation", AsyncMock(return_value=False)),
        ):
            response = await self.client.delete("/conversations/missing")

        self.assertEqual(response.status_code, 404)

    async def test_list_models_does_not_access_storage(self):
        models = [{"id": "gemini-test", "display_name": "Gemini Test"}]
        with (
            patch.object(main.public_llm, "list_public_models", AsyncMock(return_value=models)) as listing,
            patch.object(main, "storage") as storage_mock,
        ):
            response = await self.client.post(
                "/llm/models",
                json={
                    "provider": "gemini",
                    "api_url": "https://generativelanguage.googleapis.com/v1beta/models",
                    "api_key": "temporary-secret-key",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["models"][0]["id"], "gemini-test")
        self.assertEqual(listing.await_args.kwargs["api_key"], "temporary-secret-key")
        self.assertEqual(storage_mock.method_calls, [])

    def test_database_schema_has_no_api_key_column(self):
        self.assertNotIn("api_key", storage.SCHEMA_SQL.lower())


if __name__ == "__main__":
    unittest.main()
