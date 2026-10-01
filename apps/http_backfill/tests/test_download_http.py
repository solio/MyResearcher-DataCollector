"""Real authenticated node and hub downloads, using offline source fixtures."""
import csv
import io
import json
import unittest
import urllib.error
import urllib.request

import test_fleet_http as fleet_fixture


class DownloadHTTPTests(unittest.TestCase):
    setUp = fleet_fixture.FleetHTTPTests.setUp
    tearDown = fleet_fixture.FleetHTTPTests.tearDown
    collector = fleet_fixture.FleetHTTPTests.collector
    serve = fleet_fixture.FleetHTTPTests.serve
    request = fleet_fixture.FleetHTTPTests.request
    register = fleet_fixture.FleetHTTPTests.register
    synchronize = fleet_fixture.FleetHTTPTests.synchronize

    def download(self, path, base=None, token=None, auth=True):
        headers = {"Authorization": "Bearer " + (token or self.token)} if auth else {}
        request = urllib.request.Request((base or self.base) + path, headers=headers)
        try:
            response = self.opener.open(request, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, dict(response.headers), response.read()

    def test_api_only_node_exports_same_post_rows_and_real_body_without_secrets(self):
        before = [len(w.calls) for w in self.wires]
        code, headers, data = self.download("/api/download/posts?scope=local&format=jsonl", self.a_url, self.node_token)
        self.assertEqual(code, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertEqual(headers["X-Collector-Post-Count"], "2")
        self.assertEqual(headers["X-Model-Database-Eligible"], "false")
        rows = [json.loads(line) for line in data.decode().splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["content"], "共同正文")
        self.assertEqual(rows[0]["title"], self.a_row["post_title"])
        self.assertIsNone(rows[1]["content"])
        self.assertNotIn(self.node_token.encode(), data)
        self.assertEqual([len(w.calls) for w in self.wires], before)

    def test_merged_export_deduplicates_and_preserves_empty_vs_missing(self):
        self.register("a", self.a_url)
        self.register("b", self.b_url)
        self.synchronize()
        before = [len(w.calls) for w in self.wires]
        code, headers, data = self.download("/api/download/posts?scope=fleet&format=csv")
        self.assertEqual(code, 200)
        self.assertEqual(headers["X-Collector-Post-Count"], "3")
        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
        self.assertEqual([row["source_item_id"] for row in rows], ["9101", "9102", "9103"])
        self.assertEqual([row["content_missing"] for row in rows], ["False", "True", "False"])
        self.assertEqual(rows[0]["content"], "共同正文")
        self.assertEqual([len(w.calls) for w in self.wires], before)

    def test_unauthenticated_and_invalid_queries_do_not_return_a_fake_download(self):
        code, headers, _ = self.download("/api/download/posts", auth=False)
        self.assertEqual(code, 401)
        self.assertNotIn("Content-Disposition", headers)
        for query in ("scope=../data", "format=html", "scope=local&scope=fleet", "token=private", "format=csv&format=jsonl"):
            with self.subTest(query=query):
                code, headers, _ = self.download("/api/download/posts?" + query)
                self.assertEqual(code, 400)
                self.assertNotIn("Content-Disposition", headers)
        code, headers, _ = self.download("/api/download/posts?scope=fleet", self.a_url, self.node_token)
        self.assertEqual(code, 503)
        self.assertNotIn("Content-Disposition", headers)


if __name__ == "__main__":
    unittest.main()
