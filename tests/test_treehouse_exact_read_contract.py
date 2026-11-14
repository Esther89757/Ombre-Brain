"""Run the actual read handler without importing the provider-heavy server.

This isolates the auth/storage boundary and never opens the real vault or model.
"""
import ast
import hmac
import json
import logging
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class ExactReadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        source = Path(__file__).parents[1] / "src/web/hooks.py"
        tree = ast.parse(source.read_text())
        handler = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "treehouse_bucket")
        handler.decorator_list = []
        self.manager = SimpleNamespace(get_including_archive=AsyncMock())
        namespace = {"os": os, "hmac": hmac, "sh": SimpleNamespace(bucket_mgr=self.manager),
                     "_header_value": lambda request, key: request.headers.get(key, ""),
                     "logger": logging.getLogger("test")}
        exec(compile(ast.Module(body=[handler], type_ignores=[]), str(source), "exec"), namespace)
        self.handler = namespace["treehouse_bucket"]
        self.env = patch.dict(os.environ, {"OMBRE_TOOL_TOKEN": "test-tool-token"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def request(self, bucket_id="123456abcdef", token="test-tool-token"):
        return SimpleNamespace(headers={"x-ombre-hook-token": token}, path_params={"bucket_id": bucket_id})

    async def test_wrong_or_missing_token_never_reads_storage(self):
        for token in ["", "wrong"]:
            self.assertEqual((await self.handler(self.request(token=token))).status_code, 401)
        self.manager.get_including_archive.assert_not_called()

    async def test_exact_feel_and_archive_reads_no_search_or_model(self):
        bid = "feel_202611141700_V095"
        self.manager.get_including_archive.return_value = {"id": bid, "content": "exact\n text ", "metadata": {"type": "feel"}}
        response = await self.handler(self.request(bid))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body)["content"], "exact\n text ")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.manager.get_including_archive.assert_awaited_once_with(bid)

    async def test_absence_differs_from_read_failure(self):
        self.manager.get_including_archive.return_value = None
        response = await self.handler(self.request())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(json.loads(response.body), {"exists": False, "bucket_id": "123456abcdef"})
        self.manager.get_including_archive.side_effect = RuntimeError("private detail")
        response = await self.handler(self.request())
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private detail", response.body.decode())

    async def test_invalid_id_never_reads_storage(self):
        self.assertEqual((await self.handler(self.request("../private"))).status_code, 400)
        self.manager.get_including_archive.assert_not_called()


if __name__ == "__main__":
    unittest.main()
