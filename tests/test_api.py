"""
Unit tests for the Lambda handler, run against the local (file-based) store.

    python3 -m unittest discover -s tests -v
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend", "src"))

TMP = tempfile.mkdtemp()
os.environ["STORAGE"] = "local"
os.environ["LOCAL_DATA_DIR"] = TMP

import app  # noqa: E402


def call(route, body=None, user="tester", query=None, path=None):
    event = {
        "routeKey": route,
        "headers": {"x-user-id": user},
        "body": json.dumps(body) if body is not None else None,
        "queryStringParameters": query,
        "pathParameters": path,
    }
    res = app.lambda_handler(event, None)
    return res["statusCode"], json.loads(res["body"])


class ApiTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP, ignore_errors=True)

    def test_health(self):
        status, body = call("GET /health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")

    def test_create_list_summary_delete(self):
        status, a = call("POST /expenses", {"title": "Bus pass", "amount": 450, "category": "Travel", "date": "2026-10-03"})
        self.assertEqual(status, 201)
        call("POST /expenses", {"title": "Pizza", "amount": 300.5, "category": "Food", "date": "2026-10-05"})
        call("PUT /budget", {"amount": 1000})

        status, listing = call("GET /expenses", query={"month": "2026-10"})
        self.assertEqual(status, 200)
        self.assertEqual(len(listing["items"]), 2)
        self.assertEqual(listing["items"][0]["title"], "Pizza")  # newest first

        status, s = call("GET /summary", query={"month": "2026-10"})
        self.assertEqual(s["total"], 750.5)
        self.assertEqual(s["count"], 2)
        self.assertEqual(s["remaining"], 249.5)
        self.assertEqual(s["byCategory"]["Travel"], 450)

        status, _ = call("DELETE /expenses/{id}", path={"id": a["id"]})
        self.assertEqual(status, 200)
        _, s = call("GET /summary", query={"month": "2026-10"})
        self.assertEqual(s["count"], 1)

    def test_users_are_isolated(self):
        call("POST /expenses", {"title": "Book", "amount": 200, "category": "Education", "date": "2026-09-01"}, user="alice")
        _, listing = call("GET /expenses", query={"month": "2026-09"}, user="bob")
        self.assertEqual(listing["items"], [])

    def test_validation(self):
        self.assertEqual(call("POST /expenses", {"title": "", "amount": 5})[0], 400)
        self.assertEqual(call("POST /expenses", {"title": "x", "amount": -5})[0], 400)
        self.assertEqual(call("POST /expenses", {"title": "x", "amount": 5, "category": "Nope"})[0], 400)
        self.assertEqual(call("GET /expenses", query={"month": "Nov"})[0], 400)
        self.assertEqual(call("GET /health", user="Bad User!")[0], 400)
        self.assertEqual(call("POST /receipts/upload-url", {"contentType": "text/html"})[0], 400)

    def test_receipt_key_must_belong_to_user(self):
        status, _ = call("POST /expenses", {"title": "x", "amount": 5, "category": "Other",
                                            "receiptKey": "receipts/someone-else/a.png"})
        self.assertEqual(status, 400)

    def test_upload_url(self):
        status, body = call("POST /receipts/upload-url", {"contentType": "image/png"})
        self.assertEqual(status, 200)
        self.assertTrue(body["key"].startswith("receipts/tester/"))

    def test_unknown_route(self):
        self.assertEqual(call("GET /nope")[0], 404)


if __name__ == "__main__":
    unittest.main()
