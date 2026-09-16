"""
Natural-language -> SharePoint CSV -> Power BI Project (.pbip) generator.

Flow:
  1. Auth to Microsoft Graph with app-only client credentials (MS_TENANT_ID /
     MS_CLIENT_ID / MS_CLIENT_SECRET in .env).
  2. Resolve the SharePoint site + drive, locate the requested CSV file(s).
  3. Download and profile each CSV with pandas (column types, candidate keys).
  4. Auto-detect relationships across tables by matching column names +
     uniqueness, auto-build a Date dimension if any date-like columns exist,
     and auto-generate DAX measures (sums/averages + time intelligence) and
     drill-down hierarchies (Date, and common geo/category column groups).
  5. Emit a Power BI Project (.pbip) — TMDL semantic model + a minimal valid
     PBIR report — zipped, ready to open directly in Power BI Desktop.

The report canvas is intentionally left with a single starter page (a table
of the model's fields) rather than hand-authored charts: PBIR visual JSON is
the most fragile part of a .pbip to generate by hand, and a corrupt visual
can make the whole report fail to open. Everything that matters for a
drill-down dashboard — the data model, relationships, DAX measures, and
hierarchies — is fully built; wiring those onto visuals in Desktop is a
drag-and-drop, not a modeling task.
"""
import os
import io
import re
import csv
import json
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Dict, Any, Tuple

import httpx
try:
    import pandas as pd
except ImportError:
    pd = None

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# App-only tokens are valid ~60-90 min; cache and reuse instead of minting a
# fresh one on every single Graph call.
_token_cache: Dict[str, Any] = {"token": None, "expires_at": 0.0}

# Short-lived cache of full folder scans, keyed by (site_query, folder_path),
# so repeat "how many CSVs" questions don't re-walk the whole document
# library every time.
_csv_scan_cache: Dict[Tuple[str, str], Tuple[float, dict]] = {}
_CSV_SCAN_CACHE_TTL = 300.0  # seconds


class PowerBIEngineError(Exception):
    pass


# --------------------------------------------------------------------------
# Microsoft Graph (app-only)
# --------------------------------------------------------------------------

def _graph_token() -> str:
    now = time.monotonic()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    tenant = os.environ.get("MS_TENANT_ID")
    client_id = os.environ.get("MS_CLIENT_ID")
    client_secret = os.environ.get("MS_CLIENT_SECRET")
    if not (tenant and client_id and client_secret):
        raise PowerBIEngineError(
            "Microsoft Graph credentials not configured. Set MS_TENANT_ID, MS_CLIENT_ID and "
            "MS_CLIENT_SECRET in .env (Azure Portal > App registrations > your app, with "
            "Sites.Read.All application permission + admin consent granted)."
        )
    try:
        import msal
    except ImportError:
        raise PowerBIEngineError("msal is not installed. Run `pip install msal`.")
    app = msal.ConfidentialClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant}",
        client_credential=client_secret,
    )
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in result:
        raise PowerBIEngineError(
            f"Graph auth failed: {result.get('error_description') or result.get('error') or result}"
        )
    token = result["access_token"]
    # Refresh a bit early (60s slack) rather than racing token expiry mid-scan.
    _token_cache["token"] = token
    _token_cache["expires_at"] = now + max(int(result.get("expires_in", 3600)) - 60, 60)
    return token


def _graph_get(url: str, token: str, params: Optional[dict] = None, timeout: float = 10.0) -> dict:
    r = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=timeout)
    if r.status_code >= 400:
        raise PowerBIEngineError(f"Graph API error {r.status_code} calling {url}: {r.text[:300]}")
    return r.json()


def resolve_site(token: str, site_query: str) -> dict:
    """site_query can be empty (tenant root site), a search term, a site name,
    or a full SharePoint URL."""
    site_query = (site_query or "").strip()
    if not site_query:
        return _graph_get(f"{GRAPH_BASE}/sites/root", token)
    if site_query.startswith("http"):
        # https://tenant.sharepoint.com/sites/SiteName -> hostname + /sites/SiteName
        m = re.match(r"https://([^/]+)(/.*)?", site_query)
        if m:
            hostname, path = m.group(1), (m.group(2) or "").rstrip("/")
            try:
                return _graph_get(f"{GRAPH_BASE}/sites/{hostname}:{path}", token)
            except PowerBIEngineError:
                pass  # fall through to search
    data = _graph_get(f"{GRAPH_BASE}/sites", token, params={"search": site_query})
    values = data.get("value", [])
    if not values:
        raise PowerBIEngineError(f"No SharePoint site found matching '{site_query}'.")
    return values[0]


def list_drive_csvs(token: str, site_id: str, folder_path: Optional[str] = None) -> Tuple[str, List[dict]]:
    drive = _graph_get(f"{GRAPH_BASE}/sites/{site_id}/drive", token)
    drive_id = drive["id"]
    folder_path = (folder_path or "").strip("/")
    url = (
        f"{GRAPH_BASE}/drives/{drive_id}/root:/{folder_path}:/children"
        if folder_path
        else f"{GRAPH_BASE}/drives/{drive_id}/root/children"
    )
    data = _graph_get(url, token)
    items = data.get("value", [])
    csvs = [i for i in items if "file" in i and i.get("name", "").lower().endswith(".csv")]
    return drive_id, csvs


