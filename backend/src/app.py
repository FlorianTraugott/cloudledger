"""
CloudLedger - AWS Lambda handler (Python 3.13).

One Lambda function serves every route of the Amazon API Gateway HTTP API.
API Gateway passes the matched route in event["routeKey"], e.g. "GET /expenses".

Routes
  GET    /health                     -> liveness check
  GET    /expenses?month=YYYY-MM     -> list expenses for a month
  POST   /expenses                   -> create an expense
  DELETE /expenses/{id}              -> delete an expense (and its receipt)
  GET    /summary?month=YYYY-MM      -> totals, per-category breakdown, budget usage
  PUT    /budget                     -> set the monthly budget
  POST   /receipts/upload-url        -> presigned S3 URL to upload a receipt image

The caller identifies themself with the "x-user-id" header (a simple workspace name).
"""

import json
import logging
import re
import uuid
from datetime import datetime, timezone

from store import EXPENSE_PREFIX, get_store

log = logging.getLogger()
log.setLevel(logging.INFO)

CATEGORIES = ["Food", "Travel", "Shopping", "Bills", "Education", "Health", "Entertainment", "Other"]
ALLOWED_RECEIPT_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "application/pdf": "pdf"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
USER_RE = re.compile(r"^[a-z0-9_-]{2,32}$")

_store = None


def store():
    global _store
    if _store is None:
        _store = get_store()
    return _store


# --------------------------------------------------------------------------- #
#  helpers
# --------------------------------------------------------------------------- #
class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def respond(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }


def user_of(event):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    user = (headers.get("x-user-id") or "demo").strip().lower()
    if not USER_RE.match(user):
        raise ApiError(400, "x-user-id must be 2-32 chars: a-z, 0-9, _ or -")
    return user


def body_of(event):
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        raw = base64.b64decode(raw).decode()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise ApiError(400, "Request body must be valid JSON")


def month_of(event):
    month = (event.get("queryStringParameters") or {}).get("month")
    if month is None:
        return datetime.now(timezone.utc).strftime("%Y-%m")
    if not MONTH_RE.match(month):
        raise ApiError(400, "month must look like YYYY-MM")
    return month


def id_to_sk(expense_id):
    # public id  "2026-11-08_ab12..."  <->  sort key  "EXP#2026-11-08#ab12..."
    if "_" not in expense_id:
        raise ApiError(400, "Invalid expense id")
    date, uid = expense_id.split("_", 1)
    return f"{EXPENSE_PREFIX}{date}#{uid}"


def present(item):
    """Shape a stored item for the client."""
    _, date, uid = item["sk"].split("#", 2)
    out = {
        "id": f"{date}_{uid}",
        "title": item["title"],
        "amount": item["amount"],
        "category": item["category"],
        "date": date,
        "note": item.get("note", ""),
        "createdAt": item.get("createdAt"),
        "receiptKey": item.get("receiptKey"),
        "receiptUrl": None,
    }
    if item.get("receiptKey"):
        out["receiptUrl"] = store().download_url(item["receiptKey"])
    return out


# --------------------------------------------------------------------------- #
#  route handlers
# --------------------------------------------------------------------------- #
def health(event, user):
    return 200, {"status": "ok", "service": "cloudledger", "time": datetime.now(timezone.utc).isoformat()}


def list_expenses(event, user):
    month = month_of(event)
    items = store().list_expenses(user, month)
    return 200, {"month": month, "items": [present(i) for i in items]}


