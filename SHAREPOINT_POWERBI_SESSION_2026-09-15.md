# SharePoint / Power BI Debugging Session — 2026-09-15

## Starting complaint
Asking the ai-orchestration chatbot ("m365") *"how many csv files i have on my sharepoint"* would spin for ~5 minutes and never return a real answer.

## Investigation

- Confirmed the Claude Code session's own `microsoft-365` MCP server had failed to connect — a red herring, unrelated to the actual bug. The real issue lives entirely inside this repo's own backend (`server.py`, running on `ai-orchestration-vm` via uvicorn on port 8000).
- SSH'd into `ai-orchestration-vm` (OCI, `ap-hyderabad-1`, public IP `129.225.111.42`, user `ubuntu`, key `ssh-key-2026-07-02.key`) to inspect the live process and logs.
- Ran live E2E tests by POSTing directly to `/api/execute` and polling `/api/status/{task_id}`, bypassing the UI, to see exactly what the backend does for a given prompt.

## Root causes found (three separate bugs, all on the SharePoint CSV path)

1. **Slow, sequential folder scan** — `powerbi_engine.list_all_csvs_recursive()` walked the SharePoint document library one folder at a time (up to 100 sequential Graph API calls), minting a fresh MSAL token on every single call, with no overall time budget.
2. **Wrong routing priority** — With `GEMINI_API_KEY` permanently revoked (per code comment, revoked/leaked 2026-09-10), the **Antigravity CLI agent (`agy`) is the actual primary path** for every prompt, run *before* the app's own keyword-routing fallback. `agy` was found to wander autonomously (e.g. calling generic `list_dir` on its own MCP config folder) for minutes without ever answering — this is the literal "going in circles" behavior.
3. **Silent failure, infinite spinner** — The VM's Python venv was missing the `pandas` package (though it's listed in `requirements.txt` — an environment drift issue, never installed). `import powerbi_engine` threw `ModuleNotFoundError` inside a FastAPI background task, which has no top-level exception handling — Starlette just logs and drops the exception, leaving the task stuck at `status: PROCESSING` forever. This is why it looked like an infinite hang rather than a fast, visible error.

## Fixes made (all committed to `main` and deployed live on the VM)

| Commit | Fix |
|---|---|
| `f470968` | Parallelized the SharePoint folder scan (thread pool, one HTTP call per BFS level instead of per folder), cached the MSAL Graph token until near expiry, capped the whole scan to a 40s wall-clock budget, cached scan results for 5 minutes. |
| `6fbe367` | Added a SharePoint+CSV branch to the keyword-routing fallback (`run_mission_pipeline`) so it calls the real Graph-backed tool instead of fabricating a fake "Autonomous directive processed successfully" success message. |
| `c8aedb3` | Promoted that branch into `try_instant_mission_match`, the zero-LLM fast-path that runs **before** the Antigravity CLI agent ever gets a turn — so this question is now answered directly from Microsoft Graph and never touches the flaky `agy` path at all. |
| `006f8df` | Added a top-level try/except around the whole pipeline (`run_pipeline` → `_run_pipeline_tiers`) so any future unhandled exception marks the task `COMPLETED` with a real error message instead of leaving it stuck at `PROCESSING` forever. |
| *(VM-only, not a commit)* | Installed the missing `pandas` package into the VM's venv. |

## Verified end-to-end

Final E2E test (`POST /api/execute` → poll `/api/status`) for *"how many csv files i have on my sharepoint"*:
- Completed in **4.9 seconds** (previously: ~5 minutes / never completed).
- Real answer, pulled live from Microsoft Graph:
  - `landmark/data/dim_products_hierarchy.csv` (3.0 KB)
  - `landmark/data/dim_promotions_master.csv` (0.4 KB)
  - `landmark/data/dim_stores.csv` (51,859.3 KB)
  - `landmark/data/fact_till_overrides_audit.csv` (169.9 KB)
  - `landmark/data/pos_transactions_fact.csv` (222,674.4 KB)

## Follow-up: "how many pbix files are there on my powerbi"

Same fake-success symptom on a different, unrelated question — traced to a **missing capability**, not a bug:

- There is no code anywhere in this repo that lists existing `.pbix` files. `powerbi_engine.py` only *generates new* `.pbip` projects from SharePoint CSVs (`generate_powerbi_dashboard`); nothing queries the Power BI Service for existing reports/datasets.
- Tested whether the existing Azure AD app (already used for SharePoint/Graph, app-only/service-principal auth) could call the Power BI REST API: **yes**, the tenant already allows service principals to use Power BI APIs, and a token was successfully acquired. But `GET /v1.0/myorg/groups` returned **zero workspaces** — because app-only auth can only see workspaces where the service principal has been explicitly added as a member, and it **can never see a personal "My Workspace"** (a hard Power BI platform limitation, not a bug).
- Confirmed with you: your `.pbix` files are in **My Workspace** (personal), not a shared workspace.

### What's needed to support this (not yet built)

Listing "My Workspace" reports requires **delegated auth** (you sign in yourself) instead of the app-only credentials used for SharePoint:

1. **Azure Portal change (you, not me):** on the existing app registration → Authentication → enable "Allow public client flows" (enables device-code login without needing a redirect URI).
2. **Azure Portal change (you, not me):** add a delegated Power BI permission (e.g. `Report.Read.All` delegated) and consent to it for your own account.
3. **Code (me, not yet done):** implement an MSAL device-code login flow — you run it once, get a short code, sign in via browser, and the resulting refresh token gets cached on the VM so future queries don't need you to log in again. Then add a `list_pbix_reports` tool calling `GET /v1.0/myorg/reports` with the delegated token.
4. **Security tradeoff to be aware of:** caching a delegated refresh token on the VM means anyone with access to that VM/file could act as you against Power BI until the token is revoked.

Alternative that avoids all of the above: move the reports into a shared/named Power BI workspace and add the existing service principal as a member — the app-only auth already in place would then work with no new code or Azure config.

**Decision pending from you:** build the device-code delegated-auth flow, or move reports to a shared workspace instead.
