"""
Storage layer for CloudLedger.

Two interchangeable back-ends:
  * DynamoStore  -> used on AWS (Amazon DynamoDB + Amazon S3)
  * LocalStore   -> used on a laptop (JSON file + local folder), so the app can be
                    demoed and tested without an AWS account.

The back-end is chosen with the STORAGE environment variable ("dynamodb" | "local").

Data model (single DynamoDB table, on-demand billing):
  Partition key  userId  (S)   e.g. "ayush"
  Sort key       sk      (S)   "EXP#<yyyy-mm-dd>#<uuid>"  for an expense
                               "BUDGET"                    for the monthly budget
Sorting the expense key by date lets us fetch one month with a single Query:
  begins_with(sk, "EXP#2026-11")
"""

import json
import os
import threading
from decimal import Decimal

EXPENSE_PREFIX = "EXP#"
BUDGET_KEY = "BUDGET"


def _to_plain(value):
    """Convert DynamoDB Decimals into normal JSON-friendly numbers."""
    if isinstance(value, list):
        return [_to_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_plain(v) for k, v in value.items()}
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


# --------------------------------------------------------------------------- #
#  AWS implementation
# --------------------------------------------------------------------------- #
class DynamoStore:
    def __init__(self, table_name, bucket_name, region):
        import boto3
        from boto3.dynamodb.conditions import Key
        from botocore.config import Config

        self._Key = Key
        self.table = boto3.resource("dynamodb", region_name=region).Table(table_name)
        # Regional endpoint + SigV4 so presigned URLs work from the browser (CORS-safe).
        self.s3 = boto3.client(
            "s3",
            region_name=region,
            endpoint_url=f"https://s3.{region}.amazonaws.com",
            config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
        )
        self.bucket = bucket_name

    # ---- expenses -------------------------------------------------------- #
    def put_expense(self, user_id, item):
        record = dict(item)
        record["userId"] = user_id
        record["amount"] = Decimal(str(item["amount"]))
        self.table.put_item(Item=record)
        return _to_plain(record)

    def list_expenses(self, user_id, month=None):
        prefix = EXPENSE_PREFIX + (month or "")
        cond = self._Key("userId").eq(user_id) & self._Key("sk").begins_with(prefix)
        items, kwargs = [], {"KeyConditionExpression": cond, "ScanIndexForward": False}
        while True:
            resp = self.table.query(**kwargs)
            items.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return _to_plain(items)

    def get_expense(self, user_id, sk):
        resp = self.table.get_item(Key={"userId": user_id, "sk": sk})
        item = resp.get("Item")
        return _to_plain(item) if item else None

    def delete_expense(self, user_id, sk):
        self.table.delete_item(Key={"userId": user_id, "sk": sk})

    # ---- budget ---------------------------------------------------------- #
    def get_budget(self, user_id):
        item = self.table.get_item(Key={"userId": user_id, "sk": BUDGET_KEY}).get("Item")
        return _to_plain(item["amount"]) if item else 0

    def set_budget(self, user_id, amount):
        self.table.put_item(
            Item={"userId": user_id, "sk": BUDGET_KEY, "amount": Decimal(str(amount))}
        )

    # ---- receipts (Amazon S3) -------------------------------------------- #
    def upload_url(self, key, content_type):
        return self.s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=300,
        )

    def download_url(self, key):
        return self.s3.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=900
        )

    def delete_receipt(self, key):
        self.s3.delete_object(Bucket=self.bucket, Key=key)


# --------------------------------------------------------------------------- #
#  Local implementation (laptop demo / unit tests)
# --------------------------------------------------------------------------- #
class LocalStore:
    _lock = threading.Lock()

    def __init__(self, data_dir, public_base="/local-receipts"):
        self.data_dir = data_dir
        self.db_path = os.path.join(data_dir, "db.json")
        self.receipt_dir = os.path.join(data_dir, "receipts")
        self.public_base = public_base
        os.makedirs(self.receipt_dir, exist_ok=True)

    def _load(self):
        if not os.path.exists(self.db_path):
            return {}
        with open(self.db_path) as f:
            return json.load(f)

    def _save(self, db):
        os.makedirs(self.receipt_dir, exist_ok=True)
        with open(self.db_path, "w") as f:
            json.dump(db, f, indent=2)

    def put_expense(self, user_id, item):
        with self._lock:
            db = self._load()
            record = dict(item, userId=user_id)
            db.setdefault(user_id, {})[item["sk"]] = record
            self._save(db)
            return record

    def list_expenses(self, user_id, month=None):
        prefix = EXPENSE_PREFIX + (month or "")
        rows = [v for k, v in self._load().get(user_id, {}).items() if k.startswith(prefix)]
        return sorted(rows, key=lambda r: r["sk"], reverse=True)

    def get_expense(self, user_id, sk):
        return self._load().get(user_id, {}).get(sk)

    def delete_expense(self, user_id, sk):
        with self._lock:
            db = self._load()
            db.get(user_id, {}).pop(sk, None)
            self._save(db)

    def get_budget(self, user_id):
        return self._load().get(user_id, {}).get(BUDGET_KEY, {}).get("amount", 0)

    def set_budget(self, user_id, amount):
        with self._lock:
            db = self._load()
            db.setdefault(user_id, {})[BUDGET_KEY] = {"amount": amount}
            self._save(db)

    def upload_url(self, key, content_type):
        return f"{self.public_base}/{key}"

    def download_url(self, key):
        return f"{self.public_base}/{key}"

    def delete_receipt(self, key):
        path = os.path.join(self.receipt_dir, key.replace("/", "_"))
        if os.path.exists(path):
            os.remove(path)


def get_store():
    if os.environ.get("STORAGE", "dynamodb") == "local":
        return LocalStore(os.environ.get("LOCAL_DATA_DIR", ".data"))
    return DynamoStore(
        os.environ["TABLE_NAME"],
        os.environ["BUCKET_NAME"],
        os.environ.get("AWS_REGION", "ap-south-1"),
    )
