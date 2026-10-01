"""One-line Terraform + CI/CD engine for the AI Orchestration Studio.

"Terraform a web server on AWS with a CI/CD pipeline ..." becomes:

  1. a Terraform stack rendered from iac_templates/aws_web (VPC, subnet, IGW,
     route table, security group, EC2 web server) plus a guardrails policy,
  2. a new public GitHub repo holding that code and a GitHub Actions pipeline,
  3. a workflow_dispatch run: validate + security/cost gate -> plan -> apply ->
     smoke test -> auto-destroy timer, streamed stage by stage into the task log,
  4. a result card built from the run's ::notice annotations (plan, cost, url).

Terraform itself runs on GitHub's runners, never on the 1 GB production VM, and
reaches AWS through GitHub OIDC (no AWS keys stored anywhere). The one-time
AWS side is iac_templates/aws-bootstrap.yaml.

Modes:
  - full:       GitHub token + IAC_AWS_ROLE_ARN + IAC_STATE_BUCKET -> real AWS server.
  - validate:   GitHub token only -> real repo + real validate/scan/cost-gate job;
                plan/apply are reported as simulated.
  - simulated:  no GitHub token, or the prompt says "simulate"/"dry run" -> nothing
                is created; every stage is labelled SIMULATED.
"""
import asyncio
import html
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Callable, Optional

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "iac_templates", "aws_web")
REGISTRY_PATH = os.path.expanduser("~/.iac_stacks.json")
WORKFLOW_FILE = "terraform.yml"
REGION = os.environ.get("IAC_AWS_REGION", "us-east-1")

with open(os.path.join(TEMPLATE_DIR, "guardrails", "prices.json")) as _f:
    PRICING = json.load(_f)

RESOURCE_SUMMARY = "VPC, subnet, internet gateway, route table + association, security group, EC2 web server"
RESOURCE_COUNT = 7

# --------------------------------------------------------------------------- intent

_IAC_WORDS = ("terraform", "infrastructure as code", "iac stack")
_PIPELINE_WORDS = ("ci/cd", "cicd", "ci-cd", "devops pipeline", "github actions", "deployment pipeline")
_INFRA_WORDS = ("aws", "ec2", "server", "infrastructure", "infra")
_DESTROY_RE = re.compile(
    r"^\s*(?:please\s+)?(?:destroy|tear\s*down)\s+"
    r"(?:it|the\s+(?:stack|server|demo|demo\s+server|web\s+server|terraform\s+stack)|(iac-[a-z0-9-]+))\s*[.!]?\s*$"
)


_MEDIA_RE = re.compile(r"\b(?:video|reel|trailer|thumbnail|animation|voice-?over|storyboard|shot list)\b")


def is_iac_request(prompt_lower: str) -> bool:
    # A video/creative brief *about* the Terraform feature is not a deployment request.
    if _MEDIA_RE.search(prompt_lower):
        return False
    if any(w in prompt_lower for w in _IAC_WORDS):
        return True
    if any(w in prompt_lower for w in _PIPELINE_WORDS) and any(w in prompt_lower for w in _INFRA_WORDS):
        return True
    m = _DESTROY_RE.match(prompt_lower)
    return bool(m) and (m.group(1) is not None or _registry().get("last") is not None)


def parse_spec(prompt: str) -> dict:
    p = prompt.lower()
    m = re.search(r"\b([a-z][0-9][a-z]*\.(?:nano|micro|small|medium|large|[0-9]*xlarge))\b", p)
    instance_type = m.group(1) if m else "t4g.nano"
    ttl = 30
    if any(k in p for k in ("keep it", "no auto-destroy", "don't destroy", "do not destroy", "permanent")):
        ttl = 0
    else:
        t = re.search(r"(\d+)\s*(?:min|mins|minutes)\b", p)
        h = re.search(r"(\d+)\s*(?:hour|hours|hr|hrs)\b", p)
        if t:
            ttl = int(t.group(1))
        elif h:
            ttl = int(h.group(1)) * 60
        ttl = max(5, min(ttl, 240))
    return {
        "name": "iac-web-" + datetime.now(timezone.utc).strftime("%m%d-%H%M%S"),
        "region": REGION,
        "instance_type": instance_type,
        "ttl_minutes": ttl,
        "simulate": any(k in p for k in ("simulate", "simulation", "dry run", "dry-run")),
    }


