import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.backend import main, storage


class APITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.user = {
            "id": "00000000-0000-0000-0000-000000000099",
            "email": "owner@example.com",
            "role": "admin",
        }
        self.database_patch = patch.object(storage, "connect_database", AsyncMock())
        self.session_patch = patch.object(
            storage, "get_user_by_session", AsyncMock(return_value=self.user)
        )
        self.database_patch.start()
        self.session_patch.start()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app),
            base_url="http://testserver",
            cookies={main.settings.AUTH_COOKIE_NAME: "test-session"},
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.session_patch.stop()
        self.database_patch.stop()

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
        create.assert_awaited_once_with(self.user["id"], "AI", "Test", "")

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

    async def test_anonymous_request_is_rejected(self):
        with patch.object(storage, "get_user_by_session", AsyncMock(return_value=None)):
            response = await self.client.get("/projects")
        self.assertEqual(response.status_code, 401)

    async def test_project_lookup_is_scoped_to_current_user(self):
        with (
            patch.object(main, "require_database", AsyncMock()),
            patch.object(storage, "get_project", AsyncMock(return_value=None)) as get_project,
        ):
            response = await self.client.get("/projects/not-owned")
        self.assertEqual(response.status_code, 404)
        get_project.assert_awaited_once_with("not-owned", self.user["id"])

    async def test_first_registration_claims_legacy_data(self):
        new_user = {**self.user, "created_at": "now"}
        with (
            patch.object(main.auth, "hash_password", return_value="argon-hash"),
            patch.object(storage, "create_user", AsyncMock(return_value=(new_user, True))),
            patch.object(main, "issue_session", AsyncMock()) as issue,
        ):
            response = await self.client.post(
                "/auth/register",
                json={
                    "email": "Owner@Example.com",
                    "password": "strong-password",
                    "password_confirm": "strong-password",
                },
            )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["claimed_legacy_data"])
        issue.assert_awaited_once()

    async def test_registration_rejects_mismatched_password_confirmation(self):
        with patch.object(main, "require_database", AsyncMock()):
            response = await self.client.post(
                "/auth/register",
                json={
                    "email": "owner@example.com",
                    "password": "strong-password",
                    "password_confirm": "different-password",
                },
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Mật khẩu nhập lại không khớp")

    def test_database_schema_has_no_api_key_column(self):
        self.assertNotIn("api_key", storage.SCHEMA_SQL.lower())
        self.assertIn("create table if not exists users", storage.SCHEMA_SQL.lower())
        self.assertIn("user_id uuid", storage.SCHEMA_SQL.lower())


if __name__ == "__main__":
    unittest.main()
