"""Isolated HTTP-boundary tests. No real models or application imports."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import httpx
import pipeline
from PIL import Image


class PipelineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.context = self.root / "vit.json"
        self.context.write_text(
            json.dumps(
                {
                    "screen_id": "redacted-screen",
                    "image_path": "redacted.png",
                    "image_redacted": True,
                    "summary": "Settings form",
                    "elements": [{"label": "button", "bbox": [5, 5, 30, 25]}],
                }
            )
        )
        Image.new("RGB", (64, 64), "black").save(self.root / "redacted.png")

    def invoke(self, handler):
        with (
            httpx.Client(transport=httpx.MockTransport(handler)) as client,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return pipeline.run_pipeline(
                self.context,
                "http://local-vlm.test/v1/chat/completions",
                "https://server-llm.test/v1/chat/completions",
                self.root / "result.json",
                server_model="server-model",
                vlm_token="local-test-token",
                server_token="remote-test-token",
                client=client,
            )

    def completion(self, text, finish="stop"):
        return httpx.Response(
            200, json={"choices": [{"finish_reason": finish, "message": {"content": text}}]}
        )

    def test_context_to_local_vlm_then_only_text_to_server_llm(self):
        calls = []

        def handler(request):
            body = json.loads(request.content)
            calls.append((request, body))
            return self.completion("Local visual analysis" if len(calls) == 1 else "Server reasoning")

        result = self.invoke(handler)
        local, remote = calls
        self.assertEqual(local[1]["model"], pipeline.MODEL)
        local_content = local[1]["messages"][1]["content"]
        self.assertTrue(local_content[0]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertIn("Settings form", local_content[1]["text"])
        self.assertNotIn("image_path", local_content[1]["text"])
        self.assertEqual(remote[1]["model"], "server-model")
        self.assertEqual(
            remote[1]["messages"][1]["content"], "Local LFM visual context:\nLocal visual analysis"
        )
        self.assertNotIn("image_url", remote[0].content.decode())
        self.assertNotIn("Settings form", remote[0].content.decode())
        self.assertEqual(local[0].headers["Authorization"], "Bearer local-test-token")
        self.assertEqual(remote[0].headers["Authorization"], "Bearer remote-test-token")
        saved = json.loads((self.root / "result.json").read_text())
        self.assertEqual(saved["delivery"], "server_llm_completed")
        self.assertEqual(saved["result"], result)
        self.assertEqual(saved["server_output"], "Server reasoning")
        self.assertNotIn("test-token", json.dumps(saved))

    def test_model_failure_does_not_forward(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(500, json={"error": "private provider details"})

        with self.assertRaisesRegex(ValueError, "HTTP 500"):
            self.invoke(handler)
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads((self.root / "result.json").read_text())["delivery"], "not_attempted")

    def test_server_failure_preserves_model_output_without_retry(self):
        calls = []

        def handler(request):
            calls.append(request)
            return self.completion("Completed local analysis") if len(calls) == 1 else httpx.Response(503)

        with self.assertRaisesRegex(ValueError, "HTTP 503"):
            self.invoke(handler)
        self.assertEqual(len(calls), 2)
        saved = json.loads((self.root / "result.json").read_text())
        self.assertEqual(saved["result"]["output"], "Completed local analysis")
        self.assertEqual(saved["delivery"], "unconfirmed")

    def test_invalid_or_truncated_output_does_not_forward(self):
        for response in (
            httpx.Response(200, json={}),
            self.completion(""),
            self.completion("Partial", "length"),
        ):
            with self.subTest(response=response):
                calls = []

                def handler(request):
                    calls.append(request)
                    return response

                with self.assertRaises(ValueError):
                    self.invoke(handler)
                self.assertEqual(len(calls), 1)
                (self.root / "result.json").unlink()

    def test_invalid_context_and_embedding_inputs_rejected(self):
        original = json.loads(self.context.read_text())
        for patch in (
            {"image_redacted": False},
            {"embeddings": [0.1, 0.2]},
            {"width": 100},
            {"elements": [{"label": "button", "bbox": [0, 0, 100, 10]}]},
            {"elements": [{"label": "button", "bbox": [0, 0, 10, 10], "confidence": float("nan")}]},
            {"bbox_units": "normalized"},
        ):
            with self.subTest(patch=patch):
                self.context.write_text(json.dumps({**original, **patch}))
                with self.assertRaises(ValueError):
                    pipeline.load_vit_context(self.context)

    def test_existing_output_not_overwritten_or_sent(self):
        (self.root / "result.json").write_text("keep")
        with self.assertRaises(FileExistsError):
            self.invoke(lambda _: self.fail("Must not send a request"))
        self.assertEqual((self.root / "result.json").read_text(), "keep")

    def test_redirect_not_followed_and_response_body_not_exposed(self):
        with self.assertRaisesRegex(ValueError, "HTTP 307") as error:
            self.invoke(
                lambda _: httpx.Response(307, headers={"Location": "https://other.test"}, text="SECRET")
            )
        self.assertNotIn("SECRET", str(error.exception))

    def test_oversized_response_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "exceeds 1 MiB"):
            self.invoke(lambda _: httpx.Response(200, content=b"x" * (pipeline.MAX_RESPONSE + 1)))


if __name__ == "__main__":
    unittest.main()
