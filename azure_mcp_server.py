import os, json, sys
from fastmcp import FastMCP

mcp = FastMCP("azure-mcp")

def get_credential():
    from azure.identity import DefaultAzureCredential
    return DefaultAzureCredential()

def get_subscription_id():
    sub_id = os.environ.get("AZURE_SUBSCRIPTION_ID")
    if not sub_id:
        try:
            from azure.mgmt.subscription import SubscriptionClient
            client = SubscriptionClient(get_credential())
            subs = list(client.subscriptions.list())
            if subs:
                return subs[0].subscription_id
        except Exception:
            pass
    return sub_id or "00000000-0000-0000-0000-000000000000"

@mcp.tool()
def azure_list_subscriptions() -> str:
    """List all accessible Azure Subscriptions and Tenant IDs."""
    try:
        from azure.mgmt.subscription import SubscriptionClient
        client = SubscriptionClient(get_credential())
        subs = list(client.subscriptions.list())
        results = []
        for s in subs:
            results.append({
                "subscription_id": s.subscription_id,
                "display_name": s.display_name,
                "state": s.state,
                "tenant_id": getattr(s, "tenant_id", "N/A")
            })
        return json.dumps(results, indent=2)
    except Exception as e:
        return f"Error listing Azure subscriptions: {str(e)}"

@mcp.tool()
def azure_list_resource_groups(subscription_id: str = None) -> str:
    """List all Azure Resource Groups within a subscription."""
    try:
        from azure.mgmt.resource import ResourceManagementClient
        sub_id = subscription_id or get_subscription_id()
        client = ResourceManagementClient(get_credential(), sub_id)
        groups = list(client.resource_groups.list())
        results = [{"name": g.name, "location": g.location, "tags": g.tags} for g in groups]
        return json.dumps(results, indent=2)
    except Exception as e:
        return f"Error listing resource groups: {str(e)}"

@mcp.tool()
def azure_list_vms(resource_group: str = None, subscription_id: str = None) -> str:
    """List all Azure Virtual Machines (VMs), sizes, states, and locations."""
    try:
        from azure.mgmt.compute import ComputeManagementClient
        sub_id = subscription_id or get_subscription_id()
        client = ComputeManagementClient(get_credential(), sub_id)

        if resource_group:
            vms = list(client.virtual_machines.list(resource_group))
        else:
            vms = list(client.virtual_machines.list_all())

        results = []
        for vm in vms:
            results.append({
                "name": vm.name,
                "id": vm.id,
                "location": vm.location,
                "size": vm.hardware_profile.vm_size if vm.hardware_profile else "N/A",
                "os_type": vm.storage_profile.os_disk.os_type if vm.storage_profile and vm.storage_profile.os_disk else "N/A",
                "provisioning_state": vm.provisioning_state
            })
        return json.dumps(results, indent=2)
    except Exception as e:
        return f"Error listing Azure VMs: {str(e)}"

@mcp.tool()
def azure_vm_action(vm_name: str, resource_group: str, action: str, subscription_id: str = None) -> str:
    """Perform an action on an Azure VM: start, stop (powerOff), restart, or deallocate."""
    try:
        from azure.mgmt.compute import ComputeManagementClient
        sub_id = subscription_id or get_subscription_id()
        client = ComputeManagementClient(get_credential(), sub_id)

        act = action.lower()
        if act == "start":
            async_op = client.virtual_machines.begin_start(resource_group, vm_name)
        elif act in ["stop", "poweroff"]:
            async_op = client.virtual_machines.begin_power_off(resource_group, vm_name)
        elif act == "restart":
            async_op = client.virtual_machines.begin_restart(resource_group, vm_name)
        elif act == "deallocate":
            async_op = client.virtual_machines.begin_deallocate(resource_group, vm_name)
        else:
            return f"Unknown action: {action}. Supported: start, stop, restart, deallocate"

        return f"✓ Action '{action}' initiated on VM '{vm_name}' in resource group '{resource_group}'."
    except Exception as e:
        return f"Error executing VM action: {str(e)}"

@mcp.tool()
def azure_list_storage_accounts(resource_group: str = None, subscription_id: str = None) -> str:
    """List Azure Storage Accounts and Blob services."""
    try:
        from azure.mgmt.storage import StorageManagementClient
        sub_id = subscription_id or get_subscription_id()
        client = StorageManagementClient(get_credential(), sub_id)

        if resource_group:
            accs = list(client.storage_accounts.list_by_resource_group(resource_group))
        else:
            accs = list(client.storage_accounts.list())

        results = [{"name": a.name, "location": a.location, "sku": a.sku.name if a.sku else "N/A", "kind": a.kind} for a in accs]
        return json.dumps(results, indent=2)
    except Exception as e:
        return f"Error listing storage accounts: {str(e)}"