def _list_children(token: str, drive_id: str, folder_path: str) -> List[dict]:
    url = (
        f"{GRAPH_BASE}/drives/{drive_id}/root:/{folder_path}:/children"
        if folder_path
        else f"{GRAPH_BASE}/drives/{drive_id}/root/children"
    )
    try:
        return _graph_get(url, token).get("value", [])
    except PowerBIEngineError:
        return []


def list_all_csvs_recursive(token: str, drive_id: str, folder_path: Optional[str] = None,
                             max_items: int = 300, max_folders: int = 100,
                             time_budget_seconds: float = 40.0,
                             max_workers: int = 8) -> Tuple[List[dict], bool]:
    """Walk the folder tree under folder_path collecting every .csv file,
    returning its name, full path, and size. Each BFS level is fetched
    concurrently (instead of one folder at a time) and the whole scan is
    bounded by a wall-clock budget so a large/slow document library degrades
    to a partial-but-fast result instead of hanging for minutes.

    Returns (files, truncated) where truncated is True if the scan stopped
    early due to the item/folder/time caps.
    """
    start = (folder_path or "").strip("/")
    current_level = [start]
    visited = set()
    results: List[dict] = []
    folders_scanned = 0
    truncated = False
    deadline = time.monotonic() + time_budget_seconds

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        while current_level:
            if time.monotonic() >= deadline or folders_scanned >= max_folders or len(results) >= max_items:
                truncated = bool(current_level)
                break

            to_fetch = [f for f in current_level if f not in visited]
            for f in to_fetch:
                visited.add(f)
            folders_scanned += len(to_fetch)

            next_level: List[str] = []
            futures = {pool.submit(_list_children, token, drive_id, f): f for f in to_fetch}
            for fut in as_completed(futures):
                current = futures[fut]
                for item in fut.result():
                    item_path = f"{current}/{item['name']}" if current else item["name"]
                    if "folder" in item:
                        next_level.append(item_path)
                    elif item.get("name", "").lower().endswith(".csv"):
                        results.append({"name": item["name"], "path": item_path, "size": item.get("size", 0)})
            current_level = next_level

    return results[:max_items], truncated


def list_sharepoint_csvs(site_query: str, folder_path: Optional[str] = None, on_log=None) -> dict:
    def log(msg):
        if on_log:
            on_log(msg)

    cache_key = (site_query or "", folder_path or "")
    cached = _csv_scan_cache.get(cache_key)
    if cached and time.monotonic() < cached[0]:
        log("⚡ Using cached SharePoint scan (< 5 min old)...")
        return cached[1]

    token = _graph_token()
    log(f"🔎 Resolving SharePoint site '{site_query or '(tenant root)'}'...")
    site = resolve_site(token, site_query)
    drive = _graph_get(f"{GRAPH_BASE}/sites/{site['id']}/drive", token)
    drive_id = drive["id"]
    log("📂 Scanning document library for CSV files...")
    files, truncated = list_all_csvs_recursive(token, drive_id, folder_path)
    result = {
        "site_name": site.get("displayName") or site.get("name") or site_query,
        "site_url": site.get("webUrl", ""),
        "files": files,
        "truncated": truncated,
    }
    _csv_scan_cache[cache_key] = (time.monotonic() + _CSV_SCAN_CACHE_TTL, result)
    return result



# --------------------------------------------------------------------------
# Power BI Reports & PBIX Audit
# --------------------------------------------------------------------------

_pbi_token_cache: Dict[str, Any] = {"token": None, "expires_at": 0.0}

def _powerbi_service_token() -> Optional[str]:
    now = time.monotonic()
    if _pbi_token_cache["token"] and now < _pbi_token_cache["expires_at"]:
        return _pbi_token_cache["token"]

    tenant = os.environ.get("MS_TENANT_ID")
    client_id = os.environ.get("MS_CLIENT_ID")
    client_secret = os.environ.get("MS_CLIENT_SECRET")
    if not (tenant and client_id and client_secret):
        return None
    try:
        import msal
        app = msal.ConfidentialClientApplication(
            client_id,
            authority=f"https://login.microsoftonline.com/{tenant}",
            client_credential=client_secret,
        )
        res = app.acquire_token_for_client(scopes=["https://analysis.windows.net/powerbi/api/.default"])
        token = res.get("access_token")
        if token:
            _pbi_token_cache["token"] = token
            _pbi_token_cache["expires_at"] = now + max(int(res.get("expires_in", 3600)) - 60, 60)
        return token
    except Exception:
        return None


_pbi_summary_cache: Dict[str, Any] = {"expires_at": 0.0, "result": ""}
_PBI_SUMMARY_CACHE_TTL = 120.0  # 2 minutes


