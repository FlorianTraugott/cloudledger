# CloudLedger – Serverless Expense Tracker on AWS

CloudLedger is a web app for tracking personal or hostel expenses. You can upload receipt images,
set a monthly budget and see where your money goes. It runs on AWS serverless services,
so there are no servers to manage and it costs almost nothing when idle.

```
Browser ──HTTPS──► Amazon CloudFront ──► S3 (website files, private, OAC)
   │
   └──HTTPS/JSON──► API Gateway (HTTP API) ──► AWS Lambda (Python 3.13)
                                                 ├──► Amazon DynamoDB (expenses, budgets)
                                                 └──► Amazon S3 (receipts, presigned URLs)
                     CloudWatch: logs · metrics dashboard · error alarm      IAM: least-privilege role
```

## Features
- Add expenses with title, amount, category, date, note
- Upload receipt images/PDFs straight to Amazon S3 (presigned URLs)
- Monthly dashboard: total, budget left, count, largest expense, budget progress bar
- Spending-by-category chart, search, category filter, CSV export, month navigation
- Separate workspaces per user (`x-user-id` → DynamoDB partition key)
- Monitoring: CloudWatch dashboard, logs and error alarm; X-Ray tracing

## Project structure
| Path | What it is |
|------|------------|
| `template.yaml` | AWS SAM / CloudFormation template – all cloud infrastructure as code |
| `samconfig.toml` | Default deploy settings (stack `cloudledger`, region `ap-south-1`) |
| `backend/src/app.py` | Lambda handler: routing, validation, business logic |
| `backend/src/store.py` | DynamoDB + S3 data layer (and a local file store for offline testing) |
| `frontend/` | Static website (HTML/CSS/JS), hosted on S3 + CloudFront |
| `local/local_server.py` | Runs the same Lambda code on your laptop at http://localhost:8000 |
| `tests/test_api.py` | Unit tests for the API |
| `scripts/deploy.sh` / `destroy.sh` | One-command deploy and clean-up |
| `scripts/seed_demo.py` | Loads demo data |
| `docs/` | Deployment guide, team split, screenshots, project PDF |

## Quick start
Run locally (no AWS needed):

```bash
python3 local/local_server.py
```

Run the tests:

```bash
python3 -m unittest discover -s tests -v
```

Deploy to AWS (needs AWS CLI + SAM CLI configured – see `docs/DEPLOYMENT_GUIDE.md`):

```bash
./scripts/deploy.sh
```

Delete everything from AWS:

```bash
./scripts/destroy.sh
```

## API
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Health check |
| GET | `/expenses?month=YYYY-MM` | List a month's expenses |
| POST | `/expenses` | Create an expense |
| DELETE | `/expenses/{id}` | Delete an expense (and its receipt) |
| GET | `/summary?month=YYYY-MM` | Totals, by-category breakdown, budget usage |
| PUT | `/budget` | Set the monthly budget |
| POST | `/receipts/upload-url` | Get a presigned S3 upload URL |

All requests send the header `x-user-id: <workspace>`.

## Team
See `docs/TEAM_SPLIT.md`.
