"""KPI metric writer — shared helper for all agents.

Every agent that wants ATTR to measure its impact must call write_kpi_metric()
after performing an action. ATTR reads from agent_telemetry.metrics JSONB,
so without this data the pre/post delta computation returns 0.0 for all KPIs.

Usage example (in CRM Nurture, after sending an email):
    from omerion_core.telemetry.kpi import write_kpi_metric
    write_kpi_metric("crm_nurture", client_id, "speed_to_lead_minutes", minutes_elapsed)
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger

log = get_logger("omerion.telemetry.kpi")

# Canonical KPI names keyed by the persona that owns them.
# Sourced from agents.yaml personas[*].kpis — duplicated here for IDE discovery.
KNOWN_KPIS: dict[str, list[str]] = {
    "ops_leader":                    ["process_cycle_time_days", "automation_adoption_rate", "manual_task_reduction_pct"],
    "revenue_leader":                ["speed_to_lead_minutes", "pipeline_conversion_rate", "revenue_per_rep"],
    "sme_founder":                   ["owner_hours_saved_weekly", "revenue_growth_rate", "customer_acquisition_cost"],
    "agency_owner":                  ["project_margin_pct", "client_retention_rate", "deliverable_cycle_days"],
    "ecommerce_operator":            ["cart_recovery_rate", "avg_order_value", "return_rate_pct"],
    "professional_services_owner":   ["billable_hours_saved", "client_onboarding_days", "matter_throughput"],
    "saas_founder":                  ["churn_rate", "activation_rate", "support_ticket_resolution_hours"],
    "hr_talent_leader":              ["time_to_hire_days", "offer_acceptance_rate", "retention_rate"],
    "finance_ops":                   ["close_cycle_days", "reconciliation_hours_saved", "report_turnaround_hours"],
}


def write_kpi_metric(
    agent_name: str,
    client_id: UUID | str | None,
    kpi_name: str,
    value: float,
    *,
    persona: str | None = None,
    metadata: dict | None = None,
) -> bool:
    """Write a single KPI measurement to agent_telemetry.

    Args:
        agent_name:  The writing agent (e.g. "crm_nurture", "linkedin_outreach").
        client_id:   The client this measurement belongs to (may be None for global metrics).
        kpi_name:    The KPI identifier string (must match personas[*].kpis in agents.yaml).
        value:       The numeric value of the measurement.
        persona:     Optional persona tag for downstream filtering.
        metadata:    Optional additional context stored alongside the metric.

    Returns True on successful write, False on error (never raises).
    """
    row = {
        "agent_name": agent_name,
        "status": "success",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "metrics": {kpi_name: value},
        "client_id": str(client_id) if client_id else None,
        "persona": persona,
        "metadata": metadata or {},
    }
    try:
        supabase.table("agent_telemetry").insert(row).execute()
        log.info("kpi_metric_written", agent=agent_name, kpi=kpi_name, value=value, client_id=str(client_id) if client_id else None)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("kpi_metric_write_failed", agent=agent_name, kpi=kpi_name, error=str(exc))
        return False