def _fetch_pbi_endpoint(url: str, token: str, timeout: float = 2.5) -> list:
    try:
        r = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=timeout)
        if r.status_code == 200:
            return r.json().get("value", [])
    except Exception:
        pass
    return []


def list_powerbi_service_reports() -> List[dict]:
    token = _powerbi_service_token()
    if not token:
        return []

    reports = []
    seen_ids = set()

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_my = pool.submit(_fetch_pbi_endpoint, "https://api.powerbi.com/v1.0/myorg/reports", token, 3.0)
        f_groups = pool.submit(_fetch_pbi_endpoint, "https://api.powerbi.com/v1.0/myorg/groups", token, 4.0)
        f_apps = pool.submit(_fetch_pbi_endpoint, "https://api.powerbi.com/v1.0/myorg/apps", token, 4.0)

        # 1. My Workspace
        for item in f_my.result():
            rid = item.get("id")
            if rid and rid not in seen_ids:
                seen_ids.add(rid)
                reports.append({
                    "name": item.get("name", "Untitled Report"),
                    "id": rid,
                    "workspace": "My Workspace (Personal)",
                    "webUrl": item.get("webUrl", f"https://app.powerbi.com/groups/me/reports/{rid}"),
                    "datasetId": item.get("datasetId"),
                    "reportType": item.get("reportType", "PowerBIReport"),
                    "source": "Power BI Service (Cloud)"
                })

        # 2. Shared Workspaces
        groups = f_groups.result()
        if groups:
            group_futs = {
                pool.submit(_fetch_pbi_endpoint, f"https://api.powerbi.com/v1.0/myorg/groups/{g['id']}/reports", token, 4.0): g.get("name", "Workspace")
                for g in groups if "id" in g
            }
            for fut, gname in group_futs.items():
                for item in fut.result():
                    rid = item.get("id")
                    if rid and rid not in seen_ids:
                        seen_ids.add(rid)
                        reports.append({
                            "name": item.get("name", "Untitled Report"),
                            "id": rid,
                            "workspace": gname,
                            "webUrl": item.get("webUrl", f"https://app.powerbi.com/groups/{item.get('datasetWorkspaceId', '')}/reports/{rid}"),
                            "datasetId": item.get("datasetId"),
                            "reportType": item.get("reportType", "PowerBIReport"),
                            "source": "Power BI Service (Workspace)"
                        })

        # 3. Apps
        apps = f_apps.result()
        if apps:
            app_futs = {
                pool.submit(_fetch_pbi_endpoint, f"https://api.powerbi.com/v1.0/myorg/apps/{a['id']}/reports", token, 4.0): (a.get("name", "Power BI App"), a.get("id"))
                for a in apps if "id" in a
            }
            for fut, (app_name, app_id) in app_futs.items():
                for item in fut.result():
                    rid = item.get("id")
                    if rid and rid not in seen_ids:
                        seen_ids.add(rid)
                        reports.append({
                            "name": item.get("name", "Untitled Report"),
                            "id": rid,
                            "workspace": f"App: {app_name}",
                            "webUrl": item.get("webUrl", f"https://app.powerbi.com/apps/{app_id}/reports/{rid}"),
                            "datasetId": item.get("datasetId"),
                            "reportType": item.get("reportType", "PowerBIReport"),
                            "source": "Power BI App"
                        })

    return reports


def list_sharepoint_powerbi_files() -> List[dict]:
    found = []
    seen_ids = set()
    try:
        token = _graph_token()
    except Exception:
        return []

    def _search_drive_pbix(url: str, source_label: str, site_name: str) -> List[dict]:
        res = []
        try:
            r = _graph_get(url, token, timeout=5.0)
            for item in r.get("value", []):
                iid = item.get("id")
                fname = item.get("name", "")
                if iid and fname.lower().endswith((".pbix", ".pbip", ".pbit")):
                    res.append({
                        "id": iid,
                        "name": fname,
                        "path": f"SharePoint [{site_name}] / {fname}",
                        "size_kb": round((item.get("size") or 0) / 1024, 1),
                        "modified": item.get("lastModifiedDateTime", "")[:16].replace("T", " "),
                        "type": "SharePoint .pbix Report",
                        "webUrl": item.get("webUrl", ""),
                        "source": source_label
                    })
        except Exception:
            pass
        return res

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_root = pool.submit(_search_drive_pbix, f"{GRAPH_BASE}/sites/root/drive/root/search(q='.pbix')", "SharePoint (Root Site)", "Root")
        
        # Also check Landmark site
        def _search_landmark():
            res = []
            try:
                sites_data = _graph_get(f"{GRAPH_BASE}/sites", token, params={"search": "landmark"}, timeout=5.0)
                for s in sites_data.get("value", []):
                    sid = s.get("id")
                    sname = s.get("displayName") or s.get("name") or "Landmark"
                    res.extend(_search_drive_pbix(f"{GRAPH_BASE}/sites/{sid}/drive/root/search(q='.pbix')", f"SharePoint ({sname})", sname))
            except Exception:
                pass
            return res

        f_landmark = pool.submit(_search_landmark)

        for item in f_root.result() + f_landmark.result():
            if item["id"] not in seen_ids:
                seen_ids.add(item["id"])
                found.append(item)

    return found


