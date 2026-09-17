import os, json, sys
from fastmcp import FastMCP

mcp = FastMCP("gcp-mcp")

def get_credentials():
    import google.auth
    credentials, project = google.auth.default()
    return credentials, project

def get_project_id(explicit_project=None):
    if explicit_project:
        return explicit_project
    env_p = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT")
    if env_p:
        return env_p
    try:
        _, p = get_credentials()
        if p:
            return p
    except Exception:
        pass
    # ADC often has no associated project (ADC login without --project), and this
    # environment's gcloud CLI config has no project set either (confirmed: only
    # an account, no `project =` line in ~/.config/gcloud/configurations/config_default,
    # and shelling out to `gcloud config get-value project` hangs rather than
    # failing fast). A placeholder string here would just 403 silently, so fail
    # loudly instead and let the caller pick a real project from gcp_list_projects.
    raise ValueError(
        "No GCP project could be determined (no explicit project_id, no "
        "GOOGLE_CLOUD_PROJECT/GCP_PROJECT env var, no ADC project). Call "
        "gcp_list_projects first and pass one of its project_id values explicitly."
    )

@mcp.tool()
def gcp_list_projects() -> str:
    """List accessible Google Cloud Platform (GCP) Projects."""
    try:
        from google.cloud import resourcemanager_v3
        credentials, _ = get_credentials()
        client = resourcemanager_v3.ProjectsClient(credentials=credentials)
        # v3's ListProjects requires an explicit parent (org/folder); SearchProjects
        # is the v3 replacement for "list every project I can see", no parent needed.
        request = resourcemanager_v3.SearchProjectsRequest()
        page_result = client.search_projects(request=request)
        projects = []
        for p in page_result:
            projects.append({
                "project_id": p.project_id,
                "display_name": p.display_name,
                "state": p.state.name
            })
        return json.dumps(projects, indent=2)
    except Exception as e:
        return f"GCP Projects Status: {str(e)}"

@mcp.tool()
def gcp_list_instances(project_id: str = None) -> str:
    """List all Google Compute Engine VM instances across all zones."""
    try:
        from google.cloud import compute_v1
        credentials, auto_proj = get_credentials()
        proj = project_id or get_project_id(auto_proj)
        client = compute_v1.InstancesClient(credentials=credentials)
        request = compute_v1.AggregatedListInstancesRequest(project=proj)
        agg_list = client.aggregated_list(request=request)

        results = []
        for zone, response in agg_list:
            if response.instances:
                z_name = zone.split("/")[-1]
                for inst in response.instances:
                    ext_ip = "N/A"
                    if inst.network_interfaces:
                        for ac in inst.network_interfaces[0].access_configs:
                            if ac.nat_i_p:
                                ext_ip = ac.nat_i_p
                    results.append({
                        "name": inst.name,
                        "zone": z_name,
                        "machine_type": inst.machine_type.split("/")[-1],
                        "status": inst.status,
                        "external_ip": ext_ip
                    })
        return json.dumps(results, indent=2)
    except Exception as e:
        return f"GCP Compute Status: {str(e)}"

@mcp.tool()
def gcp_instance_action(instance_name: str, zone: str, action: str, project_id: str = None) -> str:
    """Perform action on a GCP VM: start, stop, reset, or delete."""
    try:
        from google.cloud import compute_v1
        credentials, auto_proj = get_credentials()
        proj = project_id or get_project_id(auto_proj)
        client = compute_v1.InstancesClient(credentials=credentials)

        act = action.lower()
        if act == "start":
            client.start(project=proj, zone=zone, instance=instance_name)
        elif act == "stop":
            client.stop(project=proj, zone=zone, instance=instance_name)
        elif act == "reset":
            client.reset(project=proj, zone=zone, instance=instance_name)
        elif act == "delete":
            client.delete(project=proj, zone=zone, instance=instance_name)
        else:
            return f"Unknown action: {action}. Supported: start, stop, reset, delete"
        return f"✓ Action '{action}' initiated for GCP instance '{instance_name}' in zone '{zone}'."
    except Exception as e:
        return f"Error executing GCP action: {str(e)}"

@mcp.tool()
def gcp_list_buckets(project_id: str = None) -> str:
    """List Google Cloud Storage (GCS) buckets."""
    try:
        from google.cloud import storage
        credentials, auto_proj = get_credentials()
        proj = project_id or get_project_id(auto_proj)
        client = storage.Client(project=proj, credentials=credentials)
        buckets = list(client.list_buckets())
        results = [{"name": b.name, "location": b.location, "storage_class": b.storage_class} for b in buckets]
        return json.dumps(results, indent=2)
    except Exception as e:
        return f"GCS Storage Status: {str(e)}"

