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
import uuid
import zipfile
from typing import Optional, List, Dict, Any, Tuple

import httpx
import pandas as pd

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class PowerBIEngineError(Exception):
    pass


# --------------------------------------------------------------------------
# Microsoft Graph (app-only)
# --------------------------------------------------------------------------

def _graph_token() -> str:
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
    return result["access_token"]


def _graph_get(url: str, token: str, params: Optional[dict] = None) -> dict:
    r = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=20.0)
    if r.status_code >= 400:
        raise PowerBIEngineError(f"Graph API error {r.status_code} calling {url}: {r.text[:300]}")
    return r.json()


def resolve_site(token: str, site_query: str) -> dict:
    """site_query can be a search term, a site name, or a full SharePoint URL."""
    site_query = (site_query or "").strip()
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