def list_local_and_onedrive_powerbi_files() -> List[dict]:
    repo_dir = os.path.dirname(os.path.abspath(__file__))
    search_dirs = [
        "/mnt/c/Users/keysh/OneDrive/Documents/powerbi",
        "/mnt/c/Users/keysh/Documents/powerbi",
        "/mnt/c/Users/keysh/Documents/landmark/powerbi",
        "/mnt/c/Users/keysh/OneDrive/Documents",
        "/mnt/c/Users/keysh/OneDrive/Desktop",
        "/mnt/c/Users/keysh/Desktop",
        "/mnt/c/Users/keysh/Downloads",
        r"C:\Users\keysh\OneDrive\Documents\powerbi",
        r"C:\Users\keysh\Documents\powerbi",
        r"C:\Users\keysh\Documents\landmark\powerbi",
        r"C:\Users\keysh\OneDrive\Documents",
        r"C:\Users\keysh\Desktop",
        r"C:\Users\keysh\Downloads",
        os.path.join(repo_dir, "generated_dashboards"),
        "/home/ubuntu/powerbi",
        "/home/ubuntu/ai-orchestration/generated_dashboards",
    ]

    found = []
    seen_paths = set()

    for base in search_dirs:
        if not os.path.exists(base):
            continue
        try:
            for root, dirs, files in os.walk(base):
                rel = os.path.relpath(root, base)
                if rel != "." and rel.count(os.sep) >= 2:
                    dirs.clear()
                for f in files:
                    lower = f.lower()
                    if lower.endswith((".pbix", ".pbip", ".pbit")):
                        full = os.path.join(root, f)
                        norm = os.path.normcase(os.path.abspath(full))
                        if norm not in seen_paths:
                            seen_paths.add(norm)
                            sz = round(os.path.getsize(full) / 1024, 1)
                            mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(full)))
                            ftype = "Power BI Desktop (.pbix)" if lower.endswith(".pbix") else ("Power BI Project (.pbip)" if lower.endswith(".pbip") else "Power BI Template (.pbit)")
                            found.append({
                                "name": f,
                                "path": full,
                                "size_kb": sz,
                                "modified": mtime,
                                "type": ftype,
                                "source": "Local / OneDrive"
                            })
        except Exception:
            pass

    return found


def list_powerbi_reports_summary() -> str:
    now = time.monotonic()
    if _pbi_summary_cache["result"] and now < _pbi_summary_cache["expires_at"]:
        return _pbi_summary_cache["result"]

    cloud_reports = []
    sharepoint_files = []
    local_files = []

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_cloud = pool.submit(list_powerbi_service_reports)
        f_sp = pool.submit(list_sharepoint_powerbi_files)
        f_loc = pool.submit(list_local_and_onedrive_powerbi_files)

        cloud_reports = f_cloud.result()
        sharepoint_files = f_sp.result()
        local_files = f_loc.result()

    total_count = len(cloud_reports) + len(sharepoint_files) + len(local_files)

    lines = []
    if total_count > 0:
        lines.append(f"📊 **Power BI Reports & PBIX Files Found ({total_count} total):**")
        lines.append("")
    else:
        lines.append("📊 **Power BI Audit Result:** No `.pbix` files or Power BI Service reports were found in indexed locations.")
        lines.append("")

    if local_files:
        lines.append("### 📁 Local & OneDrive Synced Power BI Files")
        for f in local_files:
            lines.append(f"• **`{f['name']}`** ({f['size_kb']} KB) — `{f['path']}` *(Last modified: {f['modified']})*")
        lines.append("")

    if sharepoint_files:
        lines.append("### 📂 SharePoint Document Libraries")
        for f in sharepoint_files:
            url_part = f"[{f['name']}]({f['webUrl']})" if f.get("webUrl") else f"`{f['name']}`"
            lines.append(f"• **{url_part}** ({f['size_kb']} KB) — *{f['source']}*")
        lines.append("")

    if cloud_reports:
        lines.append("### ☁️ Power BI Service (Workspaces & Apps)")
        for r in cloud_reports:
            lines.append(f"• **[{r['name']}]({r['webUrl']})** — *{r['workspace']}* (`{r['reportType']}`)")
        lines.append("")

    lines.append(f"✅ **Audit Summary:** Found **{total_count}** Power BI report(s) / file(s) across Power BI Service, SharePoint, and Local/OneDrive storage.")
    if not cloud_reports:
        lines.append("")
        lines.append("> ℹ️ **Note on Power BI Service (Cloud):** Personal *My Workspace* reports require delegated user login or moving reports to a shared workspace where the backend app (`Azure Service Principal`) is added as a workspace member.")

    res = "\n".join(lines)
    _pbi_summary_cache["result"] = res
    _pbi_summary_cache["expires_at"] = now + _PBI_SUMMARY_CACHE_TTL
    return res


