# datahub-mcp

Read-only MCP server for the AQVC Hub investor database (`api.datahub.aqvc.com`, ~35k records).
Tools: `search_investors`, `get_investor`, `count_investors`, `check_duplicates`.
**No write tools exist by design** — company rule: never push to the Hub.

## Configuration

| Env var | Required | Default | Purpose |
|---|---|---|---|
| `DATAHUB_API_TOKEN` | yes (in Docker) | — | Hub API token, mint at datahub.aqvc.com/settings/api-tokens |
| `PORT` | no | `8000` | HTTP listen port |
| `MCP_STATELESS` | no | `1` | `1` = stateless HTTP (safe behind load balancers); `0` reverts to in-memory sessions |

Endpoints: `POST /mcp` (streamable HTTP MCP), `GET /health` (unauthenticated liveness probe, returns 200).

The container exits immediately with a clear message if `DATAHUB_API_TOKEN` is missing — fail-fast on purpose.

## Run locally

```bash
# stdio (Claude Code / Claude Desktop)
uv run --with mcp python datahub_mcp.py

# Docker
docker build -t datahub-mcp .
docker run --rm -e DATAHUB_API_TOKEN=dh_... -p 8000:8000 datahub-mcp
curl http://localhost:8000/health
```

Connect from Claude Code: `claude mcp add --transport http datahub http://localhost:8000/mcp`
Connect from claude.ai: Settings → Connectors → Add custom connector → `https://<your-host>/mcp`.

## Deploy to AWS

Push the image to ECR, then run it on App Runner (simplest) or ECS Fargate.

### 1. Push to ECR

```bash
AWS_ACCOUNT=<account-id> AWS_REGION=<region>
aws ecr create-repository --repository-name datahub-mcp --region $AWS_REGION
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com
docker build -t datahub-mcp .
docker tag datahub-mcp:latest $AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/datahub-mcp:latest
docker push $AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/datahub-mcp:latest
```

### 2a. App Runner (recommended — gives you an HTTPS URL with zero infra)

Console: App Runner → Create service → Container registry → the ECR image above.
- Port: `8000`
- Env var: `DATAHUB_API_TOKEN` (store it in Secrets Manager and reference it, don't paste plaintext)
- Health check: HTTP, path `/health`
- Instance size: smallest (0.25 vCPU / 0.5 GB) is plenty — the server just proxies the Hub API

The service URL claude.ai needs is `https://<apprunner-url>/mcp`.

### 2b. ECS Fargate (if it must live in the team VPC)

Task definition essentials:
- Image: the ECR image; container port `8000`
- Secret: `DATAHUB_API_TOKEN` from Secrets Manager (`secrets`, not `environment`)
- Health check: `CMD-SHELL, python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4)"`
- Behind an ALB: target group port 8000, health-check path `/health`, HTTPS listener (claude.ai requires TLS). No sticky sessions needed — the server runs stateless.

### Scaling caveat

The Hub API allows **60 requests/min per token** and the rate limiter is per-process.
Run **one replica per token**; if you scale out, mint one token per replica or you'll trip 429s.

## Render (legacy)

Still deployed at `https://datahub-mcp-0nba.onrender.com/mcp` (`render.yaml`, service `srv-d95r0fmq1p3s73ddm6fg`, no auto-deploy — redeploy with `render deploys create <srv>`).
