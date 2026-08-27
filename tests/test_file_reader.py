import unittest

from app.backend import file_reader


class FileReaderTests(unittest.TestCase):
    def test_extract_utf8_text_and_sanitize_filename(self):
        name, text = file_reader.extract_text(
            "../../ghi-chu.txt",
            "Xin chào Việt Nam".encode(),
            max_bytes=1024,
            max_chars=100,
        )

        self.assertEqual(name, "ghi-chu.txt")
        self.assertEqual(text, "Xin chào Việt Nam")

    def test_reject_unsupported_extension(self):
        with self.assertRaisesRegex(file_reader.FileExtractionError, "chưa được hỗ trợ"):
            file_reader.extract_text(
                "payload.exe",
                b"not executable content",
                max_bytes=1024,
                max_chars=100,
            )

    def test_reject_empty_file(self):
        with self.assertRaisesRegex(file_reader.FileExtractionError, "đang trống"):
            file_reader.extract_text("empty.txt", b"", max_bytes=1024, max_chars=100)

    def test_reject_file_over_size_limit(self):
        with self.assertRaisesRegex(file_reader.FileExtractionError, "vượt quá giới hạn"):
            file_reader.extract_text(
                "large.txt",
                b"123456",
                max_bytes=5,
                max_chars=100,
            )

    def test_reject_extracted_text_over_character_limit(self):
        with self.assertRaisesRegex(file_reader.FileExtractionError, "sau khi trích xuất"):
            file_reader.extract_text(
                "long.txt",
                b"0123456789",
                max_bytes=100,
                max_chars=5,
            )


if __name__ == "__main__":
    unittest.main()