@mcp.tool()
def azure_cost_by_service(days: int = 30, subscription_id: str = None) -> str:
    """Real Azure cost breakdown by service + resource group for the last N days (default 30),
    via the Cost Management Query API. Use this instead of a single total whenever the user
    wants to know WHAT is driving their Azure bill, not just how much it is."""
    try:
        import requests
        from datetime import date, timedelta
        sub_id = subscription_id or get_subscription_id()
        end = date.today()
        start = end - timedelta(days=days)
        token = get_credential().get_token("https://management.azure.com/.default").token
        body = {
            "type": "ActualCost",
            "timeframe": "Custom",
            "timePeriod": {"from": start.isoformat(), "to": end.isoformat()},
            "dataset": {
                "granularity": "None",
                "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
                "grouping": [
                    {"type": "Dimension", "name": "ServiceName"},
                    {"type": "Dimension", "name": "ResourceGroupName"},
                ],
            },
        }
        r = requests.post(
            f"https://management.azure.com/subscriptions/{sub_id}/providers/Microsoft.CostManagement/query?api-version=2023-11-01",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        cols = [c["name"] for c in data["properties"]["columns"]]
        rows = data["properties"]["rows"]
        results = [dict(zip(cols, row)) for row in rows]
        results.sort(key=lambda row: -row.get("Cost", 0))
        return json.dumps(results, indent=2)
    except Exception as e:
        return f"Error querying Azure cost: {str(e)}"

@mcp.tool()
def azure_list_invoices(months_back: int = 2) -> str:
    """List recent Azure billing invoices (billed amount, tax, total, period, paid status) via
    the Billing API. Use this to find the exact charge behind a real card statement amount."""
    try:
        import requests
        from datetime import date, timedelta
        token = get_credential().get_token("https://management.azure.com/.default").token
        headers = {"Authorization": f"Bearer {token}"}
        r = requests.get(
            "https://management.azure.com/providers/Microsoft.Billing/billingAccounts?api-version=2024-04-01",
            headers=headers, timeout=20,
        )
        r.raise_for_status()
        accounts = r.json().get("value", [])
        if not accounts:
            return "No billing accounts accessible with this identity."
        acct_id = accounts[0]["id"]
        r2 = requests.get(
            f"https://management.azure.com{acct_id}/billingProfiles?api-version=2024-04-01",
            headers=headers, timeout=20,
        )
        r2.raise_for_status()
        profiles = r2.json().get("value", [])
        if not profiles:
            return "No billing profiles accessible with this identity."
        profile_id = profiles[0]["id"]
        end = date.today()
        start = end - timedelta(days=31 * months_back)
        r3 = requests.get(
            f"https://management.azure.com{profile_id}/invoices",
            headers=headers,
            params={
                "api-version": "2024-04-01",
                "periodStartDate": start.isoformat(),
                "periodEndDate": end.isoformat(),
            },
            timeout=20,
        )
        r3.raise_for_status()
        invoices = []
        for inv in r3.json().get("value", []):
            p = inv.get("properties", {})
            invoices.append({
                "invoice_id": inv.get("name"),
                "period_start": p.get("invoicePeriodStartDate"),
                "period_end": p.get("invoicePeriodEndDate"),
                "billed_amount": p.get("billedAmount"),
                "tax_amount": p.get("taxAmount"),
                "total_amount": p.get("totalAmount"),
                "status": p.get("status"),
            })
        return json.dumps(invoices, indent=2)
    except Exception as e:
        return f"Error listing Azure invoices: {str(e)}"

@mcp.tool()
def azure_run_script(python_code: str) -> str:
    """Execute arbitrary Python code with Azure SDK for custom cloud operations."""
    try:
        from io import StringIO
        import contextlib
        stdout = StringIO()
        locs = {"get_credential": get_credential, "get_subscription_id": get_subscription_id}
        with contextlib.redirect_stdout(stdout):
            exec(python_code, globals(), locs)
        out = stdout.getvalue()
        return out if out else "✓ Script executed successfully (no stdout)."
    except Exception as e:
        return f"Execution Error: {str(e)}"

if __name__ == "__main__":
    mcp.run()
