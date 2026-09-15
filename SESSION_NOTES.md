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

## Outstanding Next Steps

1. **Apply the `_POWERBI_BUILD_VERBS` fix to the OCI VM's production `server.py`** (`/home/ubuntu/ai-orchestration/server.py`) — currently only fixed locally. Requires a human, or an agent not subject to the "Remote Shell Writes" block, to perform the SSH file edit. See "Bugs Found & Fixed" #1 above for the exact diff needed.
2. **Re-attempt the AWS create/delete end-to-end test.** Previous attempt was inconclusive — agy timed out with no output, but also made no real API calls per CloudTrail. Unclear whether this is a timeout-tuning issue, an MCP tool issue, or something else in the OCI VM's agy session.
3. **Run the same create/delete end-to-end test for GCP, Azure, and OCI** — not yet attempted.
4. **No monitoring/alerting on the OCI VM's systemd service.** If the orphaned-process problem recurs (e.g., after a VM reboot or a manual `nohup` start by someone), the same silent-crash-loop failure mode (see Bug #2) will reappear silently for hours. Consider adding a check (e.g., `ExecStartPre` port check, or a monitoring alert) to catch this automatically.
5. **`GEMINI_API_KEY` is revoked/leaked** (per a pre-existing code comment dated 2026-09-10) and the Gemini fast-path tier (tier 3 in `_run_pipeline_tiers`) is disabled. Not addressed this session; remains a live issue independent of everything above.
6. **IAM policy expansion requests will likely hit the same hard platform block.** Any future request to add IAM (`iam:*`) permissions to `agy-vm-readonly` or any similar unattended-agent credential should be expected to be blocked by the Claude Code safety classifier, as it was this session.