def monthly_cost(instance_type: str) -> Optional[float]:
    hourly = PRICING["instance_usd_per_hour"].get(instance_type)
    if hourly is None:
        return None
    hours = PRICING["hours_per_month"]
    return hourly * hours + PRICING["root_volume_gb"] * PRICING["ebs_gp3_usd_per_gb_month"] + PRICING["public_ipv4_usd_per_hour"] * hours


def gate_failures(instance_type: str) -> list:
    cost = monthly_cost(instance_type)
    out = []
    if instance_type not in PRICING["allowed_instance_types"]:
        out.append(f"{instance_type} is not on the allow-list")
    if cost is None:
        out.append(f"no price data for {instance_type}")
    elif cost > PRICING["max_monthly_usd"]:
        out.append(f"${cost:.2f}/month exceeds the ${PRICING['max_monthly_usd']}/month cap")
    return out

# --------------------------------------------------------------------------- rendering


def _index_html(spec: dict, prompt: str) -> str:
    created = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{spec['name']}</title>
<style>
body{{margin:0;font:16px/1.6 system-ui,sans-serif;background:#0f172a;color:#e2e8f0;display:grid;place-items:center;min-height:100vh}}
main{{max-width:640px;padding:2rem}}h1{{font-size:2rem;margin:0 0 .5rem;color:#38bdf8}}
.q{{border-left:4px solid #38bdf8;padding:.5rem 1rem;background:#1e293b;border-radius:6px;font-style:italic}}
table{{border-collapse:collapse;margin-top:1rem;width:100%}}td{{padding:.35rem .5rem;border-bottom:1px solid #334155}}td:first-child{{color:#94a3b8}}
</style></head><body><main>
<h1>Deployed by Terraform from one prompt</h1>
<p class="q">{html.escape(prompt)}</p>
<table>
<tr><td>Stack</td><td>{spec['name']}</td></tr>
<tr><td>Instance</td><td>__IID__ (__ITYPE__)</td></tr>
<tr><td>Availability zone</td><td>__AZ__</td></tr>
<tr><td>Booted</td><td>__BOOTED__</td></tr>
<tr><td>Code generated</td><td>{created}</td></tr>
<tr><td>Auto-destroy</td><td>{f"{spec['ttl_minutes']} minutes after deploy" if spec['ttl_minutes'] else "off"}</td></tr>
</table>
<p>Built by AI Orchestration Studio &rarr; GitHub Actions &rarr; AWS ({spec['region']}).</p>
</main></body></html>
"""


def _readme(spec: dict, prompt: str) -> str:
    return f"""# {spec['name']}

Generated by **AI Orchestration Studio** from one prompt:

> {prompt}

| | |
|---|---|
| Cloud / Region | AWS / `{spec['region']}` |
| Instance type | `{spec['instance_type']}` |
| Resources | {RESOURCE_SUMMARY} |
| Auto-destroy | {f"{spec['ttl_minutes']} minutes after deploy" if spec['ttl_minutes'] else "off"} |

## Pipeline (`.github/workflows/terraform.yml`)

1. **Validate and scan** - `terraform fmt`, `validate`, security rules and the cost gate (`guardrails/policy.py`). No AWS access.
2. **Plan and deploy** - OIDC into AWS (no stored keys), `plan`, cost gate on the real plan, `apply`, HTTP smoke test.
3. **Auto-destroy timer** - waits the TTL, then `terraform destroy`.

Run it by hand from the Actions tab: *terraform* > *Run workflow* > `apply` / `destroy`.
"""


def render_stack(spec: dict, prompt: str) -> dict:
    """Returns {repo path: file content} for the whole stack."""
    files = {}
    for root, _dirs, names in os.walk(TEMPLATE_DIR):
        for n in names:
            full = os.path.join(root, n)
            rel = os.path.relpath(full, TEMPLATE_DIR).replace(os.sep, "/")
            if "__pycache__" in rel:
                continue
            with open(full, encoding="utf-8") as f:
                files[rel] = f.read()
    files["terraform.tfvars"] = (
        f'name          = "{spec["name"]}"\n'
        f'region        = "{spec["region"]}"\n'
        f'instance_type = "{spec["instance_type"]}"\n'
        f'ttl_minutes   = {spec["ttl_minutes"]}\n'
    )
    files["web/index.html"] = _index_html(spec, prompt)
    files["README.md"] = _readme(spec, prompt)
    files[".gitignore"] = ".terraform/\n*.tfstate*\ntfplan\nplan.json\n"
    return files

# --------------------------------------------------------------------------- GitHub


class GitHubError(Exception):
    def __init__(self, status, body):
        super().__init__(f"GitHub HTTP {status}: {body[:300]}")
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHub:
    API = "https://api.github.com"

    def __init__(self, token: str):
        self.token = token

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-orchestration-studio",
        }

    def req(self, method: str, path: str, body=None):
        url = path if path.startswith("http") else self.API + path
        data = json.dumps(body).encode() if body is not None else None
        headers = self._headers()
        if data is not None:
            headers["Content-Type"] = "application/json"
        r = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                txt = resp.read().decode("utf-8", "replace")
                return json.loads(txt) if txt.strip() else {}
        except urllib.error.HTTPError as e:
            raise GitHubError(e.code, e.read().decode("utf-8", "replace"))

    def login(self) -> str:
        return self.req("GET", "/user")["login"]

    def create_repo(self, name: str, description: str) -> dict:
        return self.req("POST", "/user/repos", {"name": name, "description": description, "private": False, "auto_init": True})

    def commit_files(self, owner: str, repo: str, branch: str, files: dict, message: str) -> str:
        """One commit with every file (git data API). auto_init's first commit can
        take a few seconds to appear, so the ref lookup is retried."""
        head = None
        for _ in range(10):
            try:
                head = self.req("GET", f"/repos/{owner}/{repo}/git/ref/heads/{branch}")["object"]["sha"]
                break
            except GitHubError as e:
                if e.status not in (404, 409):
                    raise
                time.sleep(1.5)
        if head is None:
            raise GitHubError(404, f"branch {branch} never appeared in {owner}/{repo}")
        base_tree = self.req("GET", f"/repos/{owner}/{repo}/git/commits/{head}")["tree"]["sha"]
        tree = self.req("POST", f"/repos/{owner}/{repo}/git/trees", {
            "base_tree": base_tree,
            "tree": [{"path": p, "mode": "100644", "type": "blob", "content": c} for p, c in files.items()],
        })["sha"]
        commit = self.req("POST", f"/repos/{owner}/{repo}/git/commits", {"message": message, "tree": tree, "parents": [head]})["sha"]
        self.req("PATCH", f"/repos/{owner}/{repo}/git/refs/heads/{branch}", {"sha": commit})
        return commit

    def set_variable(self, owner: str, repo: str, name: str, value: str):
        try:
            self.req("POST", f"/repos/{owner}/{repo}/actions/variables", {"name": name, "value": value})
        except GitHubError as e:
            if e.status != 409:
                raise
            self.req("PATCH", f"/repos/{owner}/{repo}/actions/variables/{name}", {"name": name, "value": value})

    def dispatch(self, owner: str, repo: str, branch: str, inputs: dict):
        # A just-pushed workflow can take a few seconds to become dispatchable.
        last = None
        for _ in range(15):
            try:
                self.req("POST", f"/repos/{owner}/{repo}/actions/workflows/{WORKFLOW_FILE}/dispatches", {"ref": branch, "inputs": inputs})
                return
            except GitHubError as e:
                if e.status not in (404, 422):
                    raise
                last = e
                time.sleep(2)
        raise last

    def find_dispatched_run(self, owner: str, repo: str, since: float) -> Optional[dict]:
        runs = self.req("GET", f"/repos/{owner}/{repo}/actions/runs?event=workflow_dispatch&per_page=10").get("workflow_runs", [])
        for run in runs:
            created = datetime.strptime(run["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
            if created >= since - 10:
                return run
        return None

    def jobs(self, owner: str, repo: str, run_id: int) -> list:
        return self.req("GET", f"/repos/{owner}/{repo}/actions/runs/{run_id}/jobs").get("jobs", [])

    def annotations(self, check_run_url: str) -> list:
        try:
            return self.req("GET", check_run_url + "/annotations")
        except GitHubError:
            return []

    def job_log_tail(self, owner: str, repo: str, job_id: int, lines: int = 25) -> str:
        """The logs endpoint 302s to a signed blob URL that rejects our auth header,
        so follow the redirect by hand without it."""
        opener = urllib.request.build_opener(_NoRedirect)
        r = urllib.request.Request(f"{self.API}/repos/{owner}/{repo}/actions/jobs/{job_id}/logs", headers=self._headers())
        try:
            opener.open(r, timeout=20)
            return ""
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location")
            if not loc:
                return ""
        try:
            with urllib.request.urlopen(loc, timeout=20) as resp:
                text = resp.read().decode("utf-8", "replace")
        except Exception:
            return ""
        tail = [re.sub(r"^\S+Z ", "", ln) for ln in text.splitlines() if ln.strip()][-lines:]
        return "\n".join(tail)

# --------------------------------------------------------------------------- registry


def _registry() -> dict:
    try:
        with open(REGISTRY_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_stack(stack: dict):
    reg = _registry()
    reg.setdefault("stacks", {})[stack["name"]] = stack
    reg["last"] = stack["name"]
    try:
        with open(REGISTRY_PATH, "w") as f:
            json.dump(reg, f, indent=2)
    except Exception:
        pass

# --------------------------------------------------------------------------- orchestration

_STAGE_ICONS = {
    "Format check": "🧹", "Init (no backend)": "📦", "Validate": "✅", "Security and cost policy": "🛡️",
    "AWS credentials (OIDC)": "🔐", "Init": "📦", "Plan": "📋", "Cost and policy gate": "💰",
    "Apply": "🚀", "Smoke test": "🌐", "Wait": "⏳", "Destroy": "🧨",
}


class _Clock:
    def __init__(self, on_log: Callable[[str], None]):
        self.t0 = time.time()
        self.on_log = on_log

    def log(self, msg: str):
        s = int(time.time() - self.t0)
        self.on_log(f"[{s // 60:02d}:{s % 60:02d}] {msg}")


async def _follow_run(gh: GitHub, owner: str, repo: str, since: float, clock: _Clock, timeout: int = 900) -> dict:
    """Streams step completions into the log until the deploy job finishes (the
    auto-destroy job keeps the run open for the TTL, so the run itself is not
    waited on), validate fails, or the run ends."""
    loop = asyncio.get_event_loop()
    run = None
    deadline = time.time() + 90
    while run is None and time.time() < deadline:
        run = await loop.run_in_executor(None, gh.find_dispatched_run, owner, repo, since)
        if run is None:
            await asyncio.sleep(3)
    if run is None:
        raise RuntimeError("the pipeline run never appeared on GitHub")
    clock.log(f"▶️ Pipeline run #{run['run_number']} started: {run['html_url']}")

    seen, started_jobs = set(), set()
    deadline = time.time() + timeout
    while time.time() < deadline:
        jobs = await loop.run_in_executor(None, gh.jobs, owner, repo, run["id"])
        by_name = {j["name"]: j for j in jobs}
        for j in jobs:
            if j["status"] != "queued" and j["id"] not in started_jobs:
                started_jobs.add(j["id"])
                clock.log(f"⚙️ Job started: {j['name']}")
            for st in j.get("steps", []):
                key = (j["id"], st["number"])
                if st["status"] == "completed" and key not in seen and st["conclusion"] != "skipped":
                    seen.add(key)
                    if st["name"].startswith(("Set up job", "Complete job", "Post ", "Run actions/", "Run hashicorp/", "Run aws-actions/")):
                        continue
                    ok = st["conclusion"] == "success"
                    icon = _STAGE_ICONS.get(st["name"], "•")
                    clock.log(f"{icon} {st['name']}: {'passed' if ok else st['conclusion'].upper()}")
        v, d = by_name.get("Validate and scan"), by_name.get("Plan and deploy")
        if v and v["status"] == "completed" and v["conclusion"] != "success":
            return {"run": run, "jobs": jobs, "failed_job": v}
        if d and d["status"] == "completed":
            # skipped = AWS vars not set on the repo (validate-only mode)
            failed = d if d["conclusion"] not in ("success", "skipped") else None
            return {"run": run, "jobs": jobs, "failed_job": failed}
        if v and v["status"] == "completed" and not d:
            # deploy job not created at all (vars missing) -> run is effectively done
            run_now = await loop.run_in_executor(None, gh.req, "GET", f"/repos/{owner}/{repo}/actions/runs/{run['id']}")
            if run_now.get("status") == "completed":
                return {"run": run, "jobs": jobs, "failed_job": None}
        await asyncio.sleep(4)
    raise RuntimeError(f"pipeline still running after {timeout // 60} minutes: {run['html_url']}")


def _collect_notices(gh: GitHub, jobs: list) -> dict:
    out = {"errors": []}
    for j in jobs:
        if j.get("conclusion") in (None, "skipped"):
            continue
        for a in gh.annotations(j["check_run_url"]):
            title, msg = (a.get("title") or "").lower(), a.get("message") or ""
            if a.get("annotation_level") == "failure":
                out["errors"].append(msg)
            elif title in ("plan", "cost", "security", "url", "ttl"):
                out[title] = msg
    return out


def _resource_graph() -> str:
    return (
        "```mermaid\ngraph LR\n"
        "    U((Internet)) -->|HTTP 80| SG[Security group]\n"
        "    IGW[Internet gateway] --> RT[Route table]\n"
        "    RT --> SN[Public subnet]\n"
        "    VPC[VPC 10.42.0.0/16] --> SN\n"
        "    VPC --> IGW\n"
        "    SN --> EC2[EC2 web server]\n"
        "    SG --> EC2\n"
        "```"
    )


def _card(spec, prompt, stages, *, headline, repo_url=None, run_url=None, live_url=None, simulated=False, files=None, extra=""):
    rows = "\n".join(f"| {n} | {s} | {d} |" for n, s, d in stages)
    parts = [f"### 🏗️ Terraform + CI/CD: `{spec['name']}` — {headline}"]
    if simulated:
        parts.append("> 🧪 **SIMULATED** stages below did not touch AWS — nothing was created for them.")
    links = []
    if live_url:
        links.append(f"🌐 **Live URL:** {live_url}")
    if repo_url:
        links.append(f"📁 **Repo:** {repo_url}")
    if run_url:
        links.append(f"⚙️ **Pipeline run:** {run_url}")
    if links:
        parts.append("  \n".join(links))
    parts.append("| Stage | Result | Detail |\n|---|---|---|\n" + rows)
    if extra:
        parts.append(extra)
    parts.append("#### Resource graph\n" + _resource_graph())
    if files:
        parts.append("<details><summary>main.tf</summary>\n\n```hcl\n" + files["main.tf"] + "\n```\n</details>")
    return "\n\n".join(parts)


async def _simulate(spec, prompt, files, clock, reason) -> dict:
    cost = monthly_cost(spec["instance_type"])
    cost_txt = f"${cost:.2f}/month" if cost is not None else "unknown"
    blocked = gate_failures(spec["instance_type"])
    clock.log(f"🧪 Simulation mode ({reason}) — nothing will be created")
    for msg in ("🧹 Format check: passed", "✅ Validate: passed", "🛡️ Security and cost policy: 0 blocking findings"):
        await asyncio.sleep(0.6)
        clock.log(f"{msg} (simulated)")
    stages = [
        ("1 · Generate Terraform", "✅", f"{RESOURCE_COUNT} resources: {RESOURCE_SUMMARY}"),
        ("2 · GitHub repo + pipeline", "🧪 simulated", reason),
        ("3 · Validate & security scan", "🧪 simulated", "fmt, validate, no SSH, IMDSv2, encrypted disk"),
    ]
    if blocked:
        clock.log(f"💰 Cost gate: BLOCKED — {'; '.join(blocked)} (simulated)")
        stages.append(("4 · Cost gate", "⛔ blocked", f"{spec['instance_type']}: {cost_txt}; " + "; ".join(blocked)))
        return {"markdown": _card(spec, prompt, stages, headline="⛔ Blocked by cost gate", simulated=True, files=files),
                "deliverable": {"type": "info", "title": f"⛔ {spec['name']}: blocked by cost gate", "url": "#"}}
    await asyncio.sleep(0.8)
    clock.log(f"📋 Plan: {RESOURCE_COUNT} to add, 0 to change, 0 to destroy (simulated)")
    clock.log(f"💰 Cost gate: {cost_txt} — under the ${PRICING['max_monthly_usd']} cap (simulated)")
    stages += [
        ("4 · Plan", "🧪 simulated", f"Plan: {RESOURCE_COUNT} to add, 0 to change, 0 to destroy"),
        ("5 · Cost gate", "🧪 simulated", f"{cost_txt} (cap ${PRICING['max_monthly_usd']})"),
        ("6 · Apply + smoke test", "🧪 simulated", "no server created"),
        ("7 · Auto-destroy", "🧪 simulated", f"{spec['ttl_minutes']} min" if spec["ttl_minutes"] else "off"),
    ]
    return {"markdown": _card(spec, prompt, stages, headline="🧪 Simulated", simulated=True, files=files),
            "deliverable": {"type": "info", "title": f"🧪 {spec['name']} (simulated)", "url": "#"}}


async def _create(prompt, clock, github_user, github_token) -> dict:
    loop = asyncio.get_event_loop()
    spec = parse_spec(prompt)
    clock.log(f"🏗️ Terraform request recognized — stack {spec['name']}, {spec['instance_type']} in {spec['region']}")
    files = render_stack(spec, prompt)
    clock.log(f"📝 Generated {len(files)} files: main.tf ({RESOURCE_COUNT} resources), pipeline, guardrails")
    cost = monthly_cost(spec["instance_type"])
    if cost is not None:
        clock.log(f"💰 Pre-flight estimate: ${cost:.2f}/month for {spec['instance_type']}")

    token = github_token or os.environ.get("GITHUB_TOKEN")
    if spec["simulate"]:
        return await _simulate(spec, prompt, files, clock, "requested in the prompt")
    if not token:
        return await _simulate(spec, prompt, files, clock, "no GitHub token on the server or in the request")

    role, bucket = os.environ.get("IAC_AWS_ROLE_ARN", ""), os.environ.get("IAC_STATE_BUCKET", "")
    aws_ready = bool(role and bucket)
    gh = GitHub(token)
    owner = await loop.run_in_executor(None, gh.login)
    # GitHub rejects descriptions with control characters (newlines, tabs) -- flatten the prompt.
    one_line = " ".join(prompt.split())
    repo = await loop.run_in_executor(None, gh.create_repo, spec["name"], f"Terraform stack generated from one prompt: {one_line[:120]}")
    branch = repo.get("default_branch") or "main"
    clock.log(f"📁 Created repo {repo['html_url']}")
    await loop.run_in_executor(None, gh.commit_files, owner, spec["name"], branch, files,
                               "Terraform stack + pipeline from AI Orchestration Studio [skip ci]")
    clock.log("⬆️ Pushed Terraform code, pipeline and guardrails in one commit")
    if aws_ready:
        await loop.run_in_executor(None, gh.set_variable, owner, spec["name"], "AWS_ROLE_ARN", role)
        await loop.run_in_executor(None, gh.set_variable, owner, spec["name"], "TF_STATE_BUCKET", bucket)
        clock.log("🔐 Pipeline wired to AWS through OIDC (no keys stored)")
    else:
        clock.log("⚠️ IAC_AWS_ROLE_ARN / IAC_STATE_BUCKET not set — pipeline will validate only; plan/apply simulated")

    since = time.time()
    action = "apply" if aws_ready else "plan"
    await loop.run_in_executor(None, gh.dispatch, owner, spec["name"], branch,
                               {"action": action, "ttl_minutes": str(spec["ttl_minutes"])})
    clock.log(f"🚦 Dispatched pipeline (action={action})")
    result = await _follow_run(gh, owner, spec["name"], since, clock)
    notes = await loop.run_in_executor(None, _collect_notices, gh, result["jobs"])
    run_url = result["run"]["html_url"]
    stack = {"name": spec["name"], "owner": owner, "branch": branch, "repo_url": repo["html_url"], "run_url": run_url,
             "instance_type": spec["instance_type"], "ttl_minutes": spec["ttl_minutes"], "created": time.time(),
             "aws": aws_ready, "url": notes.get("url")}
    _save_stack(stack)

    validate_ok = not (result["failed_job"] and result["failed_job"]["name"] == "Validate and scan")
    stages = [
        ("1 · Generate Terraform", "✅", f"{RESOURCE_COUNT} resources: {RESOURCE_SUMMARY}"),
        ("2 · GitHub repo + pipeline", "✅", f"[{owner}/{spec['name']}]({repo['html_url']})"),
        ("3 · Validate & security scan", "✅" if validate_ok else "❌", notes.get("security", "")),
    ]
    extra = ""
    if result["failed_job"]:
        tail = await loop.run_in_executor(None, gh.job_log_tail, owner, spec["name"], result["failed_job"]["id"])
        errs = "; ".join(notes["errors"]) or "see log"
        if validate_ok:
            stages.append(("4 · Plan & deploy", "❌", errs))
        else:
            stages.append(("4 · Cost / policy gate", "⛔ blocked", f"{notes.get('cost', '')} — {errs}"))
        if tail:
            extra = "<details><summary>Failing step log (last lines)</summary>\n\n```\n" + tail + "\n```\n</details>"
        blocked = any("allow-list" in e or "cap" in e for e in notes["errors"])
        headline = "⛔ Blocked by cost gate" if blocked else "❌ Pipeline failed"
        clock.log(f"{'⛔' if blocked else '❌'} {headline}: {errs}")
        return {"markdown": _card(spec, prompt, stages, headline=headline, repo_url=repo["html_url"], run_url=run_url, files=files, extra=extra),
                "deliverable": {"type": "info", "title": f"{'⛔' if blocked else '❌'} {spec['name']}: {headline[2:]}", "url": run_url}}

    if not aws_ready:
        stages += [
            ("4 · Plan", "🧪 simulated", f"Plan: {RESOURCE_COUNT} to add (AWS not connected yet)"),
            ("5 · Cost gate", "✅", notes.get("cost", "")),
            ("6 · Apply + smoke test", "🧪 simulated", "set IAC_AWS_ROLE_ARN and IAC_STATE_BUCKET to deploy for real"),
        ]
        clock.log("💎 Pipeline validated for real; AWS stages simulated")
        return {"markdown": _card(spec, prompt, stages, headline="✅ Validated (AWS stages simulated)", repo_url=repo["html_url"], run_url=run_url, simulated=True, files=files),
                "deliverable": {"type": "info", "title": f"🏗️ {spec['name']}: validated", "url": repo["html_url"]}}

    live = notes.get("url")
    stages += [
        ("4 · Plan", "✅", notes.get("plan", "")),
        ("5 · Cost gate", "✅", notes.get("cost", "")),
        ("6 · Apply", "✅", f"{spec['instance_type']} in {spec['region']}"),
        ("7 · Smoke test", "✅", live or ""),
        ("8 · Auto-destroy", "⏳" if spec["ttl_minutes"] else "off",
         f"in {spec['ttl_minutes']} min (or say “destroy it”)" if spec["ttl_minutes"] else "say “destroy it” when done"),
    ]
    clock.log(f"💎 Live: {live}")
    return {"markdown": _card(spec, prompt, stages, headline="✅ Live", repo_url=repo["html_url"], run_url=run_url, live_url=live, files=files),
            "deliverable": {"type": "info", "title": f"🌐 {spec['name']} is live", "url": live or repo["html_url"]}}


async def _destroy(prompt_lower, clock, github_token) -> dict:
    loop = asyncio.get_event_loop()
    reg = _registry()
    m = _DESTROY_RE.match(prompt_lower)
    name = (m.group(1) if m else None) or reg.get("last")
    stack = reg.get("stacks", {}).get(name)
    if not stack:
        return {"markdown": f"⚠️ No Terraform stack named `{name}` was created by this Studio.", "deliverable": None}
    if not stack.get("aws"):
        clock.log(f"🧪 {name} never reached AWS — nothing to destroy")
        return {"markdown": f"🧪 `{name}` was simulated / validate-only, so there is nothing on AWS to destroy.",
                "deliverable": {"type": "info", "title": f"🧪 {name}: nothing to destroy", "url": stack.get("repo_url", "#")}}
    token = github_token or os.environ.get("GITHUB_TOKEN")
    gh = GitHub(token)
    owner = stack["owner"]
    clock.log(f"🧨 Destroying {name} through its pipeline")
    since = time.time()
    await loop.run_in_executor(None, gh.dispatch, owner, name, stack.get("branch", "main"), {"action": "destroy", "ttl_minutes": "0"})
    result = await _follow_run(gh, owner, name, since, clock)
    notes = await loop.run_in_executor(None, _collect_notices, gh, result["jobs"])
    ok = result["failed_job"] is None
    clock.log(f"{'💎' if ok else '❌'} {notes.get('plan', '')}")
    md = (f"### 🧨 `{name}` {'destroyed' if ok else 'destroy FAILED'}\n\n"
          f"{notes.get('plan', '')}\n\n⚙️ **Pipeline run:** {result['run']['html_url']}\n\n"
          + ("Cost back to $0 for this stack. The repo is kept as a record." if ok else "Check the run log; resources may still exist."))
    return {"markdown": md, "deliverable": {"type": "info", "title": f"🧨 {name} {'destroyed' if ok else 'destroy failed'}", "url": result["run"]["html_url"]}}


async def run_iac_mission(prompt: str, on_log: Callable[[str], None], github_user: Optional[str] = None,
                          github_token: Optional[str] = None) -> dict:
    clock = _Clock(on_log)
    try:
        if _DESTROY_RE.match(prompt.lower()):
            return await _destroy(prompt.lower(), clock, github_token)
        return await _create(prompt, clock, github_user, github_token)
    except GitHubError as e:
        hint = ""
        if e.status in (401, 403):
            hint = " The GitHub token needs the `repo` and `workflow` scopes."
        elif e.status == 422 and "already exists" in str(e):
            hint = " (A repo with that name already exists.)"
        clock.log(f"❌ GitHub error: {e}")
        return {"markdown": f"❌ GitHub rejected the request: {e}.{hint}", "deliverable": None}
    except Exception as e:
        clock.log(f"❌ {e}")
        return {"markdown": f"❌ Terraform pipeline error: {e}", "deliverable": None}
