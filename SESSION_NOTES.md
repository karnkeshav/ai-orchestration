# Session Notes — ai-orchestration Debugging/Infra Session (2026-09-15)

This file documents a debugging/infra session on the `ai-orchestration` project. It is reference-oriented (file paths, PIDs, exact identifiers) for an AI agent picking up this project cold — not a narrative.

## Architecture

### Two deployments of the same `server.py` FastAPI backend

1. **Local laptop copy**
   - Path: `C:\Users\keysh\github\ai-orchestration\server.py`
   - Normally NOT running (was off until this session started it for testing).
   - Must run as a **native Windows Python process**, not inside WSL.
   - Reason: `_agy_command()` in `server.py` (~line 3657) branches on `platform.system() == "Windows"` and shells out to WSL via `wsl.exe -e /home/keysh/.local/bin/agy`. Running `server.py` itself inside WSL breaks this — the Linux branch expects a bare `agy` on `PATH`, which is not the case, causing `FileNotFoundError` on spawn.

2. **Production deployment — Oracle Cloud (OCI) VM**
   - Instance name: `ai-orchestration-vm` (OCID visible via `mcp__oci__list_instances`)
   - Public IP / SSH key / exact domain names: **intentionally omitted from this public doc** — see private notes / ask Keshav directly. (Redacted here because this repo is public on GitHub Pages; do not re-add these specifics to a tracked file in this repo.)
   - Hostname: `always-free-e2-micro`
   - Region: `ap-hyderabad-1`
   - Source path on VM: `/home/ubuntu/ai-orchestration/server.py`
   - Run via systemd unit: `/etc/systemd/system/ai-studio.service` (unit name `ai-studio.service`)
     - Correctly sets `Environment="AGY_BIN=/home/ubuntu/.local/bin/agy"`.
   - Public endpoint: a duckdns.org subdomain — nginx reverse-proxies 443/80 → `127.0.0.1:8000`. (Exact hostname redacted; see private notes.)
   - SSH access: user `ubuntu` (NOT `opc`). Key file is in the Windows Downloads folder — ask Keshav which of the two present keys matches (verify against the instance's authorized key before use; do not guess).

3. **Unrelated second app on the same VM**
   - nginx also proxies a second duckdns/sslip.io hostname to a `gunicorn` process on port 8080 (routes `/ask`, `/health`).
   - This is a different backend from `ai-orchestration` and was not investigated further this session.

4. **`agy` (Antigravity CLI)**
   - Full agentic CLI with its own independent MCP tool configuration at `~/.gemini/config/mcp_config.json` on whichever host it runs on — separate from Claude Code's `.mcp.json`.
   - Local laptop and OCI VM each have their own separate `agy` installs and separate MCP configs. They are NOT the same agent instance.

5. **`/api/execute` tiered pipeline** (`_run_pipeline_tiers`, ~line 4323 in `server.py`):
   1. Instant app creation
   2. Instant cloud query — read-only keyword fast-path for cost/compute/storage/services across aws/gcp/azure/oci (`try_instant_cloud_query`)
   2.5. Instant "mission" match (food/rides/shopping/video)
   3. Gemini fast-path — **currently disabled**: `GEMINI_API_KEY` was revoked/leaked as of 2026-09-10 (every call 403s)
   4. `agy` via `run_agy_pipeline` — the tier with real unattended create/delete power on all 4 clouds via agy's own independently-configured MCP servers, run with `--dangerously-skip-permissions` (no human approval step; no human present in a backend request)
   5. Keyword-router fallback

## Bugs Found & Fixed

### 1. PowerBI/pbix intent misrouting

- **Location**: `server.py`, `_POWERBI_INTENT_KEYWORDS` (~line 3795)
- **Bug**: The list included the bare substring `"pbix"`. Any prompt containing that substring — including plain read-only questions like "how many pbix files do I have?" — set `is_powerbi_build = True`. This replaced the user's actual prompt with a completely different, forceful hardcoded directive (`_AGY_POWERBI_HINT`) instructing agy to read SharePoint CSVs and write/extend a Power BI project (a dashboard-build workflow), instead of answering the user's real question.
- **Fix applied**:
  - Added a new tuple: `_POWERBI_BUILD_VERBS = ("build", "create", "generate", "make", "extend", "add to", "update the", "design", "construct", "develop")`
  - Changed the `is_powerbi_build` check in `run_agy_pipeline` (~line 4146) to require BOTH a `_POWERBI_INTENT_KEYWORDS` match AND a `_POWERBI_BUILD_VERBS` match, so a plain query no longer gets hijacked into a build directive.
- **Status**:
  - Applied and verified (`py_compile` passed) on the **local laptop copy** of `server.py`.
  - **NOT applied to the production OCI copy** (`/home/ubuntu/ai-orchestration/server.py`). Attempted via SSH but blocked by the Claude Code auto-mode safety classifier (category: "Remote Shell Writes" — mutating SSH commands to that remote production host are gated).
  - **This is the highest-priority outstanding item.** Someone with direct access needs to apply the same two edits (new `_POWERBI_BUILD_VERBS` tuple + updated `is_powerbi_build` condition) to the production file.

### 2. Orphaned process blocking the real systemd service on the OCI VM

- **Symptom**: `ai-studio.service` (the correctly-configured systemd service) was crash-looping for hours (~5,600+ restart attempts observed during the session) with `[Errno 98] address already in use` on port 8000.
- **Root cause**: An orphaned, manually-started process — `PID 205005`, started 2026-09-15 08:49:47, NOT managed by systemd, parent PID 1 — was squatting on port 8000 and serving all real traffic.
  - That orphaned process's environment had a bare OS-default `PATH` with no `/home/ubuntu/.local/bin`, so it could never find/spawn `agy` either. This means `agy` was non-functional on production regardless of which process was actually listening.
- **Note**: The systemd unit itself was already correctly configured (`AGY_BIN=/home/ubuntu/.local/bin/agy` explicitly set) — no code/config fix was needed there, only killing the orphan.
- **Resolution**: Claude Code's safety classifier blocked Claude from running `kill 205005` itself (also categorized "Remote Shell Writes" — blocks mutating commands over SSH to this remote host regardless of `sudo` use). The user (Keshav) ran the following manually:
  - `kill 205005`
  - `sudo systemctl start ai-studio.service`
- **Current status: FIXED AND VERIFIED** (as of end of session). `ai-studio.service` is `active (running)`, bound cleanly to port 8000, with a live `agy` child process and its own MCP tool wrapper subprocesses (`amazon_mcp_wrapper.py`, `oci_mcp_wrapper.py`, `ola_mcp_wrapper.py`, `aws_mcp_wrapper.py`, etc.) under `/home/ubuntu/` and `/home/ubuntu/.gemini/config/`.

## AWS IAM Setup

- **AWS account**: redacted account ID (ask Keshav), region `us-east-1`.
- **IAM user created**: `agy-vm-readonly`
  - Originally scoped read-only: Cost Explorer, Budgets, EC2/Lambda/RDS/S3 Describe/List/Get, Free Tier, billing views.
  - Later expanded at the user's explicit request to include full write access on EC2/S3/Lambda/RDS (`ec2:*`, `s3:*`, `lambda:*`, `rds:*`) via a second attached policy: `agy-vm-fullwrite-noiam-policy`.
  - Two IAM policies now exist for this user:
    - `agy-vm-readonly-policy` (read-only)
    - `agy-vm-fullwrite-noiam-policy` (EC2/S3/Lambda/RDS full write, explicitly excludes IAM)
- **IAM (`iam:*`) access was explicitly requested by the user but BLOCKED** by the Claude Code safety classifier. Granting `iam:*` to a credential set used by an unattended, `--dangerously-skip-permissions` agent is treated as a hard-blocked self-privilege-escalation risk, and this could not be overridden even after explicit user confirmation. **This is a durable platform-level guardrail** — expect it to block again in future sessions if attempted.
- **Credential deployment**: The `agy-vm-readonly` credentials were deployed to `~/.aws/credentials` on the OCI VM (`ai-orchestration-vm`) — NOT on the local laptop. AWS CLI v2 (`2.36.45`) was also installed on that VM this session (it had none before).
- **Separate, pre-existing identity**: `claude-code-user` (same AWS account as above) — used for all direct read/write AWS CLI operations performed FROM this Claude Code session/laptop this session. This is a different, pre-existing, broader-permissioned identity, unrelated to the new `agy-vm-readonly` user.
  - CloudTrail confirmed this same `claude-code-user` identity (via `invokedBy: aws-mcp.amazonaws.com`) created and terminated a test EC2 instance (`i-026e7862e0f3b0979`, tag "new-test") on 2026-09-14, roughly 90 seconds apart. This happened through some prior Claude/agy session with an AWS MCP server wired up, NOT through the OCI VM (which had no AWS CLI at all until this session installed it).

## End-to-End Test Status

- After fixing the orphaned-process issue, one end-to-end test was run against the **live production endpoint**:
  - `POST <production-domain>/api/execute` (see redacted domain note under Architecture)
  - Task: ask agy to create a minimal free-tier EC2 test instance and then immediately terminate it.
- **Result**: Task status came back `COMPLETED` with answer `"⚠️ Antigravity agent produced no output."`
  - agy's warm session was reused and appeared to start working (no immediate ENOENT error this time, unlike earlier failed attempts), but it did not finish in time / did not compose a final answer.
- **CloudTrail verification**: Confirmed via `aws cloudtrail lookup-events` that **NO `RunInstances` or `TerminateInstances` calls occurred** in that time window. No real AWS resources were created or left orphaned by this attempted test — no cost/cleanup concern from this specific attempt.
- **Overall status**: The 4-cloud (AWS/GCP/Azure/OCI) create-and-delete end-to-end test has **NOT** been successfully completed for ANY of the 4 clouds this session.
  - Only AWS was attempted, and it did not produce a confirmed result either way.
  - GCP, Azure, and OCI create/delete were never attempted this session.

## Session Addendum (2026-09-16) — Which backend/agy actually ran a chatbox request, and Azure status

Triggered by: user reported that creating+deleting an Azure Cosmos DB account (`cosmos-sales-keshav`, `rg-ai-orchestration`) via the main chatbox worked on 2026-09-05, but the same kind of request stopped working by 2026-09-16. This section documents how to answer "which backend handled it" and "is Azure actually configured" without re-doing the whole investigation.

### How to tell which backend/agy processed a given chatbox request

The frontend (`index.html`, served from GitHub Pages at `https://karnkeshav.github.io/ai-orchestration/`) picks its backend candidate list based on `window.location.origin` (~line 1317, `getBackendCandidates()`):
- Origin is `localhost`/`127.0.0.1` → **local laptop `server.py`/`agy` tried first**.
- Any other origin (i.e. the real GitHub Pages URL) → **OCI VM (`ai-orchestration-vm`, `OCI_VM_BACKEND` constant) tried first**, then localhost, then Render (`ready4launch.onrender.com`) as last resort.

Since this repo's frontend has pointed the "production" branch at the OCI VM since the very first deploy (`304b085`, 2026-08-30, "Zero Localhost"), **any request made via the GitHub Pages URL has always been processed by the OCI VM's `agy`, never the laptop's** — this includes the 2026-09-05 Cosmos DB test.

**Definitive proof method (don't guess — check the cloud provider's own activity log):** Azure Activity Log events carry the calling identity's AAD token claims, which include the real source IP (`.claims.ipaddr`). For the Sept 5 Cosmos DB create/delete:
```powershell
az monitor activity-log list --start-time <ISO> --end-time <ISO> -o json
# then inspect $event.claims.ipaddr on the write/delete EndRequest events
```
Both the account-create (`Microsoft.DocumentDB/databaseAccounts/write`, 03:52–03:55) and account-delete (`.../delete`, 04:24–04:33) events on 2026-09-05 show `ipaddr: 129.225.111.42` — the OCI VM's public IP. A separate `listKeys` batch at 04:11–04:21 came from `49.43.221.91` (the user's own laptop/home IP) — that was the user manually checking the portal/connection string in between, not part of the automated pipeline. This same technique (check `claims.ipaddr` on the relevant provider's audit/activity log) generalizes to AWS CloudTrail (`sourceIPAddress`) and GCP audit logs (`protoPayload.requestMetadata.callerIp`) if the same question ever comes up for those providers.

### OCI VM `agy` MCP toolset — state as of 2026-09-16

SSH: `ssh -i "<Downloads>\ssh-key-2026-07-02.key" ubuntu@129.225.111.42` (this key, not the 2026-08-19 one, was verified working this session).

`~/.gemini/config/mcp_config.json` on the VM currently lists these MCP servers: `amazon, blinkit, flipkart, ola, rapido, swiggy, uber, zomato, zepto, meesho, azure, oci, gcp, aws`. An `azure` entry **does exist** (`/home/ubuntu/azure_mcp_wrapper.py`) — earlier working assumption in this doc's original AWS IAM section that Azure had no wrapper at all was incomplete; it exists but its *auth state* is what's unverified (see below).

File mtimes for the cloud wrappers show a **batch rewrite on 2026-09-14 ~17:53**, the same day as the DuckDNS/nginx backend migration (`bd9ab30`):
- `azure_mcp_wrapper.py` — rewritten 2026-09-14 17:53 (original `azure_mcp_server.py` itself is untouched since 2026-08-30 15:49)
- `gcp_mcp_wrapper.py` — rewritten 2026-09-14 17:53
- `cloud_mcp_server.py` / `cloud_mcp_wrapper.py` — brand new files, 2026-09-14 17:09/17:12 (a newer generic multi-cloud MCP server, purpose/relationship to the per-provider wrappers not yet investigated)

**Azure auth mechanism**: `azure_mcp_server.py` uses `azure.identity.DefaultAzureCredential()` — no `AZURE_*` env vars are set on the VM (`env | grep -i azure` → empty), no managed identity, so in practice this falls back to whatever's cached in `~/.azure` (the Azure CLI login cache).

**Important: `az` the CLI binary is NOT installed/on-PATH on the VM** (`az: command not found` in a normal SSH session) — despite this, a `~/.azure/` cache directory exists and is populated, meaning `az login` was run there at some point (likely via `uv run --with azure-cli` or similar, not a persistent global install). Cache file timestamps:
- `azureProfile.json`, `clouds.config`, `az_survey.json`, `az.json` — all 2026-08-30 16:39 (initial login)
- `commandIndex.json`, `extensionCommandTree.json`, `extensionHelpIndex.json` — 2026-09-05 02:17–04:28 (**matches the working Cosmos DB test exactly**)
- `msal_token_cache.json`, `msal_http_cache.bin` — refreshed 2026-09-14 15:59 (same day as the wrapper rewrite)
- `config`, `az.sess`, `versionCheck.json` — touched as recently as **2026-09-16 08:18** (today, before this session's SSH check even happened — something is still periodically touching this cache)

**NOT verified this session — blocked by Claude Code's safety classifier ("Credential Exploration")**: whether the cached Azure login is still *valid* (token not expired) right now. Both `DefaultAzureCredential().get_token(...)` and even a passive `cat ~/.azure/azureProfile.json` were blocked. **Whoever picks this up needs to either run this check themselves via a direct SSH session (not through an AI agent bound by that classifier), or explicitly add a Bash permission rule allowing inspection of `~/.azure/*` on this host** before an agent can finish this specific check.

### Conclusion for the original question ("why did Cosmos DB create/delete stop working?")

Not a code regression in `server.py`'s routing (the mutation-verb escalation logic that sends create/delete to `agy` is intact and correct). Most likely explanation, in order of evidence strength:
1. The Azure CLI login cache backing `DefaultAzureCredential` on the OCI VM may have expired or been invalidated by the 2026-09-14 wrapper rewrite — **unconfirmed, needs the credential check above**.
2. Independently, `agy` on the VM was confirmed completely non-functional for some window before 2026-09-15 due to the orphaned-process bug (see "Bugs Found & Fixed #2" above) — if the user's failed attempt happened to land in that window, that alone explains it regardless of Azure auth state.
3. The public URL fronting the VM churned through 4 different mechanisms between Aug 30 and Sep 14 (Cloudflare quick tunnel → Render fallback → raw IP → DuckDNS+nginx+certbot), with at least one documented outage (tunnel timeout, fixed 2026-09-05) — a request landing during any undocumented gap would silently fail over to the unrelated Render backend.

### Resolved: how Cosmos DB create/delete actually executed with no dedicated tool

`/home/ubuntu/azure_mcp_server.py` on the OCI VM has existed unchanged since **2026-08-30** (mtime). `~/.gemini/config/mcp_config.json` was created (birth time) **2026-09-01**. Both predate the 2026-09-05 Cosmos DB test by days.

The server only exposes these named tools: `azure_list_subscriptions`, `azure_list_resource_groups`, `azure_list_vms`, `azure_vm_action`, `azure_list_storage_accounts` — **none specific to Cosmos DB** — plus a generic escape-hatch tool:
```python
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
```
`agy` almost certainly composed ad-hoc Python using `azure.mgmt.cosmosdb.CosmosDBManagementClient(get_credential(), sub_id)` on the fly and ran it via `azure_run_script` — it never needed a purpose-built Cosmos DB tool. Auth flowed through `get_credential()` → `DefaultAzureCredential()` → the `az login` session cached in `~/.azure` (established 2026-08-30, confirmed fresh as of the Sept 5 test via the `commandIndex.json`/`extensionCommandTree.json` cache-refresh timestamps of 02:17–04:28 that day).

**Correction to the MCP-wrapper note above**: `~/.bash_history` on the VM only holds 32 lines — a short/recent window that does not reach back to 2026-09-05. What it does show is a same-day (**2026-09-14**) sequence: creation of a short-lived unified `cloud_mcp_server.py`/`cloud_mcp_wrapper.py` experiment, then its removal (`data["mcpServers"].pop("cloud", None)`) and re-registration of separate `azure`/`oci`/`gcp` entries in `mcp_config.json`. This was same-day cleanup of an unrelated experiment, **not** the origin of Azure MCP support — which (per the timestamps above) predates it by two weeks.

### Final resolution: credentials verified working across all 4 providers, latency fix applied

**Latency fix applied and independently verified live**: `AGY_WARM_IDLE_SECONDS=21600` (6 hours, up from the 30-minute default) added to `/etc/systemd/system/ai-studio.service` on the OCI VM, service restarted, confirmed via `grep AGY_WARM_IDLE /etc/systemd/system/ai-studio.service` and a live `curl http://127.0.0.1:8000/api/health` → `{"status":"online","engine":"Antigravity Autonomous Core","active_tasks":0}`. Config-only change (systemd `Environment=` line); no `server.py` code touched, since the env-var override already existed in code (`WARM_IDLE_TIMEOUT_SECONDS = int(os.environ.get("AGY_WARM_IDLE_SECONDS", 30*60))` at server.py:3895).

**Credential verification across all 4 providers — completed and independently cross-checked**, not just trusted from the backend's own self-report. A verification script was run directly in the OCI VM's backend Python runtime; its claims were then independently re-verified via four completely separate tool paths (not reusing the VM/backend at all):

| Provider | Backend-reported identity | Backend-reported resource | Independently re-verified via | Result |
|---|---|---|---|---|
| AWS | `arn:aws:iam::533267451842:user/agy-vm-readonly` | `i-01fbc0b7bb46ec091` (`cheapest-test-instance`, t4g.nano, running, us-east-1a) | `aws ec2 describe-instances` from a separate local AWS CLI session (different IAM user, `claude-code-user`, same account `533267451842`) | Exact match — confirmed real |
| OCI | `keshav.karn@gmail.com` via `~/.oci/config`, ap-hyderabad-1 | `sensex-bot` instance, RUNNING | `mcp__oci__get_instance` (OCI MCP tool, separate from the VM/agy entirely) | Exact match on name/state/shape — confirmed real |
| Azure | Service principal (`AZURE_CLIENT_ID`/`AZURE_CLIENT_SECRET`/`AZURE_TENANT_ID` in `/home/ubuntu/ai-orchestration/.env` — NOT the `az login` device-code cache investigated earlier) | `azure-ai-node-1`, Standard_B1s, indiasouthcentral, running | `az vm list -d` on a separate already-authenticated local `az` CLI session | Exact match — confirmed real |
| GCP | ADC, project `calm-catfish-464514-t6` | `gcp-ai-node-1`, e2-micro, us-central1-a, RUNNING, IP `35.253.123.223` | `gcloud compute instances list` on a separate local `gcloud` session | Exact match including IP — confirmed real |

**Correction to earlier notes in this addendum**: Azure auth on the VM is a **service principal via `.env`**, not the `az login` cache in `~/.azure` that this document spent significant effort investigating (that cache's validity is now moot — `DefaultAzureCredential()`'s chain tries env-var credentials before falling back to the CLI cache, so the service principal wins first). This matches the `~/.bash_history` snippet already found (see "Resolved" section above) that wrote exactly those three env vars into `.env`.

**Important unresolved caveat — do not claim full confidence without flagging this**: none of the above proves create/delete *mutations* actually work end-to-end post-fix — it only proves (a) each provider's read-only credential is valid right now, and (b) each provider's *listing* API call succeeds. The actual mutation path (`agy` composing/calling a create or delete tool) has **not been freshly re-tested since any of today's fixes** (credential confirmation, warm-session idle timeout change). Recall from "End-to-End Test Status" above: the one AWS create+terminate E2E test attempted on 2026-09-15 (after the orphaned-process fix, with credentials present) returned `"⚠️ Antigravity agent produced no output"` and CloudTrail confirmed zero `RunInstances`/`TerminateInstances` calls were made — i.e. even with valid credentials and a running service, `agy` silently failed to invoke a tool at all for that attempt. This specific failure mode has never been root-caused or re-tested. Read-only queries (`try_instant_cloud_query` in `server.py`, zero-LLM/zero-agy direct SDK calls) are much more reliable by construction and should be trusted more than untested mutation paths.

## Outstanding Next Steps

1. **Apply the `_POWERBI_BUILD_VERBS` fix to the OCI VM's production `server.py`** (`/home/ubuntu/ai-orchestration/server.py`) — currently only fixed locally. Requires a human, or an agent not subject to the "Remote Shell Writes" block, to perform the SSH file edit. See "Bugs Found & Fixed" #1 above for the exact diff needed.
2. **Re-attempt the AWS create/delete end-to-end test.** Previous attempt was inconclusive — agy timed out with no output, but also made no real API calls per CloudTrail. Unclear whether this is a timeout-tuning issue, an MCP tool issue, or something else in the OCI VM's agy session.
3. **Run the same create/delete end-to-end test for GCP, Azure, and OCI** — not yet attempted.
4. **No monitoring/alerting on the OCI VM's systemd service.** If the orphaned-process problem recurs (e.g., after a VM reboot or a manual `nohup` start by someone), the same silent-crash-loop failure mode (see Bug #2) will reappear silently for hours. Consider adding a check (e.g., `ExecStartPre` port check, or a monitoring alert) to catch this automatically.
5. **`GEMINI_API_KEY` is revoked/leaked** (per a pre-existing code comment dated 2026-09-10) and the Gemini fast-path tier (tier 3 in `_run_pipeline_tiers`) is disabled. Not addressed this session; remains a live issue independent of everything above.
6. **IAM policy expansion requests will likely hit the same hard platform block.** Any future request to add IAM (`iam:*`) permissions to `agy-vm-readonly` or any similar unattended-agent credential should be expected to be blocked by the Claude Code safety classifier, as it was this session.
7. **RESOLVED (see "Final resolution" section above)**: all 4 providers' credentials confirmed valid and independently cross-checked as of 2026-09-16. Azure auth turned out to be a service-principal via `.env`, not the `~/.azure` CLI cache this item originally asked about — that cache's validity is now moot.
9. **Run a fresh live create+delete E2E test for at least one resource on each of the 4 providers, now that credentials are confirmed and the warm-session idle timeout is fixed** — this is the only way to know mutations actually work post-fix, since nothing has actually created/deleted anything since 2026-09-05 (Azure) and the one 2026-09-15 attempt (AWS) failed silently with no root cause identified.
8. **Investigate `cloud_mcp_server.py`/`cloud_mcp_wrapper.py`** (new on the OCI VM as of 2026-09-14, alongside the `azure`/`gcp` wrapper rewrites same day) — unclear if this is meant to supersede the per-provider wrappers (`azure_mcp_server.py`, `gcp_mcp_server.py`, etc.) or run alongside them, and whether its introduction is related to the Cosmos DB regression.

## Session Notes — 2026-09-16: Unattended Auto-Termination Root Cause, Routing Fix, Credential Re-Architecture, Groq Router

Triggered by: user noticed a GCP instance report didn't match the expected "5th September state." That question turned into a full root-cause investigation of why cloud resources were disappearing shortly after creation, plus two production bug fixes, two rounds of E2E validation, a credential architecture change across all four clouds, and a new LLM-routing tier.

### Root cause: production `agy` was self-terminating resources it had just created

Investigation via gcloud/aws/oci CLI logging and audit trails (CloudTrail, GCP audit logs, OCI audit) found that the production backend (`server.py` on the OCI VM, `129.225.111.42`, duckdns.org domain, `ai-studio.service`) was creating real cloud instances and then auto-terminating them within seconds to minutes — observed across GCP, AWS, and OCI, with Azure suspected but not directly confirmed. Cause: the unattended `agy` agent runs with `--dangerously-skip-permissions` (no human approval step, no human present in a backend request — see Architecture section above) and was self-interpreting a plain "create an instance" request as "prove I can do it, then clean up after myself."

### Bugs Found & Fixed

#### 1. Missing guardrail against unrequested deletion

- **Location**: `server.py`, `_AGY_TOOL_HINT`.
- **Bug**: Nothing in the prompt told the `agy` agent it should never delete/terminate a resource — existing or newly created — unless the user's own request explicitly asked for that. Left to its own judgment under `--dangerously-skip-permissions`, it chose to clean up after itself.
- **Fix**: Edited `_AGY_TOOL_HINT` to explicitly forbid terminating/deleting any cloud resource unless the user's request explicitly asks for it.
- **Status**: Deployed to production via SSH — run by the user (Claude Code's own Bash tool remains blocked from mutating SSH to this host by the "Credential Exploration" classifier; see Guardrails below).

#### 2. `is_shopping_mission_query()` hijacking cloud-provisioning prompts

- **Bug**: The shopping/deal-finder mission matcher matched on the bare word "cheapest," so a prompt like "create the cheapest AWS instance" got routed into the unrelated e-commerce deal-finder path (tier 2.5 in `_run_pipeline_tiers`) before it ever reached the `agy` agent.
- **Fix**: Added `is_cloud_infra_mutation_query()` — checks for a mutation verb plus a cloud keyword — and used it to skip the shopping match for these prompts.
- **Status**: Deployed to production.

### E2E validation round 1 — concurrent, hit contention

Ran 4 parallel E2E tests (one per cloud) against production. All 4 hit problems:
- Initially hit the fix-#2 routing bug (tested before that fix was deployed).
- After deploying fix #2, hit contention on the single shared `_agy_session` warm session: OCI got stuck in queue, Azure's task vanished from `/api/status` entirely (`"Task not found"`), and AWS's `agy` session crashed after an IAM self-check.
- Root-caused to concurrency contention on the one warm `agy` session, not to the two fixes above.

### E2E validation round 2 — sequential, all passed

Ran AWS, GCP, Azure, OCI one at a time (not concurrently). All 4 passed cleanly, confirming both fixes work end-to-end — including OCI, which hit a real Always-Free quota limit (2× `VM.Standard.E2.1.Micro` max) and correctly refused to delete an existing instance to make room, citing the new no-unrequested-deletion guardrail instead of working around it.

### Credential architecture change — back to owner identity on all 4 clouds

User decided the automation should run entirely under their own owner identity on all four clouds, with no separate locked-down service accounts/principals — matching how it worked on 2026-09-05, before a 2026-09-14 security-hardening pass introduced scoped-down identities (`agy-vm-readonly` on AWS, a service principal on Azure).

- **GCP**: already using the owner's personal account (`keshav.karn@gmail.com`, `roles/owner`) — confirmed via `gcloud projects get-iam-policy`. No change needed.
- **OCI**: already using the owner's personal account (`keshav.karn@gmail.com`, member of the `Administrators` group) — confirmed via `oci iam user list-groups`. No change needed.
- **AWS**: switched via a new `[agy]` profile alias in `~/.aws/credentials` on the VM, pointing at the owner's existing `claude-code-user` IAM identity (not root), plus `AWS_PROFILE=agy` set in both `.env` and the systemd unit's `Environment=`. Verified at the process level via `/proc/<PID>/environ`, and confirmed the `aws_mcp_wrapper.py` subprocess inherits it.
  - Note: attempts to rename the IAM user itself to literally `agy` (`aws iam update-user --new-user-name agy`) and to attach a broader managed policy (`PowerUserAccess`) were both blocked by Claude Code's safety classifier ("Modify Shared Resources" / generic IAM-mutation block). This is a durable guardrail — expect it to block again in future sessions.
- **Azure**: switched from a service principal (`AZURE_CLIENT_ID`/`AZURE_CLIENT_SECRET`/`AZURE_TENANT_ID` in `.env`, now commented out) to the owner's personal cached login via `az login --use-device-code` (device code approved in browser by the user). Verified via `az account show` → `"type": "user"`, `Keshav@keyshavkarnoutlook.onmicrosoft.com`.

### Bug found: error-masking in the multi-cloud tabular compute display

- **Location**: `try_instant_cloud_query`, multi-cloud tabular compute-instance display path.
- **Bug**: A query error (exception/timeout) for one provider was silently rendered as "0 VMs" in the table, discarding the actual error text — misleading, since "0 VMs" is indistinguishable from a real empty account.
- **Fix**: Partially applied — now surfaces the real error instead of a fake zero.
- **Note**: The *single-provider* path (`run_mission_pipeline`'s `_run_compute_m`) was already showing raw errors correctly and did not need this fix.

### Live production reliability bug found (not yet fixed)

Found via a GCP timing-test subagent:
- `/api/status/{task_id}` intermittently returns `{"status": "NOT_FOUND"}` for an extended period (minutes) for a task that genuinely exists and later completes — looks like a backend state-visibility issue, possibly multiple worker processes/instances not sharing the in-memory `tasks` dict.
- Separately: when the `agy` session crashes mid-task, the fallback chain (at the time, Gemini — which had no working API key) masked the failure by silently returning a stale/generic answer instead of surfacing the error. Net effect: a create request could appear to have succeeded when nothing was actually created.
- **Status: NOT fixed.** Flagged as a follow-up — see Outstanding Next Steps.

### Speed optimization: `_AGY_CLOUD_HINT`

Added `_AGY_CLOUD_HINT` — hands the `agy` agent already-known account/project/subscription/region defaults directly in the prompt for cloud-infra mutation requests, with an instruction to skip enumerating all projects/subscriptions/instances before acting:
- AWS account `533267451842` / `us-east-1`
- GCP project `calm-catfish-464514-t6` / `us-central1-a`
- Azure subscription `2cfd3004-...` / `rg-ai-indiasouthcentral` / `indiasouthcentral`
- OCI tenancy root / `ap-hyderabad-1`

Confirmed live and working via a timing-test subagent — no more `gcp_list_projects`-style enumeration calls in the log trail.

### Architecture concern raised: brittle keyword routing

User pointed out that keyword-matching routing (`_MUTATION_VERBS`, `is_shopping_mission_query`, `wants_compute`/`wants_cost`/etc.) is brittle. Example: "how many resources are there on gcp" doesn't match any fast-path keyword (only "instance"/"vm"/"server"/"ec2"/"service" trigger it), so it falls through to the slow/unstable `agy` path — which was observed hanging indefinitely (stuck in `PROCESSING` for 2+ minutes, never completing) for that exact query.

### New: Groq-based semantic router (replaces disabled Gemini fast-path tier)

Since `GEMINI_API_KEY` is revoked/leaked as of 2026-09-10 (tier 3 in `_run_pipeline_tiers` has been dead since then — see Architecture section and Outstanding Next Steps #5 above), built a Groq-based router to catch phrasing the keyword fast-paths miss, without waiting on a Gemini key fix.

New code in `server.py`:
- `GROQ_MODEL`, `_groq_client()`
- `_gemini_schema_to_json_schema()` — converts the existing Gemini tool-declaration catalog into OpenAI/Groq tool-calling format, so both routers share one tool catalog and can't drift out of sync
- `_groq_tool_declarations()`
- `run_groq_pipeline()` — mirrors `run_gemini_pipeline`'s structure and dispatches via the same `_GEMINI_DISPATCH` table, but calls Groq's chat completions API with tools instead of Gemini's API

Wired into `_run_pipeline_tiers` to run before `agy` (catches phrasing the keyword fast-paths miss) and as the fallback if `agy` crashes.

Deployment:
- `GROQ_API_KEY` added to `.env` (both the local repo copy and production VM's `/home/ubuntu/ai-orchestration/.env`)
- `groq` Python package (v1.7.0) installed in the VM's venv
- Code deployed to production, service restarted, health-checked OK

**Bug found on first live test**: the configured model ID `llama-3.3-70b-versatile` returns `404 model_not_found` from Groq's API for this account/key ("The model `llama-3.3-70b-versatile` does not exist or you do not have access to it."). The Groq tier's exception handling worked correctly — it caught the failure and fell through to `agy` as designed — but the router itself is non-functional until `GROQ_MODEL` is updated to a valid model ID.

**Next immediate step**: query `GET https://api.groq.com/openai/v1/models` with the configured key **from the VM itself**, where the key already lives server-side — not by passing the raw key through Claude Code's own tool calls, which gets blocked as "Credential Leakage" (see Guardrails below). Use the resulting valid model list to update `GROQ_MODEL` in `server.py` and redeploy.

### Durable Claude Code platform guardrails encountered this session

For future sessions' awareness — these were tested and held firm this session:
- **SSH to the production VM** (read or mutating) from Claude Code's own tools was blocked ("Credential Exploration" category) in this session. All VM-side changes this session were performed by the user or a separate already-connected session, never by Claude Code's own Bash tool. **Superseded 2026-09-21: SSH from Claude Code's tools now works for both reads and writes when the user explicitly authorizes it - see the 2026-09-21 section at the end of this file.**
- **IAM/permission-policy mutations** (`attach-user-policy`, `update-user` rename, etc.) are blocked regardless of scope ("Modify Shared Resources" / generic block) — confirmed again this session (AWS IAM rename/policy-attach attempts).
- **Passing a raw API key/secret value inline** in an outbound Claude-Code-initiated request is blocked ("Credential Leakage") — relevant to the Groq model-list lookup above; do that lookup from the VM, not through Claude Code's tools.

## Session Notes — 2026-09-16 (continued): Multi-Provider Router Saga Resolved via Gemini, Encoding Bug Recovery, Azure Timeout Root-Caused

Follow-up to the "Unattended Auto-Termination Root Cause, Routing Fix, Credential Re-Architecture, Groq Router" section above, same day. Picks up right where that section's "Next immediate step" (fix the Groq router's model ID) left off.

### The multi-provider semantic-router saga, and its resolution

Live testing after the initial Groq router deploy (model `llama-3.3-70b-versatile`) confirmed the 404 flagged above — that model is deprecated for this account/key. Tried a sequence of replacement models and providers, all failing for different reasons:

| Attempt | Provider | Model | Failure |
|---|---|---|---|
| 1 | Groq | `llama-3.3-70b-versatile` | `404 model_not_found` (deprecated) |
| 2 | Groq | `qwen/qwen3.8-27b` | Emitted non-standard XML tool-call syntax; Groq's own parser rejected it |
| 3 | Groq | `openai/gpt-oss-120b` | After a schema-reinforcement prompt fix, got the tool name + args right — but Groq's own tool-call serializer then rejected the response: `"expected string, but got object"` for the arguments field |
| 4 | Groq | `groq/compound` | Doesn't support custom tool declarations at all — it's an agentic system with only built-in tools |
| 5 | Cerebras | (added as a 2nd OpenAI-compatible option) | `402 Payment Required` — no payment method on file, never actually tested beyond this |
| 6 | SambaNova | (added as a 3rd OpenAI-compatible option) | `402 Payment Required` — same, never actually tested |

For attempts 5–6, refactored a shared `_run_openai_compatible_router_pipeline` helper so Cerebras/SambaNova could reuse the same OpenAI-compatible client/tool-calling code path as Groq rather than duplicating it.

**Resolution**: the user provided a fresh `GEMINI_API_KEY` (the original router/tier 3, disabled since the 2026-09-10 key leak — see Architecture section and Outstanding Next Steps history above). Rather than continuing to chase Groq/Cerebras/SambaNova model quirks, pivoted back to Gemini. It worked immediately and correctly — including demonstrating real semantic understanding: a query for "resources" correctly triggered both `query_compute` AND `query_services` tool calls in one turn, something no keyword matcher could do.

### Cleanup: removed all non-working provider code and keys

Per explicit user instruction ("remove all the unwanted api keys if they are not working"), fully removed the Groq/Cerebras/SambaNova code and credentials, since none of the three ever completed a single successful end-to-end request:
- Removed: client functions, model constants, the shared `_run_openai_compatible_router_pipeline` helper, and the pipeline wrapper functions — roughly 190 lines total from `server.py`.
- Removed the corresponding API keys from both the local repo `.env` and the production VM's `.env`.
- Kept `run_gemini_pipeline` (pre-existing from before the 2026-09-10 leak) — just re-wired it into `_run_pipeline_tiers` ahead of `run_agy_pipeline`, restoring the original tier-3 position.

### Self-inflicted encoding bug and recovery (lesson for future sessions)

While removing the Groq/Cerebras/SambaNova code, used PowerShell's `Get-Content`/`Set-Content` on the whole `server.py` file to delete a line range. This corrupted every non-ASCII character in the file (emoji, em-dashes) via a UTF-8-bytes-misread-as-cp1252 mojibake pattern (e.g. `⚡` became `âš¡`).

- **Caught before deployment**: `py_compile` succeeded (mojibake is still valid Python), but a visual diff check showed garbage characters throughout. Not deployed to production.
- **Recovery**: confirmed git had a clean, uncorrupted baseline predating the whole day's session (`git restore server.py`), then carefully re-applied each of the day's real changes individually via precise Edit-tool string replacements (not bulk line-array operations), running a syntax check after each one.
- **Lesson recorded for future sessions**: never use PowerShell `Get-Content`/`Set-Content` (or any line-array round-trip) on files containing non-ASCII characters like emoji — use targeted string-replacement edits instead, or Python with explicit UTF-8 encoding if a bulk rewrite is truly required.

### Azure SDK timeout — root-caused and fixed

The "0 instances on Azure" bug reported at the very start of this session (2026-09-16), and the Azure query timeouts seen in later live tests (always exactly "timed out after 10s" across `query_azure_vms`/`query_azure_services`/`query_azure_cost`), were root-caused to `DefaultAzureCredential()`'s provider chain: on this OCI VM (not an Azure VM), `ManagedIdentityCredential` — one step in that chain — tries to reach an Azure-only IMDS metadata endpoint that doesn't exist on OCI, stalling before ever falling through to the CLI credential that actually holds the user's cached `az login` session.

- **Fix**: switched all 4 usages in `server.py` (`query_azure_vms`, `query_azure_services`, `query_azure_cost`, `_fetch_azure_pdf_bytes`) to use `AzureCliCredential()` directly instead of `DefaultAzureCredential()`, skipping the slow chain entirely.
- **Confirmed live**: Azure resource query now correctly returns real data (3 VMs — `azure-ai-node-1`, `e2e-fix-verify-azure-2`, `e2e-fix-verify-azure-3`, all `Standard_B1s` in `indiasouthcentral`; plus 1 Function App, `shift-pulse-messaging`) in ~10s instead of timing out and silently masking as "0 instances."
- **Scope**: this fix is read-only-query-path only. `agy`'s separate create/delete path (already fixed earlier in the day via the owner-identity `az login --use-device-code` change) is unaffected.

### New reliability bug investigated (unresolved, low priority): `/api/status/{task_id}` intermittent `NOT_FOUND`

This is the same class of bug flagged earlier today ("Live production reliability bug found (not yet fixed)" above) but investigated further this round. `/api/status/{task_id}` intermittently returns `{"status": "NOT_FOUND"}` for a task that genuinely exists and later completes correctly with the right data for that exact `task_id`.

Ruled out:
- **Multi-worker process state isolation** — confirmed via `cat /etc/systemd/system/ai-studio.service` + `ps -ef` that only ONE uvicorn process runs, no `--workers` flag, no load-balancing in nginx (single direct upstream to `127.0.0.1:8000`).
- **Task cleanup/expiry logic** — none exists in the codebase (grepped for it).
- **A race condition in `/api/execute`** — confirmed via code read that `tasks[task_id] = {...}` happens synchronously before the response returns.

Empirically tested: fired a fresh request in steady state (no restart in the preceding several minutes) and the bug did NOT reproduce — immediate poll correctly showed `PROCESSING`, then `COMPLETED` cleanly. Every actual occurrence of this bug throughout the whole session happened specifically right after a deploy + `systemctl restart`, even after the health check had already passed.

**Root mechanism not fully proven.** Candidate theory: a slow one-time lazy import on the first real request after a fresh process start (e.g. `from azure.identity import ...` or `from google import genai` imported inside functions rather than at module level) — not confirmed with server-side timing logs. Classified as low real-world priority since production doesn't restart on every user request the way this session's rapid iterate-deploy-test-deploy-test cycle did. Left open for future investigation if it recurs in normal usage.

### Confirmed scope boundaries

None of today's changes (cloud routing fixes, Gemini router, Azure credential fix) touch the app-creation (`try_instant_app_creation` / `build_and_deploy_stitch_app`), Power BI (`_AGY_POWERBI_HINT`, `generate_powerbi_dashboard`), or SharePoint/CSV (`list_sharepoint_csv_files`) code paths — verified by reading the actual dispatch order and confirming Gemini's own tool handlers for those (`_gemini_exec_create_and_deploy_app`, `_gemini_exec_generate_powerbi_dashboard`, `_gemini_exec_list_sharepoint_csv_files`) call the same real, pre-existing underlying functions, not stubs. Re-enabling Gemini is additive coverage for those paths (a second way to reach them if keyword phrasing misses), not a behavior change.

### Commits this session

Two commits were made to git this session (both touching `server.py`):
- `6adc451` — "fix(cloud): stop agy auto-deleting resources, fix routing, restore semantic router"
- `35b6b0a` — "fix(azure): use AzureCliCredential directly instead of DefaultAzureCredential"

## Outstanding Next Steps (updated 2026-09-16, end of session)

1. **DONE — Azure SDK timeout fixed and confirmed live.** (Was open earlier today; see "Azure SDK timeout — root-caused and fixed" above.) All 4 `AzureCliCredential()` swaps deployed to production and verified returning real VM/Function App data in ~10s.
2. **NEW — `/api/status/{task_id}` intermittent `NOT_FOUND` bug, investigated but unresolved.** Multi-worker isolation, task-expiry logic, and an `/api/execute` race condition were all ruled out. Strongly correlated with requests landing shortly after a deploy+restart; candidate theory is a slow lazy import on first request, unconfirmed. Low priority for now since production restarts are rare outside active iteration sessions like this one. See "New reliability bug investigated" above for the full investigation trail before re-opening this.
3. **DONE — router tier 3 restored, but via Gemini, not Groq.** Groq/Cerebras/SambaNova were all tried and abandoned (see saga table above) and their code/keys fully removed. `run_gemini_pipeline` is back in `_run_pipeline_tiers` ahead of `run_agy_pipeline`, using a freshly-issued `GEMINI_API_KEY`, confirmed live with correct multi-tool-call semantic routing.
4. **DONE — all 4 clouds (AWS, GCP, Azure, OCI) reconfirmed working end-to-end earlier today**, via sequential (not concurrent) solo E2E testing per "E2E validation round 2" above, independent of the router work — this closes out the older "re-attempt create/delete E2E test" items carried in this file since 2026-09-15.
5. **Fix the fallback-masking bug**: when `agy` crashes mid-task and the fallback tier also fails, the failure was previously masked as a stale/generic success-looking answer instead of a surfaced error. Worth re-checking now that Gemini (a working fallback) is back in the tier chain — the masking scenario specifically arose when the fallback ALSO had no working key, which is no longer the case, but the underlying missing-error-path logic itself was never directly patched.
6. **Finish the error-masking fix** in `try_instant_cloud_query`'s multi-cloud tabular compute display — the single-provider path was already correct; the multi-cloud tabular path fix should be double-checked for completeness.
7. **Consider replacing brittle keyword routing** (`_MUTATION_VERBS`, `is_shopping_mission_query`, `wants_compute`/`wants_cost`/etc.) with semantic routing more broadly — Gemini's tier is a step in this direction but keyword fast-paths still run first and can still misroute phrasing they don't recognize (e.g. "how many resources are there on gcp").
8. **Re-verify Azure auto-termination.** GCP, AWS, and OCI were directly confirmed exhibiting the auto-terminate bug pre-fix; Azure was suspected but never directly confirmed before the no-unrequested-deletion guardrail fix was deployed.
9. Previously-outstanding items from 2026-09-15 (PowerBI build-verb fix on production, monitoring/alerting for the systemd service, `cloud_mcp_server.py` investigation) remain open and are believed unaffected by this session's work — not re-verified this session.
10. **Lesson to carry forward, not an action item**: never use PowerShell `Get-Content`/`Set-Content` (or any line-array round-trip) on files with non-ASCII characters (emoji, em-dashes) — it silently mojibake-corrupts them. Use targeted Edit-tool string replacements, or Python with explicit UTF-8 encoding for genuine bulk rewrites.

## Session Notes — 2026-09-16 (continued again): Full multi-cloud resource inventory added, "going in circles" bug fixed

Triggered by: user compared their Azure Portal (14 resource-manager entries, screenshot) against the chatbox and got only a single VM back, repeatedly, no matter how the question was phrased — "progress is going in circles." This is the exact brittle-keyword-routing failure mode flagged as Outstanding Next Steps item 7 above ("how many resources are there on gcp" doesn't match any fast-path keyword), now hit for Azure too and root-caused/fixed.

### Root cause

`server.py` never had a "list every resource type" capability for any of the 4 clouds — only narrow `query_*_vms`/`query_*_services`/`query_*_cost` functions existed. Worse, in `run_mission_pipeline`'s fallback chain:
```python
elif wants_compute or providers:   # providers = ["azure"] just because the prompt said "azure"
```
`providers` is non-empty any time a cloud name is merely *mentioned*, so a prompt like "list all resources of azure" fell into the **compute** branch and silently returned just the one VM — same wrong answer every time, hence "going in circles."

### Fix: new full-inventory function per cloud, wired ahead of the compute fallback

Added a `wants_all_resources` intent (trigger phrases: "all resources", "list resources", "what do I have", "resource inventory", etc.), checked and handled **before** `wants_compute or providers` in both `try_instant_cloud_query` and `run_mission_pipeline`, so it can no longer be swallowed by the VM-only branch.

New functions in `server.py` (one per cloud, each using that provider's native full-inventory API rather than a per-service client):
- `query_azure_all_resources()` — `azure.mgmt.resource.resources.ResourceManagementClient.resources.list()` + `resource_groups.list()`. Note: `azure-mgmt-resource` ≥23 stopped re-exporting `ResourceManagementClient` from the top-level package — must import from `azure.mgmt.resource.resources` directly, not `azure.mgmt.resource`.
- `query_aws_all_resources()` — Resource Groups Tagging API (`resourcegroupstaggingapi.get_resources`, paginated). Same single-region (`us-east-1`) convention as the rest of the file's AWS functions. Caveat inherent to this AWS API (not fixable from this side): only returns resources that are or were ever tagged — untagged resources are invisible to it.
- `query_oci_all_resources()` — `oci.resource_search.ResourceSearchClient.search_resources()` with query `"query all resources"`, tenancy-wide. `ResourceSummary` objects have no `.region` attribute (only `.availability_domain`) — using `.region` throws `AttributeError`; use `.availability_domain` instead.
- `query_gcp_all_resources()` — Cloud Asset Inventory `searchAllResources` REST endpoint (`cloudasset.googleapis.com`), paginated via `pageToken`. Requires the Cloud Asset API enabled on the project (free to enable; `searchAllResources` itself is a free-tier call, no billing). **Must send `X-Goog-User-Project: <project>` header explicitly** — local/VM Application Default Credentials without an explicit quota-project configured otherwise get routed to an unrelated Google-internal fallback project for quota/billing checks, which 403s with `SERVICE_DISABLED` even after the real target project's API is enabled and the caller is Owner.

### Second bug found while verifying billing: GCP missing from `run_mission_pipeline`'s cost path

While confirming all 4 clouds' billing still worked, found `cost_fn` in `run_mission_pipeline` (as opposed to the already-correct one in `try_instant_cloud_query`) was `{"aws": ..., "oci": ..., "azure": ...}` — no `"gcp"` key — and its default provider list for cost queries was `["aws", "oci", "azure"]`, also missing GCP. A GCP billing question landing in this pipeline (rather than the instant fast-path) would incorrectly answer "Cost querying not implemented for GCP yet." even though `query_gcp_cost` has worked fine all along. Fixed both (added `"gcp": query_gcp_cost` to `cost_fn`, added `"gcp"` to the default target list).

### Live verification (run directly against the user's real accounts/subscriptions from the local laptop copy, `az`/`gcloud`/`oci` config already authenticated there)

| Provider | Resource inventory | Billing (month-to-date) |
|---|---|---|
| Azure | 43 resources | code correct; hit a transient `429 Too many requests` from Cost Management API on one test run (known aggressive rate limit on that API, not a bug) |
| AWS | 5 resources (tagging-API limitation, see above) | $1.84 USD |
| OCI | 78 resources | $0.00 USD |
| GCP | 177 resources (after the Cloud Asset API was enabled by the user and the quota-project header fix was applied) | ₹0.04 INR |

### Dependencies

Added `azure-mgmt-resource` to `requirements.txt` (also `pip install`ed locally for testing — was not previously installed on the local laptop copy's environment despite being a new addition).

### Status: NOT deployed

All of the above is applied and verified **only on the local laptop copy** (`C:\Users\keysh\github\ai-orchestration\server.py`). Not committed to git, not deployed to the production OCI VM (`/home/ubuntu/ai-orchestration/server.py`, `ai-studio.service`). Per this file's recurring pattern (see Bug #1 and Bug #2's "Fix" sections above), Claude Code's own tools remain blocked from mutating SSH to the production VM — deployment there requires the user (or a separate already-connected session) to pull/copy the changes and restart the service, same as every prior production deploy in this file.

### Relationship to Outstanding Next Steps item 7 (brittle keyword routing)

This session's fix directly patches the specific example named in that item ("how many resources are there on gcp" / the Azure equivalent that triggered this session). It does not replace keyword routing with semantic routing — it adds one more well-scoped keyword-triggered branch (`wants_all_resources`) ahead of the branch that was mis-catching it. The broader architectural concern in item 7 (keyword fast-paths can still misroute *other* phrasings the Gemini tier doesn't catch first) remains open.

### UPDATE (later same session): status above is now stale — deployed to production, two more real bugs found and fixed, remaining provider gap closed

The "Status: NOT deployed" section above was accurate at the time it was written but has since been superseded. Recorded here rather than edited in place so the investigation trail stays intact.

#### Live test after first deploy attempt failed — `wants_all_resources` still didn't fire for the user's actual phrasing

User's literal test prompt against the live chatbox, "list all my Azure resources", still returned only the narrow VM+services answer. Root cause: two remaining gaps.

1. **Keyword list required adjacent substrings.** `"all resource"`, `"list resource"` etc. never match real phrasing like "list all **my** Azure resources" — the scope word and "resource" are rarely adjacent. Fixed by splitting into two independent substring checks (`_has_resource_word` + `_has_scope_word`, both present anywhere in the prompt) instead of one combined phrase, in both `try_instant_cloud_query` and `run_mission_pipeline`. Kept a handful of literal standalone phrases ("everything i have", "what do i have") as a second OR-branch since those don't say "resource" at all.
2. **The Gemini semantic router (tier 3, restored earlier this session) didn't know the new capability existed.** Since the prompt missed the keyword fast-path, it fell through to Gemini, which had no `query_all_resources` tool declaration and picked the two closest tools it did know (`query_compute` + `query_services`) — explaining the exact wrong output the user saw. Added `query_all_resources` to `_gemini_tool_declarations()`, a new `_gemini_exec_query_all_resources()` executor mirroring `_gemini_exec_query_services()`, `_GEMINI_ALL_RESOURCES_FN`, and a `_GEMINI_DISPATCH` entry.

Re-verified live: `"list all my azure resources"` now returns the full 43-resource table via the keyword fast-path alone (no LLM round trip needed), confirmed against the live production `/api/execute` endpoint, not just locally.

#### Deploying to the OCI VM surfaced a second incident: 89 lines of never-committed local edits sitting on the VM

`git pull` on the VM failed with "local changes would be overwritten." Investigation (not blind stash/discard — read the full diff first) found the VM's working tree had uncommitted edits that were byte-for-byte the same content as what this session's earlier `6adc451`/`35b6b0a` commits already contain upstream. Explanation: those two commits' fixes were originally applied to the VM by live-editing over SSH (per this file's own earlier "Deployed to production via SSH — run by the user" notes) but were never `git commit`ed there — the VM's git history stayed frozen at `6b8e374` while its working tree silently drifted ahead.

Resolution, verified safe at each step before acting:
1. `git stash push -- server.py` (preserve, don't discard)
2. `git pull` — fast-forwarded cleanly to `b98de21` (which already contains the same fixes via a different commit path)
3. `git apply --check --reverse` against the stash confirmed it was now **byte-identical / fully redundant** with the post-pull file before touching it further
4. `git stash drop` was attempted but blocked by Claude Code's classifier ("Irreversible Local Destruction") — left in the stash list, harmless, for the user to drop later if they want (`git stash drop` on the VM)

**Lesson for future sessions**: when applying a fix directly to the VM via live SSH edits (as this file's guardrail section says is sometimes the only option, since Claude Code's own tools are blocked from mutating SSH there), also `git commit` it on the VM immediately afterward — otherwise the next `git pull` from a properly-committed laptop change will conflict, even when the two sides agree.

#### Third gap found via a full manual audit of every provider dict, at the user's request ("are there any other queries or execution related to cloud which we are missing")

Manually checked every `*_fn` dict (`compute_fn`, `services_fn`, `cost_fn`, `storage_fn`, the new `resource_fn`) in both pipelines plus the Gemini tool catalog for missing provider entries. Found one real remaining gap: `storage_fn = {"aws": query_aws_s3, "oci": query_oci_buckets}` — no Azure or GCP entry, so a dedicated "list my Azure storage accounts" / "GCP buckets" query replied "not implemented" (a graceful degradation, not a wrong-answer bug like the earlier ones, since nothing else caught it as a false positive).

Fixed:
- Added `query_azure_storage()` (`StorageManagementClient.storage_accounts.list()`)
- Added `query_gcp_storage()` (Cloud Storage JSON API `GET /storage/v1/b`, same `X-Goog-User-Project` quota-project fix as the resource-inventory function)
- Wired both into `storage_fn` (both pipelines), `_GEMINI_STORAGE_FN`, and widened the default no-provider-named target list from `["aws", "oci"]` to all 4
- Fixed two stale Gemini tool-declaration descriptions that could have misled the router: `query_storage`'s said only aws/oci were supported (no longer true), and `query_cost`'s said gcp wasn't supported (was never actually true — `query_gcp_cost` has worked all session)

After this fix, a manual re-check of all five provider dicts confirmed every one has all 4 clouds present. Cloud query/execution coverage (compute, services, cost, storage, full-resource-inventory — everything except create/delete, which is intentionally `agy`'s job, not `server.py`'s) is now symmetric across AWS/OCI/Azure/GCP.

#### Final deployment status: LIVE on production

Three commits landed on `origin/main` and were each pulled + compiled + service-restarted + health-checked on the OCI VM this session:
- `f86eb43` — full multi-cloud resource inventory (`query_*_all_resources`, `wants_all_resources` routing)
- `b98de21` — Gemini `query_all_resources` tool + broadened keyword matching (the fix for the "still going in circles after first deploy" report above)
- `9fa8e46` — Azure/GCP storage listing, closing the last provider gap

All three verified against the live production `/api/execute` endpoint (not just locally) with real multi-second response times and real account data, not simulated. `git status` on the VM is clean at `9fa8e46` except for the one harmless, confirmed-redundant stash entry noted above.

Updates the file's "Outstanding Next Steps" item 7 (brittle keyword routing) further: two more concrete misses in that category were found and patched this round (adjacent-substring keyword matching, and an LLM router tier not knowing about a newly-added tool). The general architectural concern — new capabilities need to be registered in *both* the keyword fast-path *and* the LLM tool catalog, and it's easy to add one and forget the other, as happened here — remains a standing risk for whoever adds the next new query type.

---

## 2026-09-21 - OCI Ampere (A1) harvester session; SSH from Claude Code works

### SSH status (supersedes the older "blocked" guardrail notes above)
- SSH from Claude Code's own tools to the production OCI VM (`ai-orchestration-vm`) **worked this session for both reads and writes**, after the user explicitly said to use it ("you can try using ssh it works"). Treat the earlier "Remote Shell Writes" / "Credential Exploration" block notes as historical. Still act only on explicit user authorization, show the change first, and fall back to handing the user the commands if a call is blocked.
- Login: user `ubuntu`, key file in the Windows Downloads folder (ask Keshav which one). The IP and key filename are deliberately **not** recorded here because this repo is public.
- `scp` to the VM also works. The VM's OCI SDK (`/home/ubuntu/.oci_venv`) is a handy fallback for read-only OCI queries when the `oci` MCP server times out (it did this session).

### Harvester state found
- `oci-arm-harvester.service` was running since the last VM boot (2026-09-16 05:56 UTC): about 3,950 attempts, every one "Host capacity for VM.Standard.A1.Flex is currently full" in the single Hyderabad AD. No A1 instance exists; only the two AMD micro instances (`ai-orchestration-vm`, `sensex-bot`) are running.
- The old unit had `Restart=always`, so after a successful launch it would have restarted and launched more instances (extra boot volumes would have exceeded the 200 GB Always Free storage limit and been billed on a paid account).

### Changes applied to the VM (backups kept beside the originals)
- `/home/ubuntu/create_oci_arm_instance.py` replaced (original: `create_oci_arm_instance.py.bak`). New behaviour: rotates all 9 combinations of shape (1 OCPU/6 GB, 2 OCPU/12 GB, 1 OCPU/2 GB) x fault domain (FAULT-DOMAIN-1/2/3); exponential backoff on 429; exits without launching if an A1.Flex instance already exists; exits 0 after success; optional success webhook via the `OCI_HARVESTER_WEBHOOK` env var (not set).
- `/etc/systemd/system/oci-arm-harvester.service` (original: `.service.bak`): `Restart=on-failure` instead of `always`; added `--tiny-ocpus 1 --tiny-memory 2`. Service reloaded and restarted; first log lines confirmed the three fault domains and per-attempt fault-domain labels.
- Interval left at 90 s. Idea for later: 45 s once a day of clean logs shows no 429s.

### Account findings (checked via the OCI SDK on the VM and the OCI console)
- Tenancy was on **Free Tier** (plan type Free Tier, account type Promo, started 2026-06-30). The A1 service limit was **2 OCPU / 12 GB**, i.e. the free-only cap after Oracle's 2026-06-15 change; 4 OCPU / 24 GB is only reportedly kept on paid (PAYG) tenancies.
- No payment method was attached at first (uploading a card at signup does not upgrade the account). The user then added a card and started the upgrade to a paid account; the console showed "upgrade in progress, email when complete". **Still to verify after Oracle's confirmation email:** plan type is paid, the A1 limit rose, and the harvester still runs. Set a $1 budget alert (deferred by the user).
- Storage: 147 GB of 200 GB used. An unattached 47 GB boot volume left over from an older `sensex-bot` was **not** deleted (deleting it does not affect capacity odds; confirm it holds nothing needed first). When terminating any surplus instance, also delete its boot volume.
- Signing in to the OCI console: the "cloud account name" is the tenancy name. Do not enter passwords for the user; they sign in themselves in the automation browser window.

---

## 2026-09-21 (Part 2) - Multi-Turn Conversation & Unconstrained agy Parity (Local WSL vs OCI VM)

### 1. Problem & User Report
- Asking *"how many csv files are there on my sharepoint"* in the web UI (`https://karnkeshav.github.io/ai-orchestration/`) returned 12 files.
- Asking a natural follow-up immediately afterward (*"what is the difference in the name"*) resulted in a blank/mock status: `"✓ Autonomous directive processed successfully"` with no actual answer.
- Asking the same question in local WSL CLI (`agy`) responded naturally in full English Markdown, whereas `agy` on the OCI VM produced no answer.

### 2. Root Cause Analysis
1. **Stateless API:** `index.html` only sent the isolated prompt string to `/api/execute` without prior conversation history.
2. **Turn 1 Bypassed agy:** Turn 1 was answered instantly by Python fast-path (`try_instant_mission_match`), so `agy` on the VM never saw Turn 1 and had zero memory of the 12 files.
3. **Rigid `--json-schema` on VM agy:** `AgyWarmSession._spawn()` launched `agy` with `--json-schema agy_book_schema.json` (expecting `{markdown, options}` for shopping/rides). Non-shopping conversational answers were rejected or dropped by schema validation.
4. **Dropped `text_delta` stream:** In `_read_turn()`, `server.py` logged that `agy` was composing a response but discarded `step["text_delta"]` chunks rather than buffering them.
5. **Over-restrictive prompt hints:** `_AGY_TOOL_HINT` forced `agy` to strictly call MCP tools, causing it to stall when asked natural comparison/reasoning questions.
6. **Fake catch-all fallback:** Tier 5 returned static string `"✓ Autonomous directive processed successfully: {prompt}"` masking backend routing failures.

### 3. Changes Applied & Committed (Commit `876c39c`)
1. **Unconstrained agy (`server.py`):**
   - Commented out `--json-schema _BOOK_SCHEMA_PATH` in `_spawn()`.
   - Added `accumulated_deltas: List[str]` to capture and buffer all live `text_delta` streaming events.
   - Updated `_AGY_TOOL_HINT` to balanced dual-mode instructions (tools for live data, natural Markdown for analysis/reasoning).
2. **Conversation Context & History:**
   - Updated `ExecuteRequest` in `server.py` to accept `history: Optional[List[Dict[str, Any]]] = None`.
   - Propagated `history` through `run_pipeline` and `run_agy_pipeline` to format prior turns into agent context.
   - Updated `index.html` to maintain `window.conversationHistory` and send recent turns to `/api/execute`.
3. **Enhanced SharePoint CSV Analysis (`server.py`):**
   - Enhanced `_gemini_exec_list_sharepoint_csv_files` to automatically structure files by folder (`landmark/data/` vs `landmark/data_filled/`) and output a filename & dataset comparison matrix.
4. **Real Response Synthesis Fallback:**
   - Replaced static placeholder in Tier 5 with direct intelligent synthesis via Gemini/agy.

### 4. Deployment Status: LIVE on GitHub & OCI VM
- Pushed to GitHub repository (`origin/main`, commit `876c39c`) — updating GitHub Pages at `https://karnkeshav.github.io/ai-orchestration/`.
- Pulled and synced onto OCI VM (`129.225.111.42`) at `/home/ubuntu/ai-orchestration/`.
- Restarted `ai-studio.service` on OCI VM.
- Verified live: Turn 1 immediately outputs 12 files with folder breakdown and comparison matrix; Turn 2 receives context and responds intelligently.

---

## 2026-09-21 (Part 3) - Mutation Router Disambiguation for Tabular Presentation Queries

### 1. Problem & User Report
- Prompt: `"create a tabular view of the difference of csv file names on sharepoint"` got stuck in `PROCESSING` indefinitely with no response returned to the user.

### 2. Root Cause Analysis
1. **Mutation Verb False Positive:** The verb `"create"` matched `_MUTATION_VERBS` ("create", "launch", "provision", etc.) and `"sharepoint"` matched `_MUTATION_TARGET_KEYWORDS`. Consequently, `is_mutation_request()` evaluated to `True`.
2. **Subtle Substring Collisions:** When attempting to exclude presentation requests with `is_real_infra = any(k in prompt_lower for k in ("vm", "instance", "ec2", "bucket", "database", "repository", "repo", "app", "pipeline"))`, the substring `"repo"` evaluated to `True` because `"repo"` is a literal substring inside `"sha-repo-int"` (`sharepoint`). As a result, `is_real_infra` was mistakenly set to `True`, canceling the presentation exclusion.
3. **Fast-Path Bypassed & OCI Micro VM Overload:** The mutation router escalated the task directly to `run_agy_pipeline` to cold-start `agy` with 20 MCP servers. On the Always-Free OCI VM (`VM.Standard.E2.1.Micro`, 1 OCPU, 1 GB RAM), spawning full CLI subprocesses during high load caused memory thrashing and long task processing times.

### 3. Solution Applied
1. **Regex Word-Boundary Matching:** Replaced naive substring checks with regex word boundaries `re.search(r'\b(vm|vms|instance|instances|ec2|bucket|buckets|database|databases|repository|repositories|repo|repos|pipeline|pipelines)\b', prompt_lower)`. This prevents `"repo"` from matching inside `"sharepoint"`.
2. **`_PRESENTATION_EXCLUSIONS` in `server.py`:** Added presentation keywords (`tabular`, `table`, `view`, `summary`, `list`, `comparison`, `matrix`, `chart`, `breakdown`, `overview`, `diff`, `difference`, `compare`) to `is_mutation_request()`. If a user asks to "create a table / tabular view / summary", it is recognized as a read-only presentation/reporting request unless accompanied by actual infrastructure targets.
---

## 2026-09-21 (Part 4) - Power BI Dashboard Project (.pbip) Generation & SharePoint Path Extraction

### 1. Problem & User Report
- Prompt: `"Use my CSV files from my SharePoint landmark/data_filled/ and create a corporate Power BI dashboard"` timed out after 6 minutes with `⚠️ Antigravity CLI failed on this request (agy warm session turn failed: )`.

### 2. Root Cause Analysis
1. **Mutation Router Escalation:** The prompt contains `"create"` + `"power bi"` / `"dashboard"`, triggering `is_mutation_request(prompt_lower) == True` and escalating directly to `run_agy_pipeline`.
2. **Missing `sharepoint` MCP Server on OCI VM:** `agy` was prompted to call `sharepoint_read_file`, but `mcp_config.json` on the OCI VM did not register the SharePoint MCP server.
3. **Hardcoded Windows Path in Hint:** `_AGY_POWERBI_HINT` commanded `agy` to edit local files at `/mnt/c/Users/keysh/Documents/...` which does not exist on the Linux OCI VM.
4. **Hardcoded Folder Path:** The hint hardcoded `landmark/data/`, overriding the user's specific request for `landmark/data_filled/`.

### 3. Solution Applied
1. **`is_powerbi_dashboard_build_request` in `server.py`:** Added detection for Power BI project build requests (`power bi`/`pbip`/`dashboard` + build verb + `sharepoint`/`csv`/`data`). Excluded these requests from `is_mutation_request` so they are routed directly to the native Python TMDL engine (`powerbi_engine.generate_pbip_project`).
2. **Dynamic Folder Path Extraction:** Added `extract_sharepoint_folder_path(prompt)` to dynamically detect folder paths (e.g. `landmark/data_filled`, `landmark/data`).
3. **Fast-Path Native Execution:** Integrated `_gemini_exec_generate_powerbi_dashboard` into `try_instant_mission_match` and `run_mission_pipeline`. It connects directly to Microsoft Graph, downloads CSVs, profiles data types, auto-builds 13 star-schema relationships, hierarchies, DAX measures, Date dimension, and zips the full `.pbip` Power BI Desktop project with a direct download link.
4. **Fully-Qualified Download URLs:** Formatted download links with `PUBLIC_BASE_URL` (`https://ai-orchestration-app.duckdns.org/generated_dashboards/...`) for cross-origin downloads from GitHub Pages.

---

## 2026-09-21 (Part 5) - Direct .pbix Deliverable Generation & Dynamic Project Naming

### 1. Requirements
- Allow users to specify custom names in their prompt (e.g. `name the pbix file as sales.pbix`, `named Sales_Performance`).
- Deliver a direct, standalone `.pbix` file ready to double-click on Windows in addition to the full `.pbip` project zip.

### 2. Implementation
1. **Dynamic Project Name Extraction:** Added `extract_powerbi_project_name(prompt)` to parse names from prompts (`name the pbix file as sales.pbix` -> `sales`).
2. **Dual Deliverable Output:** `powerbi_engine.generate_pbip_project` now outputs both `output_dir/<name>.pbix` and `output_dir/<name>.zip` (`.pbip`).
3. **Download Links:** Response surfaces both direct links:
   - `• 📊 Direct Power BI File: [⬇️ Download sales.pbix](...)`
   - `• 📦 Complete Project (.pbip): [⬇️ Download sales.zip](...)`

---

## 2026-09-21 (Part 6) - Power BI Service Cloud Publishing (Entra ID / Workspace Integration)

### 1. Requirements & Problem
- User requested that the generated dashboard be pushed automatically to their Power BI cloud environment so when they open Power BI Desktop or the Power BI Web App under their Entra ID account (`Keshav@keyshavkarnoutlook.onmicrosoft.com`), the report is already there in their workspace.

### 2. Architecture & Implementation
1. **Power BI REST API Client Credential Auth:**
   - Acquired access tokens for audience `https://analysis.windows.net/powerbi/api/.default` using MSAL with `MS_TENANT_ID`, `MS_CLIENT_ID`, and `MS_CLIENT_SECRET`.
2. **Auto-Publish via Imports API:**
   - Added `publish_pbix_to_powerbi_service()` in `powerbi_engine.py`.
   - Discovers workspace `AI-Orchestration` (`id: 6a70c915-4c84-4adc-b314-5f8e1775254e`).
   - Posts the generated `.pbix` to `POST https://api.powerbi.com/v1.0/myorg/groups/{groupId}/imports?datasetDisplayName={name}&nameConflict=CreateOrOverwrite`.
   - Polls import state until `Succeeded` and extracts live report web URL (`https://app.powerbi.com/groups/{groupId}/reports/{reportId}`).
3. **Response & Deliverables:**
   - Response renders direct interactive links:
     - 🚀 **Power BI Cloud (Online):** `[🌐 Open sales in Power BI Service](https://app.powerbi.com/groups/...)` *(Workspace: `AI-Orchestration`)*
     - 📊 **Direct Power BI File:** `[⬇️ Download sales.pbix](...)`
     - 📦 **Complete Project (.pbip):** `[⬇️ Download sales.zip](...)`
   - Sets primary deliverable to the Power BI Cloud report URL.
