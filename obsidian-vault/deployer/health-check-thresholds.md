# Deployer — Health Check Thresholds

Last updated: 2026-06-04
Maintained by: DEPLOY (deployer, Agent #18)

## Smoke Test Definition

A deployment is only marked `status = "live"` when ALL checks pass.

| Check | Endpoint | Pass condition | Timeout |
|-------|---------|---------------|---------|
| API health | GET /api/v1/health | HTTP 200, body `{"status":"ok"}` | 30s |
| Bot heartbeat | Internal sidecar GET /health | HTTP 200 | 15s |

## Retry Policy

```
Attempt 1: immediate
Attempt 2: +10s
Attempt 3: +10s (20s elapsed)
After 3 failures: trigger rollback
```

## Cold Start Window

Railway containers may take up to 60s on first request.
If `/api/v1/health` returns connection refused within the first 30s:
wait 30s then retry once before counting it as a failure.

## Deployment Status Lifecycle

```
pending → provisioning → smoke_testing → live
                      ↘ failed → rollback_attempted → rolled_back
                                                    ↘ rollback_failed (manual intervention)
```

## Post-Deploy Verification (optional, founder-initiated)

After DEPLOYER marks `status = "live"`:
1. Send a test Discord message to #mission-control
2. Trigger one manual agent run via the control plane API
3. Verify Langfuse traces appear within 2 minutes of the run start