def download_csv_bytes(token: str, drive_id: str, item_id: str) -> bytes:
    r = httpx.get(
        f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/content",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
        follow_redirects=True,
    )
    if r.status_code >= 400:
        raise PowerBIEngineError(f"Failed to download CSV (HTTP {r.status_code}).")
    return r.content


# --------------------------------------------------------------------------
# Schema profiling
# --------------------------------------------------------------------------

def _sanitize_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9 _]+", " ", str(name)).strip()
    return re.sub(r"\s+", " ", name) or "Column"


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def load_table(filename: str, raw: bytes) -> Tuple[str, pd.DataFrame]:
    table_name = _sanitize_name(re.sub(r"\.csv$", "", filename, flags=re.IGNORECASE))
    df = pd.read_csv(io.BytesIO(raw))
    df.columns = [_sanitize_name(c) for c in df.columns]
    return table_name, df


def _infer_dax_type(series: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_integer_dtype(series):
        return "int64"
    if pd.api.types.is_float_dtype(series):
        return "double"
    non_null = series.dropna()
    if len(non_null) == 0:
        return "string"
    try:
        parsed = pd.to_datetime(non_null, errors="coerce")
        if parsed.notna().mean() > 0.85:
            return "dateTime"
    except Exception:
        pass
    return "string"


_ID_SUFFIXES = {"id", "key", "code", "no", "num"}


def _split_words(name: str) -> List[str]:
    """Splits on non-alphanumerics, then on camelCase boundaries, so both
    'Customer ID' and 'CustomerID' tokenize to ['Customer', 'ID']."""
    words = []
    for part in re.split(r"[^A-Za-z0-9]+", name):
        if not part:
            continue
        words.extend(re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+", part))
    return words


def _is_id_like(name: str) -> bool:
    words = _split_words(name)
    return bool(words) and words[-1].lower() in _ID_SUFFIXES


def profile_tables(tables: Dict[str, pd.DataFrame]) -> Dict[str, dict]:
    """Returns {table: {column: {"dtype": ..., "is_numeric": bool, "is_date": bool,
    "is_id_like": bool, "unique": bool}}}"""
    profile = {}
    for tname, df in tables.items():
        cols = {}
        for c in df.columns:
            dtype = _infer_dax_type(df[c])
            is_numeric = dtype in ("int64", "double")
            is_id_like = _is_id_like(c)
            cols[c] = {
                "dtype": dtype,
                "is_numeric": is_numeric,
                "is_date": dtype == "dateTime",
                "is_id_like": is_id_like,
                "unique": df[c].nunique(dropna=True) == len(df[c].dropna()) and len(df[c].dropna()) > 0,
            }
        profile[tname] = cols
    return profile


# --------------------------------------------------------------------------
# Relationship + hierarchy + measure detection
# --------------------------------------------------------------------------

def detect_relationships(tables: Dict[str, pd.DataFrame], profile: Dict[str, dict]) -> List[dict]:
    rels = []
    names = list(tables.keys())
    seen_pairs = set()
    for i, t1 in enumerate(names):
        for t2 in names[i + 1:]:
            for c1 in tables[t1].columns:
                for c2 in tables[t2].columns:
                    if _norm(c1) != _norm(c2) or not _norm(c1):
                        continue
                    u1 = profile[t1][c1]["unique"]
                    u2 = profile[t2][c2]["unique"]
                    if u1 == u2:
                        continue  # ambiguous cardinality, skip rather than guess wrong
                    one_table, one_col = (t1, c1) if u1 else (t2, c2)
                    many_table, many_col = (t2, c2) if u1 else (t1, c1)
                    key = (many_table, many_col, one_table, one_col)
                    if key in seen_pairs:
                        continue
                    seen_pairs.add(key)
                    rels.append({
                        "from_table": many_table, "from_col": many_col,
                        "to_table": one_table, "to_col": one_col,
                        "cardinality": "manyToOne",
                    })
    return rels


_GEO_HIERARCHY = ["Country", "Region", "State", "City"]
_CAT_HIERARCHY = ["Category", "Subcategory", "Sub Category", "Product"]


def detect_hierarchies(tables: Dict[str, pd.DataFrame]) -> Dict[str, List[str]]:
    """Returns {table: [ordered column names]} for any table containing >=2
    columns from a known drill-down pattern, in pattern order."""
    hierarchies = {}
    for tname, df in tables.items():
        cols_norm = {_norm(c): c for c in df.columns}
        for pattern, label in ((_GEO_HIERARCHY, "Geography"), (_CAT_HIERARCHY, "Category")):
            matched = [cols_norm[_norm(p)] for p in pattern if _norm(p) in cols_norm]
            if len(matched) >= 2:
                hierarchies[f"{tname}::{label}"] = matched
    return hierarchies


def build_date_dimension(tables: Dict[str, pd.DataFrame], profile: Dict[str, dict]) -> Optional[dict]:
    """If any date-like column exists across the tables, build a synthetic
    Date dimension spanning the observed min/max date, plus relationships
    linking each fact table's date column to it."""
    date_cols = []  # (table, col)
    all_dates = []
    for tname, cols in profile.items():
        for c, meta in cols.items():
            if meta["is_date"]:
                date_cols.append((tname, c))
                parsed = pd.to_datetime(tables[tname][c], errors="coerce").dropna()
                if len(parsed):
                    all_dates.append(parsed)
    if not date_cols or not all_dates:
        return None
    combined = pd.concat(all_dates)
    start, end = combined.min().normalize(), combined.max().normalize()
    dates = pd.date_range(start, end, freq="D")
    date_df = pd.DataFrame({
        "Date": dates,
        "Year": dates.year,
        "Quarter": "Q" + dates.quarter.astype(str),
        "Month": dates.month,
        "MonthName": dates.strftime("%B"),
        "Day": dates.day,
    })
    return {"table": "Date", "df": date_df, "links": date_cols}


def build_measures(tname: str, df: pd.DataFrame, cols: dict, has_date: bool) -> List[dict]:
    measures = []
    for c, meta in cols.items():
        if not meta["is_numeric"] or meta["is_id_like"]:
            continue
        safe = c.replace("'", "")
        total_name = f"Total {safe}"
        measures.append({"name": total_name, "expr": f"SUM('{tname}'[{c}])"})
        measures.append({"name": f"Avg {safe}", "expr": f"AVERAGE('{tname}'[{c}])"})
        if has_date:
            measures.append({
                "name": f"{total_name} YTD",
                "expr": f"TOTALYTD([{total_name}], 'Date'[Date])",
            })
            measures.append({
                "name": f"{total_name} PY",
                "expr": f"CALCULATE([{total_name}], SAMEPERIODLASTYEAR('Date'[Date]))",
            })
            measures.append({
                "name": f"{total_name} YoY %",
                "expr": f"DIVIDE([{total_name}] - [{total_name} PY], [{total_name} PY])",
            })
    return measures


# --------------------------------------------------------------------------
# TMDL generation
# --------------------------------------------------------------------------

def _tmdl_column_block(col: str, dtype: str, source_col: str = None) -> str:
    source_col = source_col or col
    lines = [f"\tcolumn '{col}'", f"\t\tdataType: {dtype}", "\t\tsummarizeBy: none", f"\t\tsourceColumn: {source_col}"]
    if dtype in ("int64", "double"):
        lines[2] = "\t\tsummarizeBy: sum"
    lines.append("\n\t\tannotation SummarizationSetBy = Automatic")
    return "\n".join(lines) + "\n"


def _m_query_for_sharepoint(site_url: str, folder_path: str, filename: str) -> str:
    folder = folder_path.strip("/")
    return (
        "partition '{name}' = m\n"
        "\tmode: import\n"
        "\tsource =\n"
        "\t\tlet\n"
        f"\t\t\tSource = SharePoint.Files(\"{site_url}\", [ApiVersion = 15]),\n"
        f"\t\t\tTarget = Source{{[Name=\"{filename}\", Folder Path=\"{site_url}/{folder}\"]}}[Content],\n"
        "\t\t\tCsv = Csv.Document(Target, [Delimiter=\",\", Encoding=65001, QuoteStyle=QuoteStyle.None]),\n"
        "\t\t\tPromoted = Table.PromoteHeaders(Csv, [PromoteAllScalars=true])\n"
        "\t\tin\n"
        "\t\t\tPromoted\n"
    )


def _m_query_for_date_table() -> str:
    return (
        "partition 'Date' = m\n"
        "\tmode: import\n"
        "\tsource =\n"
        "\t\tlet\n"
        "\t\t\tSource = Table.FromRecords(DateRecords)\n"
        "\t\tin\n"
        "\t\t\tSource\n"
    )


def generate_table_tmdl(tname: str, df: pd.DataFrame, cols: dict, measures: List[dict],
                         hierarchies: List[List[str]], site_url: str, folder_path: str, filename: str) -> str:
    parts = [f"table '{tname}'\n"]
    for c in df.columns:
        parts.append(_tmdl_column_block(c, cols[c]["dtype"]))
    for h in hierarchies:
        h_name = " > ".join(h)
        parts.append(f"\thierarchy '{h_name}'\n")
        for level in h:
            parts.append(f"\t\tlevel '{level}'\n\t\t\tcolumn: {level}\n")
    for m in measures:
        parts.append(f"\tmeasure '{m['name']}' = {m['expr']}\n\t\tformatString: #,0.00\n")
    parts.append("\n\t" + _m_query_for_sharepoint(site_url, folder_path, filename).replace("{name}", tname))
    return "\n".join(parts)


def generate_date_table_tmdl(date_df: pd.DataFrame) -> str:
    parts = ["table 'Date'\n"]
    dtype_map = {"Date": "dateTime", "Year": "int64", "Quarter": "string", "Month": "int64",
                 "MonthName": "string", "Day": "int64"}
    for c in date_df.columns:
        parts.append(_tmdl_column_block(c, dtype_map.get(c, "string")))
    parts.append("\thierarchy 'Year > Quarter > Month > Day'\n")
    for level in ("Year", "Quarter", "MonthName", "Day"):
        parts.append(f"\t\tlevel '{level}'\n\t\t\tcolumn: {level}\n")
    parts.append(
        "\n\tpartition 'Date' = m\n\t\tmode: import\n\t\tsource =\n\t\t\tlet\n"
        f"\t\t\t\tSource = List.Dates(#date({date_df['Year'].min()},1,1), "
        f"{len(date_df)}, #duration(1,0,0,0)),\n"
        "\t\t\t\tToTable = Table.FromList(Source, Splitter.SplitByNothing(), {\"Date\"}),\n"
        "\t\t\t\tTyped = Table.TransformColumnTypes(ToTable, {{\"Date\", type date}}),\n"
        "\t\t\t\tAddYear = Table.AddColumn(Typed, \"Year\", each Date.Year([Date]), Int64.Type),\n"
        "\t\t\t\tAddQuarter = Table.AddColumn(AddYear, \"Quarter\", each \"Q\" & Text.From(Date.QuarterOfYear([Date])), type text),\n"
        "\t\t\t\tAddMonth = Table.AddColumn(AddQuarter, \"Month\", each Date.Month([Date]), Int64.Type),\n"
        "\t\t\t\tAddMonthName = Table.AddColumn(AddMonth, \"MonthName\", each Date.MonthName([Date]), type text),\n"
        "\t\t\t\tAddDay = Table.AddColumn(AddMonthName, \"Day\", each Date.Day([Date]), Int64.Type)\n"
        "\t\t\tin\n\t\t\t\tAddDay\n"
    )
    parts.append("\n\tannotation __PBI_TimeIntelligenceEnabled = 1")
    return "\n".join(parts)


def generate_relationships_tmdl(rels: List[dict]) -> str:
    lines = []
    for r in rels:
        rel_id = str(uuid.uuid4())
        lines.append(
            f"relationship {rel_id}\n"
            f"\tfromColumn: {r['from_table']}.{r['from_col']}\n"
            f"\ttoColumn: {r['to_table']}.{r['to_col']}\n"
        )
    return "\n".join(lines)


def generate_database_tmdl(model_name: str) -> str:
    return f"database '{model_name}'\n\tcompatibilityLevel: 1567\n"


def generate_model_tmdl(table_names: List[str]) -> str:
    lines = [
        "model Model",
        "\tculture: en-US",
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        "\tsourceQueryCulture: en-US",
        "",
    ]
    for t in table_names:
        lines.append(f"ref table '{t}'")
    lines.append("ref cultureInfo 'en-US'")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# PBIR (report) — minimal, valid starter page
# --------------------------------------------------------------------------

def generate_report_files(project_name: str, table_names: List[str]) -> Dict[str, str]:
    page_id = "ReportSection1"
    visual_id = "StarterTable"
    files = {}

    files[".platform"] = json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "Report", "displayName": project_name},
        "config": {"version": "2.0", "logicalId": str(uuid.uuid4())},
    }, indent=2)

    files["definition.pbir"] = json.dumps({
        "version": "4.0",
        "datasetReference": {"byPath": {"path": f"../{project_name}.SemanticModel"}},
    }, indent=2)

    files["definition/report.json"] = json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/1.2.0/schema.json",
        "themeCollection": {"baseTheme": {"name": "CY24SU10"}},
        "layoutOptimization": "None",
    }, indent=2)

    files["definition/pages/pages.json"] = json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pages/1.0.0/schema.json",
        "pageOrder": [page_id],
        "activePageName": page_id,
    }, indent=2)

    files[f"definition/pages/{page_id}/page.json"] = json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/1.4.0/schema.json",
        "name": page_id,
        "displayName": "Overview",
        "height": 720,
        "width": 1280,
    }, indent=2)

    fact_table = table_names[0] if table_names else "Table1"
    files[f"definition/pages/{page_id}/visuals/{visual_id}/visual.json"] = json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.3.0/schema.json",
        "name": visual_id,
        "position": {"x": 40, "y": 40, "z": 0, "width": 1200, "height": 600},
        "visual": {
            "visualType": "tableEx",
            "query": {
                "queryState": {
                    "Values": {
                        "projections": [
                            {"field": {"Column": {"Expression": {"SourceRef": {"Entity": fact_table}}, "Property": c}},
                             "queryRef": f"{fact_table}.{c}"}
                            for c in []  # left empty: user drags fields on open; avoids referencing columns we can't verify exist post-load
                        ]
                    }
                }
            },
        },
    }, indent=2)

    return files


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def generate_pbip_project(
    site_query: str,
    folder_path: str,
    filenames: Optional[List[str]],
    project_name: str,
    output_dir: str,
    on_log=None,
) -> dict:
    def log(msg):
        if on_log:
            on_log(msg)

    project_name = _sanitize_name(project_name or "Dashboard").replace(" ", "")

    log(f"🔐 Authenticating to Microsoft Graph (app-only)...")
    token = _graph_token()

    log(f"🔎 Resolving SharePoint site '{site_query}'...")
    site = resolve_site(token, site_query)
    site_url = site.get("webUrl", "").rstrip("/")

    log(f"📂 Listing CSV files in '{folder_path or '/'}'...")
    drive_id, csv_items = list_drive_csvs(token, site["id"], folder_path)
    if filenames:
        wanted = {f.strip().lower() for f in filenames}
        csv_items = [i for i in csv_items if i["name"].lower() in wanted]
    if not csv_items:
        raise PowerBIEngineError(
            f"No matching CSV files found in '{site.get('displayName', site_query)}' / '{folder_path or '/'}'."
        )

    tables: Dict[str, pd.DataFrame] = {}
    source_files: Dict[str, str] = {}
    for item in csv_items:
        log(f"⬇️ Downloading '{item['name']}'...")
        raw = download_csv_bytes(token, drive_id, item["id"])
        tname, df = load_table(item["name"], raw)
        tables[tname] = df
        source_files[tname] = item["name"]

    log("🧠 Profiling schemas and detecting relationships...")
    profile = profile_tables(tables)
    rels = detect_relationships(tables, profile)
    hierarchies = detect_hierarchies(tables)
    date_dim = build_date_dimension(tables, profile)

    if date_dim:
        for tname, dcol in date_dim["links"]:
            rels.append({
                "from_table": tname, "from_col": dcol,
                "to_table": "Date", "to_col": "Date",
                "cardinality": "manyToOne",
            })

    log("📐 Generating DAX measures...")
    table_measures = {}
    for tname, df in tables.items():
        table_measures[tname] = build_measures(tname, df, profile[tname], has_date=bool(date_dim))

    log("🏗️ Writing TMDL semantic model + PBIR report files...")
    project_root = os.path.join(output_dir, project_name)
    dataset_dir = os.path.join(project_root, f"{project_name}.SemanticModel")
    report_dir = os.path.join(project_root, f"{project_name}.Report")
    os.makedirs(os.path.join(dataset_dir, "definition", "tables"), exist_ok=True)
    os.makedirs(report_dir, exist_ok=True)

    with open(os.path.join(project_root, f"{project_name}.pbip"), "w", encoding="utf-8") as f:
        json.dump({
            "version": "1.0",
            "artifacts": [{"report": {"path": f"{project_name}.Report"}}],
            "settings": {"enableAutoRecovery": True},
        }, f, indent=2)

    with open(os.path.join(dataset_dir, "definition.pbism"), "w", encoding="utf-8") as f:
        json.dump({"version": "4.2", "settings": {}}, f, indent=2)
    with open(os.path.join(dataset_dir, ".platform"), "w", encoding="utf-8") as f:
        json.dump({
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
            "metadata": {"type": "SemanticModel", "displayName": project_name},
            "config": {"version": "2.0", "logicalId": str(uuid.uuid4())},
        }, f, indent=2)

    with open(os.path.join(dataset_dir, "definition", "database.tmdl"), "w", encoding="utf-8") as f:
        f.write(generate_database_tmdl(project_name))

    all_table_names = list(tables.keys()) + (["Date"] if date_dim else [])
    with open(os.path.join(dataset_dir, "definition", "model.tmdl"), "w", encoding="utf-8") as f:
        f.write(generate_model_tmdl(all_table_names))

    with open(os.path.join(dataset_dir, "definition", "relationships.tmdl"), "w", encoding="utf-8") as f:
        f.write(generate_relationships_tmdl(rels))

    for tname, df in tables.items():
        table_hierarchies = [cols for key, cols in hierarchies.items() if key.startswith(f"{tname}::")]
        content = generate_table_tmdl(
            tname, df, profile[tname], table_measures[tname], table_hierarchies,
            site_url, folder_path, source_files[tname],
        )
        with open(os.path.join(dataset_dir, "definition", "tables", f"{tname}.tmdl"), "w", encoding="utf-8") as f:
            f.write(content)

    if date_dim:
        with open(os.path.join(dataset_dir, "definition", "tables", "Date.tmdl"), "w", encoding="utf-8") as f:
            f.write(generate_date_table_tmdl(date_dim["df"]))

    report_files = generate_report_files(project_name, list(tables.keys()))
    for rel_path, content in report_files.items():
        full_path = os.path.join(report_dir, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)

    log("🗜️ Zipping .pbip project...")
    zip_path = os.path.join(output_dir, f"{project_name}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(project_root):
            for fname in files:
                full = os.path.join(root, fname)
                arcname = os.path.relpath(full, output_dir)
                zf.write(full, arcname)

    return {
        "project_name": project_name,
        "zip_path": zip_path,
        "tables": list(tables.keys()),
        "relationships": rels,
        "hierarchies": hierarchies,
        "date_dimension": bool(date_dim),
        "measures": {t: [m["name"] for m in ms] for t, ms in table_measures.items()},
        "site_name": site.get("displayName", site_query),
    }
