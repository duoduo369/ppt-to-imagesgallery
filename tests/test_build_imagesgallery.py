import json
import tempfile
import unittest
import zipfile
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_imagesgallery import (  # noqa: E402
    _extract_json_text,
    load_full_speech_session_prompt,
    parse_bl_omni_stdout,
    read_manuscript,
    run_batch_with_retries,
    validate_batch_result,
)


class TestBuildImagesGalleryHelpers(unittest.TestCase):
    def test_docx_numeric_heading_style_ids_become_markdown_headings(self):
        styles_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:styleId="2">
    <w:name w:val="heading 2"/>
  </w:style>
  <w:style w:type="paragraph" w:styleId="3">
    <w:name w:val="heading 3"/>
  </w:style>
  <w:style w:type="paragraph" w:styleId="4">
    <w:name w:val="Normal"/>
  </w:style>
</w:styles>
"""
        document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p>
      <w:pPr><w:pStyle w:val="2"/></w:pPr>
      <w:r><w:t>章节标题</w:t></w:r>
    </w:p>
    <w:p>
      <w:pPr><w:pStyle w:val="4"/></w:pPr>
      <w:r><w:t>正文段落</w:t></w:r>
    </w:p>
    <w:p>
      <w:pPr><w:pStyle w:val="3"/></w:pPr>
      <w:r><w:t>小节标题</w:t></w:r>
    </w:p>
  </w:body>
</w:document>
"""
        with tempfile.TemporaryDirectory() as tmp:
            docx_path = Path(tmp) / "sample.docx"
            with zipfile.ZipFile(docx_path, "w") as zf:
                zf.writestr("word/document.xml", document_xml)
                zf.writestr("word/styles.xml", styles_xml)

            manuscript = read_manuscript(docx_path)

        self.assertIn("## 章节标题", manuscript)
        self.assertIn("正文段落", manuscript)
        self.assertIn("### 小节标题", manuscript)

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

    def test_run_batch_with_retries_uses_canonical_prompt_file(self):
        prompts = []

        def invoke(prompt_text: str) -> str:
            prompts.append(prompt_text)
            return "{\"pages\":[{\"page_number\":1,\"speech\":\"ok\"}]}"

        ordered = run_batch_with_retries(invoke, expected_page_numbers=[1], max_retries=1)
        self.assertEqual(["ok"], ordered)
        self.assertEqual(1, len(prompts))

        canonical_prompt = load_full_speech_session_prompt()
        self.assertTrue(prompts[0].startswith(canonical_prompt))
        self.assertIn("只返回 JSON", prompts[0])


if __name__ == "__main__":
    unittest.main()
