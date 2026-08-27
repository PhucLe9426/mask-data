import unittest
from unittest.mock import patch

from app.backend import public_llm


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            request = public_llm.httpx.Request("GET", "https://provider.invalid/models")
            raise public_llm.httpx.HTTPStatusError(
                "provider error",
                request=request,
                response=public_llm.httpx.Response(self.status_code, request=request),
            )


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def get(self, url, **kwargs):
        self.calls.append({"url": str(url), **kwargs})
        return self.response


class PublicLLMModelTests(unittest.IsolatedAsyncioTestCase):
    def test_derive_openai_models_url(self):
        cases = {
            "https://api.openai.com/v1/chat/completions": "https://api.openai.com/v1/models",
            "https://example.com/v1/responses": "https://example.com/v1/models",
            "https://example.com/v1/models": "https://example.com/v1/models",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(public_llm._openai_models_url(source), expected)

    async def test_gemini_only_returns_generate_content_models(self):
        client = FakeClient(FakeResponse({
            "models": [
                {
                    "name": "models/gemini-chat",
                    "displayName": "Gemini Chat",
                    "supportedGenerationMethods": ["generateContent"],
                },
                {
                    "name": "models/gemini-embedding",
                    "displayName": "Embedding",
                    "supportedGenerationMethods": ["embedContent"],
                },
            ]
        }))

        with patch.object(public_llm.httpx, "AsyncClient", return_value=client):
            models = await public_llm.list_public_models(
                provider="gemini",
                api_url="https://unused.invalid",
                api_key="temporary-key",
            )

        self.assertEqual(models, [{"id": "gemini-chat", "display_name": "Gemini Chat"}])
        self.assertEqual(client.calls[0]["headers"]["x-goog-api-key"], "temporary-key")

    async def test_anthropic_models_are_normalized(self):
        client = FakeClient(FakeResponse({
            "data": [{"id": "claude-test", "display_name": "Claude Test"}]
        }))

        with patch.object(public_llm.httpx, "AsyncClient", return_value=client):
            models = await public_llm.list_public_models(
                provider="anthropic",
                api_url="https://unused.invalid",
                api_key="temporary-key",
            )

        self.assertEqual(models[0]["id"], "claude-test")
        self.assertEqual(client.calls[0]["headers"]["x-api-key"], "temporary-key")

    async def test_openai_compatible_uses_bearer_key_and_models_endpoint(self):
        client = FakeClient(FakeResponse({"data": [{"id": "gpt-test"}]}))

        with patch.object(public_llm.httpx, "AsyncClient", return_value=client):
            models = await public_llm.list_public_models(
                provider="openai_compatible",
                api_url="https://example.com/v1/chat/completions",
                api_key="temporary-key",
            )

        self.assertEqual(models[0]["id"], "gpt-test")
        self.assertEqual(client.calls[0]["url"], "https://example.com/v1/models")
        self.assertEqual(client.calls[0]["headers"]["Authorization"], "Bearer temporary-key")


if __name__ == "__main__":
    unittest.main()
