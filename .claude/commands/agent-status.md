---
description: Check the live status of all 15 Omerion agents by querying Supabase telemetry. Shows last run time, status, pending HITL items, and cost for the last 24h.
---

You are checking the operational status of the Omerion agent fleet.

**Run these checks in sequence:**

1. **Check agent_runs** — query the last 24h from Supabase:
   ```sql
   SELECT agent_name, status, COUNT(*) as runs,
          SUM(cost_usd) as total_cost_usd,
          MAX(ended_at) as last_run,
          SUM(CASE WHEN status = 'failure' THEN 1 ELSE 0 END) as failures
   FROM agent_runs
   WHERE created_at > NOW() - INTERVAL '24 hours'
   GROUP BY agent_name, status
   ORDER BY agent_name;
   ```

2. **Check HITL queue** — query pending approvals:
   ```sql
   SELECT agent_name, COUNT(*) as pending
   FROM founder_review_queue
   WHERE status = 'pending'
   GROUP BY agent_name;
   ```

3. **Check recent errors:**
   ```sql
   SELECT source, message, occurred_at
   FROM error_log
   WHERE occurred_at > NOW() - INTERVAL '24 hours'
   ORDER BY occurred_at DESC
   LIMIT 10;
   ```

4. **Output a status table:**

| Agent | Last Run | Status | Runs (24h) | Cost (24h) | Pending HITL |
|-------|----------|--------|-----------|------------|--------------|
| SCOUT | ... | ... | ... | ... | ... |
| SCORE | ... | ... | ... | ... | ... |
| LEADS | ... | ... | ... | ... | ... |
| NURTURE | ... | ... | ... | ... | ... |
| REACH | ... | ... | ... | ... | ... |
| MATCH | ... | ... | ... | ... | ... |
| INTEL | ... | ... | ... | ... | ... |
| MAPPER | ... | ... | ... | ... | ... |
| ATTR | ... | ... | ... | ... | ... |
| BUILD | ... | ... | ... | ... | ... |
| SEEK | ... | ... | ... | ... | ... |
| R1 | ... | ... | ... | ... | ... |
| R2 | ... | ... | ... | ... | ... |
| R3 | ... | ... | ... | ... | ... |
| R4 | ... | ... | ... | ... | ... |

5. **Flag any agents** with:
   - No runs in 24h (potentially not triggered)
   - Failure rate > 20%
   - Pending HITL items older than 4h
   - Cost anomalies (any single run > $0.50)
