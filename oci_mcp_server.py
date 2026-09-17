#!/usr/bin/env python3
import os
import sys
import json
import oci
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("oci-mcp", instructions="Oracle Cloud Infrastructure (OCI) MCP server for inspecting and managing Compute, Networking, Storage, and Limits.")

CONFIG_PATH = os.path.expanduser("~/.oci/config")
config = oci.config.from_file(CONFIG_PATH, "DEFAULT")

@mcp.tool()
def list_instances(compartment_id: str = "") -> str:
    """List OCI compute instances in the given compartment (defaults to root tenancy)."""
    comp_id = compartment_id or config["tenancy"]
    compute = oci.core.ComputeClient(config)
    instances = compute.list_instances(compartment_id=comp_id).data
    results = []
    for inst in instances:
        results.append({
            "id": inst.id,
            "display_name": inst.display_name,
            "shape": inst.shape,
            "lifecycle_state": inst.lifecycle_state,
            "availability_domain": inst.availability_domain,
            "time_created": str(inst.time_created),
            "region": config.get("region")
        })
    return json.dumps(results, indent=2)

@mcp.tool()
def get_instance(instance_id: str) -> str:
    """Get detailed information about an OCI compute instance."""
    compute = oci.core.ComputeClient(config)
    inst = compute.get_instance(instance_id).data
    return json.dumps(oci.util.to_dict(inst), indent=2, default=str)

@mcp.tool()
def instance_action(instance_id: str, action: str) -> str:
    """Perform an action on a compute instance (e.g. START, STOP, SOFTRESET, RESET, SOFTSTOP)."""
    compute = oci.core.ComputeClient(config)
    res = compute.instance_action(instance_id=instance_id, action=action.upper()).data
    return json.dumps(oci.util.to_dict(res), indent=2, default=str)

@mcp.tool()
def list_compartments() -> str:
    """List all accessible compartments in the OCI tenancy."""
    identity = oci.identity.IdentityClient(config)
    comps = identity.list_compartments(compartment_id=config["tenancy"], compartment_id_in_subtree=True).data
    results = [{"id": config["tenancy"], "name": "root_tenancy", "lifecycle_state": "ACTIVE"}]
    for c in comps:
        results.append({
            "id": c.id,
            "name": c.name,
            "description": c.description,
            "lifecycle_state": c.lifecycle_state
        })
    return json.dumps(results, indent=2)

@mcp.tool()
def list_buckets(compartment_id: str = "") -> str:
    """List Object Storage buckets in the compartment."""
    comp_id = compartment_id or config["tenancy"]
    obj_storage = oci.object_storage.ObjectStorageClient(config)
    namespace = obj_storage.get_namespace().data
    buckets = obj_storage.list_buckets(namespace_name=namespace, compartment_id=comp_id).data
    return json.dumps([{"name": b.name, "created": str(b.time_created)} for b in buckets], indent=2)

@mcp.tool()
def cost_by_service(days: int = 30) -> str:
    """Real OCI cost breakdown by service for the last N days (default 30), via the Usage API.
    Use this instead of just listing resources when the user wants to know what's driving
    their OCI bill -- note many OCI resources (Always Free shapes/tiers) genuinely cost $0."""
    try:
        from datetime import date, timedelta
        usage_client = oci.usage_api.UsageapiClient(config)
        tenant_id = config["tenancy"]
        end = date.today()
        start = end - timedelta(days=days)
        details = oci.usage_api.models.RequestSummarizedUsagesDetails(
            tenant_id=tenant_id,
            time_usage_started=f"{start.isoformat()}T00:00:00.000Z",
            time_usage_ended=f"{end.isoformat()}T00:00:00.000Z",
            granularity="MONTHLY",
            group_by=["service"],
        )
        resp = usage_client.request_summarized_usages(details).data
        items = resp.items or []
        results = [{"service": i.service, "cost": i.computed_amount, "currency": i.currency} for i in items]
        results.sort(key=lambda r: -(r["cost"] or 0))
        return json.dumps(results, indent=2)
    except Exception as e:
        return f"Error querying OCI cost: {str(e)}"

@mcp.tool()
def audit_search(compartment_id: str = "", days: int = 14, limit: int = 20) -> str:
    """Search OCI Audit Service events for root-cause investigation -- who did what action,
    from which identity/IP, and when. Useful for tracing an unexpected resource or cost back
    to the action that created it."""
    try:
        from datetime import datetime, timedelta, timezone
        comp_id = compartment_id or config["tenancy"]
        audit_client = oci.audit.AuditClient(config)
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        events = audit_client.list_events(compartment_id=comp_id, start_time=start, end_time=end).data
        results = []
        for e in events[:limit]:
            identity = e.identity
            results.append({
                "event_time": str(e.event_time),
                "event_name": e.event_name,
                "principal": getattr(identity, "principal_name", None) if identity else None,
                "ip_address": getattr(identity, "ip_address", None) if identity else None,
            })
        return json.dumps(results, indent=2) if results else "No matching audit events found in that window."
    except Exception as e:
        return f"Error searching OCI audit events: {str(e)}"

@mcp.tool()
def run_oci_script(code: str) -> str:
    """Execute Python code with full OCI SDK access. Pre-imported: `oci`, `config` (OCI config dict). Return or print output."""
    import io
    from contextlib import redirect_stdout, redirect_stderr
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    local_vars = {"oci": oci, "config": config}
    with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
        try:
            exec(code, local_vars)
        except Exception as e:
            stderr_buf.write(f"Exception: {e}\n")
    return json.dumps({
        "stdout": stdout_buf.getvalue(),
        "stderr": stderr_buf.getvalue()
    }, indent=2)

if __name__ == "__main__":
    mcp.run()
