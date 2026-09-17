import os, json
from fastmcp import FastMCP

mcp = FastMCP("power-automate-mcp")

_BAP_RESOURCE = "https://service.powerapps.com/"
_org_url_cache = {}


def get_credential():
    # Reuses the same `az login` session already active on this host for the
    # Azure/azure_mcp_wrapper tools -- one signed-in identity, no separate
    # Power Platform credential to provision or rotate.
    from azure.identity import AzureCliCredential
    return AzureCliCredential()


def _bap_token():
    return get_credential().get_token(f"{_BAP_RESOURCE}.default").token


def _get_org_url(environment_id: str) -> str:
    """Resolves a Power Platform environment ID to its Dataverse Web API
    base URL (instanceApiUrl), via the BAP (Business App Platform) API."""
    if environment_id in _org_url_cache:
        return _org_url_cache[environment_id]
    import requests
    token = _bap_token()
    r = requests.get(
        f"https://api.bap.microsoft.com/providers/Microsoft.BusinessAppPlatform/environments/{environment_id}",
        headers={"Authorization": f"Bearer {token}"},
        params={"api-version": "2020-10-01", "$expand": "properties.linkedEnvironmentMetadata"},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    org_url = data.get("properties", {}).get("linkedEnvironmentMetadata", {}).get("instanceApiUrl")
    if not org_url:
        raise RuntimeError(f"Environment {environment_id} has no linked Dataverse instance (not a Dataverse-enabled environment).")
    _org_url_cache[environment_id] = org_url.rstrip("/")
    return _org_url_cache[environment_id]


def _dataverse_token(org_url: str) -> str:
    return get_credential().get_token(f"{org_url}/.default").token


def _dataverse_get(org_url: str, path: str, params: dict = None):
    import requests
    token = _dataverse_token(org_url)
    r = requests.get(
        f"{org_url}/api/data/v9.2/{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        params=params or {},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


@mcp.tool()
def powerplatform_list_environments() -> str:
    """List all Power Platform environments visible to the signed-in identity (name, ID, region, type)."""
    try:
        import requests
        token = _bap_token()
        r = requests.get(
            "https://api.bap.microsoft.com/providers/Microsoft.BusinessAppPlatform/environments",
            headers={"Authorization": f"Bearer {token}"},
            params={"api-version": "2020-10-01"},
            timeout=15,
        )
        r.raise_for_status()
        results = []
        for e in r.json().get("value", []):
            props = e.get("properties", {})
            results.append({
                "id": e.get("name"),
                "displayName": props.get("displayName"),
                "type": props.get("environmentSku"),
                "region": props.get("azureRegion") or props.get("location"),
                "isDefault": props.get("isDefault", False),
                "hasDataverse": bool(props.get("linkedEnvironmentMetadata")),
            })
        return json.dumps(results, indent=2)
    except Exception as e:
        return f"Error listing Power Platform environments: {str(e)}"


@mcp.tool()
def powerplatform_list_flows(environment_id: str, name_contains: str = None) -> str:
    """List cloud flows (Power Automate) in a given environment. environment_id is the
    environment's GUID/name as returned by powerplatform_list_environments. Flows are
    stored as Dataverse 'workflows' with category=5."""
    try:
        org_url = _get_org_url(environment_id)
        params = {
            "$select": "workflowid,name,statecode,statuscode,createdon,modifiedon,ismanaged",
            "$filter": "category eq 5",
        }
        if name_contains:
            params["$filter"] += f" and contains(name,'{name_contains}')"
        data = _dataverse_get(org_url, "workflows", params)
        results = []
        for w in data.get("value", []):
            results.append({
                "flow_id": w.get("workflowid"),
                "name": w.get("name"),
                "state": "On" if w.get("statecode") == 1 else "Off/Draft",
                "modified": w.get("modifiedon"),
                "managed": w.get("ismanaged"),
            })
        return json.dumps(results, indent=2)
    except Exception as e:
        return f"Error listing flows: {str(e)}"


@mcp.tool()
def powerplatform_get_flow(environment_id: str, flow_id: str) -> str:
    """Get full details (definition, connection references, state) for one cloud flow."""
    try:
        org_url = _get_org_url(environment_id)
        data = _dataverse_get(org_url, f"workflows({flow_id})")
        return json.dumps(data, indent=2)
    except Exception as e:
        return f"Error getting flow: {str(e)}"


@mcp.tool()
def powerplatform_set_flow_state(environment_id: str, flow_id: str, turn_on: bool) -> str:
    """Turn a cloud flow on (activate) or off (deactivate). Does not delete anything."""
    try:
        import requests
        org_url = _get_org_url(environment_id)
        token = _dataverse_token(org_url)
        statecode, statuscode = (1, 2) if turn_on else (0, 1)
        r = requests.patch(
            f"{org_url}/api/data/v9.2/workflows({flow_id})",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={"statecode": statecode, "statuscode": statuscode},
            timeout=20,
        )
        r.raise_for_status()
        return f"✓ Flow {flow_id} turned {'on' if turn_on else 'off'}."
    except Exception as e:
        return f"Error setting flow state: {str(e)}"


@mcp.tool()
def powerplatform_delete_flow(environment_id: str, flow_id: str) -> str:
    """Permanently delete a cloud flow. This cannot be undone -- only call this when the
    user's own request explicitly names this flow for deletion."""
    try:
        import requests
        org_url = _get_org_url(environment_id)
        token = _dataverse_token(org_url)
        r = requests.delete(
            f"{org_url}/api/data/v9.2/workflows({flow_id})",
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
        r.raise_for_status()
        return f"✓ Flow {flow_id} deleted."
    except Exception as e:
        return f"Error deleting flow: {str(e)}"


if __name__ == "__main__":
    mcp.run()
