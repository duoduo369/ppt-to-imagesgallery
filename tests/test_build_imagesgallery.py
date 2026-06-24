import json
import unittest
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_imagesgallery import (  # noqa: E402
    _extract_json_text,
    parse_bl_omni_stdout,
    run_batch_with_retries,
    validate_batch_result,
)


class TestBuildImagesGalleryHelpers(unittest.TestCase):
    def test_parse_bl_omni_stdout(self):
        output = json.dumps({"content": "{\"pages\":[{\"page_number\":1,\"speech\":\"a\"}]}"})
        content = parse_bl_omni_stdout(output)
        self.assertIn("pages", content)

    def test_extract_json_text_from_fence(self):
        raw = "```json\n{\"pages\":[{\"page_number\":1,\"speech\":\"ok\"}]}\n```"
        extracted = _extract_json_text(raw)
        payload = json.loads(extracted)
        self.assertEqual(1, payload["pages"][0]["page_number"])

    def test_validate_batch_result(self):
        payload = {
            "pages": [
                {"page_number": 1, "speech": "a"},
                {"page_number": 2, "speech": "b"},
            ]
        }
        ordered = validate_batch_result(payload, [1, 2])
        self.assertEqual(["a", "b"], ordered)

    def test_retry_then_success(self):
        calls = {"n": 0}

        def invoke(extra_prompt: str) -> str:
            calls["n"] += 1
            if calls["n"] == 1:
                return "not-json"
            return "{\"pages\":[{\"page_number\":1,\"speech\":\"ok\"}]}"

        ordered = run_batch_with_retries(invoke, expected_page_numbers=[1], max_retries=2)
        self.assertEqual(["ok"], ordered)
        self.assertEqual(2, calls["n"])

    def test_retry_on_post_validate_failure(self):
        calls = {"n": 0}

        def invoke(extra_prompt: str) -> str:
            calls["n"] += 1
            return "{\"pages\":[{\"page_number\":1,\"speech\":\"ok\"}]}"

        def post_validate(speeches):
            if calls["n"] == 1:
                raise ValueError("alignment failed")

        ordered = run_batch_with_retries(
            invoke,
            expected_page_numbers=[1],
            max_retries=2,
            post_validate=post_validate,
        )
        self.assertEqual(["ok"], ordered)
        self.assertEqual(2, calls["n"])


if __name__ == "__main__":
    unittest.main()