@mcp.tool()
def gcp_create_always_free_vm(instance_name: str, project_id: str = None, zone: str = "us-central1-a") -> str:
    """Create an Always-Free e2-micro Google Compute Engine VM instance."""
    try:
        from google.cloud import compute_v1
        credentials, auto_proj = get_credentials()
        proj = project_id or get_project_id(auto_proj)
        client = compute_v1.InstancesClient(credentials=credentials)

        instance = compute_v1.Instance()
        instance.name = instance_name
        instance.machine_type = f"zones/{zone}/machineTypes/e2-micro"

        # OS Disk: Ubuntu 24.04 LTS (30GB Always-Free persistent disk)
        disk = compute_v1.AttachedDisk()
        disk.boot = True
        disk.auto_delete = True
        init_params = compute_v1.AttachedDiskInitializeParams()
        init_params.source_image = "projects/ubuntu-os-cloud/global/images/family/ubuntu-2404-lts-amd64"
        init_params.disk_size_gb = 30
        init_params.disk_type = f"zones/{zone}/diskTypes/pd-standard"
        disk.initialize_params = init_params
        instance.disks = [disk]

        # Network Interface with External Public IP
        network_interface = compute_v1.NetworkInterface()
        network_interface.network = "global/networks/default"
        access_config = compute_v1.AccessConfig()
        access_config.type_ = "ONE_TO_ONE_NAT"
        access_config.name = "External NAT"
        network_interface.access_configs = [access_config]
        instance.network_interfaces = [network_interface]

        operation = client.insert(project=proj, zone=zone, instance_resource=instance)
        return f"✓ Always-Free e2-micro VM '{instance_name}' creation initiated in '{zone}' (Operation ID: {operation.name})."
    except Exception as e:
        return f"Error creating GCP VM: {str(e)}"

@mcp.tool()
def gcp_cost_by_service(days: int = 30, project_id: str = None, billing_table: str = None) -> str:
    """Real GCP cost breakdown by service for the last N days, via the BigQuery billing export.
    Requires BigQuery billing export to already be configured for the billing account (Console >
    Billing > Billing export > BigQuery export). Pass billing_table as 'project.dataset.table'
    if the GCP_BILLING_BQ_TABLE environment variable isn't set."""
    try:
        from google.cloud import bigquery
        from datetime import date, timedelta
        credentials, auto_proj = get_credentials()
        proj = project_id or get_project_id(auto_proj)
        table = billing_table or os.environ.get("GCP_BILLING_BQ_TABLE")
        if not table:
            return ("GCP BigQuery billing export not configured for this call. Pass "
                    "billing_table='project.dataset.table' (find it in Console > Billing > "
                    "Billing export > BigQuery export), or set GCP_BILLING_BQ_TABLE.")
        client = bigquery.Client(project=proj, credentials=credentials)
        start = date.today() - timedelta(days=days)
        query = f"""
            SELECT service.description AS service, SUM(cost) AS cost, ANY_VALUE(currency) AS currency
            FROM `{table}`
            WHERE usage_start_time >= TIMESTAMP(@start_date) AND project.id = @project_id
            GROUP BY service
            ORDER BY cost DESC
        """
        job_config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("start_date", "DATE", start.isoformat()),
            bigquery.ScalarQueryParameter("project_id", "STRING", proj),
        ])
        job = client.query(query, job_config=job_config)
        results = [{"service": row.service, "cost": row.cost, "currency": row.currency} for row in job.result()]
        return json.dumps(results, indent=2)
    except Exception as e:
        return f"Error querying GCP cost: {str(e)}"

@mcp.tool()
def gcp_audit_log_lookup(filter_str: str, days: int = 14, project_id: str = None, limit: int = 20) -> str:
    """Search GCP Cloud Audit Logs for root-cause investigation (who did what, when). filter_str
    is a Cloud Logging filter expression, e.g.
    'protoPayload.methodName="google.api.serviceusage.v1.ServiceUsage.EnableService" AND protoPayload.request.serviceName="networkmanagement.googleapis.com"'.
    Returns timestamp, calling principal, and caller IP for each matching entry."""
    try:
        from google.cloud import logging as gcp_logging
        from datetime import datetime, timedelta, timezone
        credentials, auto_proj = get_credentials()
        proj = project_id or get_project_id(auto_proj)
        client = gcp_logging.Client(project=proj, credentials=credentials)
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        full_filter = f'timestamp >= "{since}" AND ({filter_str})'
        entries = client.list_entries(filter_=full_filter, order_by=gcp_logging.ASCENDING, page_size=limit)
        results = []
        for entry in entries:
            payload = entry.payload if isinstance(entry.payload, dict) else {}
            results.append({
                "timestamp": entry.timestamp.isoformat() if entry.timestamp else None,
                "method": payload.get("methodName"),
                "principal": (payload.get("authenticationInfo") or {}).get("principalEmail"),
                "caller_ip": (payload.get("requestMetadata") or {}).get("callerIp"),
                "resource_name": payload.get("resourceName"),
            })
            if len(results) >= limit:
                break
        return json.dumps(results, indent=2) if results else "No matching audit log entries found in that window."
    except Exception as e:
        return f"Error querying GCP audit logs: {str(e)}"

@mcp.tool()
def gcp_run_script(python_code: str) -> str:
    """Execute arbitrary Python script with Google Cloud SDK."""
    try:
        from io import StringIO
        import contextlib
        stdout = StringIO()
        locs = {"get_credentials": get_credentials, "get_project_id": get_project_id}
        with contextlib.redirect_stdout(stdout):
            exec(python_code, globals(), locs)
        out = stdout.getvalue()
        return out if out else "✓ Script executed successfully."
    except Exception as e:
        return f"Execution Error: {str(e)}"

if __name__ == "__main__":
    mcp.run()