def create_expense(event, user):
    data = body_of(event)
    title = str(data.get("title", "")).strip()
    category = data.get("category", "Other")
    date = data.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    note = str(data.get("note", "")).strip()[:300]
    receipt_key = data.get("receiptKey")

    try:
        amount = round(float(data.get("amount")), 2)
    except (TypeError, ValueError):
        raise ApiError(400, "amount must be a number")
    if not title or len(title) > 80:
        raise ApiError(400, "title is required (max 80 chars)")
    if amount <= 0 or amount > 10_000_000:
        raise ApiError(400, "amount must be between 0 and 10,000,000")
    if category not in CATEGORIES:
        raise ApiError(400, f"category must be one of {CATEGORIES}")
    if not DATE_RE.match(date):
        raise ApiError(400, "date must look like YYYY-MM-DD")
    if receipt_key and not str(receipt_key).startswith(f"receipts/{user}/"):
        raise ApiError(400, "receiptKey does not belong to this user")

    item = {
        "sk": f"{EXPENSE_PREFIX}{date}#{uuid.uuid4().hex[:12]}",
        "title": title,
        "amount": amount,
        "category": category,
        "note": note,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    if receipt_key:
        item["receiptKey"] = receipt_key
    saved = store().put_expense(user, item)
    log.info("expense created user=%s amount=%s category=%s", user, amount, category)
    return 201, present(saved)


def delete_expense(event, user):
    expense_id = (event.get("pathParameters") or {}).get("id", "")
    sk = id_to_sk(expense_id)
    existing = store().get_expense(user, sk)
    if not existing:
        raise ApiError(404, "Expense not found")
    if existing.get("receiptKey"):
        store().delete_receipt(existing["receiptKey"])
    store().delete_expense(user, sk)
    log.info("expense deleted user=%s id=%s", user, expense_id)
    return 200, {"deleted": expense_id}


def summary(event, user):
    month = month_of(event)
    items = store().list_expenses(user, month)
    by_category = {}
    for i in items:
        by_category[i["category"]] = round(by_category.get(i["category"], 0) + float(i["amount"]), 2)
    total = round(sum(by_category.values()), 2)
    budget = float(store().get_budget(user) or 0)
    biggest = max(items, key=lambda i: float(i["amount"]), default=None)
    return 200, {
        "month": month,
        "total": total,
        "count": len(items),
        "budget": budget,
        "remaining": round(budget - total, 2) if budget else None,
        "budgetUsedPct": round(total / budget * 100, 1) if budget else None,
        "byCategory": dict(sorted(by_category.items(), key=lambda kv: -kv[1])),
        "largest": {"title": biggest["title"], "amount": float(biggest["amount"])} if biggest else None,
    }


def set_budget(event, user):
    data = body_of(event)
    try:
        amount = round(float(data.get("amount")), 2)
    except (TypeError, ValueError):
        raise ApiError(400, "amount must be a number")
    if amount < 0:
        raise ApiError(400, "amount cannot be negative")
    store().set_budget(user, amount)
    return 200, {"budget": amount}


def receipt_upload_url(event, user):
    data = body_of(event)
    content_type = data.get("contentType", "")
    if content_type not in ALLOWED_RECEIPT_TYPES:
        raise ApiError(400, "Only PNG, JPEG, WEBP or PDF receipts are allowed")
    key = f"receipts/{user}/{uuid.uuid4().hex}.{ALLOWED_RECEIPT_TYPES[content_type]}"
    return 200, {"key": key, "uploadUrl": store().upload_url(key, content_type), "method": "PUT"}


ROUTES = {
    "GET /health": health,
    "GET /expenses": list_expenses,
    "POST /expenses": create_expense,
    "DELETE /expenses/{id}": delete_expense,
    "GET /summary": summary,
    "PUT /budget": set_budget,
    "POST /receipts/upload-url": receipt_upload_url,
}


# --------------------------------------------------------------------------- #
#  Lambda entry point
# --------------------------------------------------------------------------- #
def lambda_handler(event, context):
    route = event.get("routeKey", "")
    handler = ROUTES.get(route)
    if handler is None:
        return respond(404, {"error": f"No route for {route}"})
    try:
        status, body = handler(event, user_of(event))
        return respond(status, body)
    except ApiError as e:
        return respond(e.status, {"error": e.message})
    except Exception:  # pragma: no cover - logged to CloudWatch
        log.exception("Unhandled error on %s", route)
        return respond(500, {"error": "Internal server error"})
