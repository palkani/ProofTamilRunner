# ProofTamilRunner (Aksharamukha IME Service)

Clean-architecture FastAPI service providing transliteration for the Go IME adapter with strict security.

## Security model
- Requires headers on all endpoints (except `/health`):
  - `X-API-Key`
  - `X-Client-Id`
- API keys are HMAC-SHA256 hashed with `API_KEY_SECRET` and stored in-memory.
- No API keys are logged. Request IDs are propagated and logged.
- Per-client rate limiting (in-memory) with HTTP 429 on violation.

## Project structure
```
app/
  main.py
  api/
    routes.py
    schemas.py
  core/
    config.py
    security.py
    rate_limit.py
    logging.py
  middleware/
    auth.py
    request_id.py
    metrics.py
  services/
    transliteration.py
  adapters/
    aksharamukha.py
```

## API
- `GET /health` (no auth needed)
- `POST /transliterate`
  - Request: `{"text":"enathu","mode":"spoken","limit":8}`
  - Response: `{"success": true, "suggestions":[{"word":"எனது","ta":"எனது","score":1.0}]}`
  - Backward compatible with existing Go backend (suggestions array, word/ta/score).

## Local run
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export API_KEY_SECRET=change-me
export API_KEY=demo-key
export CLIENT_ID=demo-client
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

## Authenticated curl example
```bash
curl -X POST http://localhost:8088/transliterate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: demo-key" \
  -H "X-Client-Id: demo-client" \
  -d '{"text":"enathu","mode":"spoken","limit":8}'
```

## Cloud Run deploy (example)
```bash
gcloud run deploy proof-tamil-runner \
  --source . \
  --region asia-south1 \
  --allow-unauthenticated \
  --set-env-vars API_KEY_SECRET=change-me,API_KEY=demo-key,CLIENT_ID=demo-client
```

### GitHub Actions (auto-deploy)
This repo includes `.github/workflows/deploy.yml` which:
- Builds the Docker image with Cloud Build
- Deploys to Cloud Run

Required GitHub secrets:
- `GCP_PROJECT_ID`
- `GCP_REGION`
- `GCP_SERVICE` (e.g., proof-tamil-runner)
- `GCP_SA_KEY` (JSON key for a deploy service account)
- `API_KEY_SECRET`, `API_KEY`, `CLIENT_ID`
- Optional: `RATE_LIMIT_PER_MIN` (default 60), `MAX_TEXT_LEN` (default 64)

## Pricing / plan notes
- Free vs paid plans can be enforced by expanding the in-memory client registry and rate-limit policy.
- Rate limits are per-client_id; adjust `RATE_LIMIT_PER_MIN` env.

## GitHub Actions (auto-deploy)
This repo includes `.github/workflows/deploy.yml` which:
- Builds the Docker image with Cloud Build
- Deploys to Cloud Run

Required GitHub secrets:
- `GCP_PROJECT_ID`
- `GCP_REGION`
- `GCP_SERVICE` (e.g., proof-tamil-runner)
- `GCP_SA_KEY` (JSON key for a deploy service account)
- `API_KEY_SECRET`, `API_KEY`, `CLIENT_ID`
- Optional: `RATE_LIMIT_PER_MIN` (default 60), `MAX_TEXT_LEN` (default 64)

## Tests
```bash
pytest
```

