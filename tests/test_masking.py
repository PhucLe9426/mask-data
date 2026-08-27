import unittest
from unittest.mock import AsyncMock, patch

from app.backend import masking


class MaskingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        masking._SESSIONS.clear()

    def tearDown(self):
        masking._SESSIONS.clear()

    def test_reconcile_restores_accents_and_removes_duplicates(self):
        original = "Tên tôi là Lê Trọng Phúc, số điện thoại 0913885457."
        detected = [
            {"text": "LE_TRONG_PHUC", "type": "TEN_NGUOI"},
            {"text": "Lê Trọng Phúc", "type": "TEN_NGUOI"},
            {"text": "0913885457", "type": "SO_DIEN_THOAI"},
            {"text": "không tồn tại", "type": "UNKNOWN"},
        ]

        result = masking.reconcile_entities(original, detected)

        self.assertEqual(
            result,
            [
                {"text": "Lê Trọng Phúc", "type": "TEN_NGUOI"},
                {"text": "0913885457", "type": "SO_DIEN_THOAI"},
            ],
        )

    def test_mask_and_unmask_round_trip(self):
        original = "Liên hệ 0913885457 hoặc phuc@example.com."
        entities = [
            {"text": "0913885457", "type": "SO_DIEN_THOAI"},
            {"text": "phuc@example.com", "type": "EMAIL"},
        ]

        masked, mapping = masking.mask_text(original, entities)

        self.assertNotIn("0913885457", masked)
        self.assertNotIn("phuc@example.com", masked)
        self.assertEqual(masking.unmask_text(masked, mapping), original)

    async def test_process_pipeline_uses_mocked_local_llm(self):
        original = "Số điện thoại của tôi là 0913885457"
        detected = [{"text": "0913885457", "type": "SO_DIEN_THOAI"}]

        with patch.object(
            masking,
            "detect_entities_chunked",
            AsyncMock(return_value=detected),
        ) as detector:
            result = await masking.process_mask(original)

        detector.assert_awaited_once()
        self.assertEqual(result["entity_count"], 1)
        self.assertIn("[SO_DIEN_THOAI_1]", result["masked_text"])
        restored = masking.process_unmask(result["session_id"], result["masked_text"])
        self.assertEqual(restored["final_text"], original)

    def test_unknown_session_cannot_be_unmasked(self):
        with self.assertRaises(KeyError):
            masking.process_unmask("missing-session", "[TEN_NGUOI_1]")

    def test_split_detection_text_respects_long_inputs(self):
        text = ("một đoạn văn bản dài " * 180).strip()
        chunks = masking.split_detection_text(text, 1000)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 1000 for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
