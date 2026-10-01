"""Security + cost gate for the generated Terraform stack.

    python3 guardrails/policy.py static           # no AWS access needed: reads *.tf + terraform.tfvars
    python3 guardrails/policy.py plan plan.json   # reads `terraform show -json tfplan`

Results are emitted as GitHub Actions ::notice / ::error annotations (titles:
security, plan, cost, policy) -- the AI Orchestration Studio reads those back
from the job's check run to build its result card. Exit code 1 blocks the pipeline.
"""
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "prices.json")) as f:
    CFG = json.load(f)

ALLOWED = CFG["allowed_instance_types"]
MAX_MONTHLY = float(os.environ.get("MAX_MONTHLY_USD") or CFG["max_monthly_usd"])


def annotate(level, title, msg):
    print(f"::{level} title={title}::{msg}")


def summary(line):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as f:
            f.write(line + "\n")


def monthly_cost(instance_type):
    hourly = CFG["instance_usd_per_hour"].get(instance_type)
    if hourly is None:
        return None
    hours = CFG["hours_per_month"]
    return hourly * hours + CFG["root_volume_gb"] * CFG["ebs_gp3_usd_per_gb_month"] + CFG["public_ipv4_usd_per_hour"] * hours


def cost_gate(instance_type):
    """Returns a list of blocking failures for this instance type."""
    failures = []
    cost = monthly_cost(instance_type)
    if cost is None:
        annotate("error", "cost", f"No price data for {instance_type}; cannot estimate cost")
        return [f"unknown instance type {instance_type}"]
    annotate("notice", "cost", f"${cost:.2f}/month est. ({instance_type} + 8 GB gp3 + public IPv4, us-east-1 on-demand)")
    summary(f"- **Cost:** ${cost:.2f}/month est. for `{instance_type}` (cap ${MAX_MONTHLY:.0f})")
    if instance_type not in ALLOWED:
        failures.append(f"{instance_type} is not on the allow-list ({', '.join(ALLOWED)})")
    if cost > MAX_MONTHLY:
        failures.append(f"${cost:.2f}/month exceeds the ${MAX_MONTHLY:.0f}/month cap")
    return failures


def static():
    src = "\n".join(open(p).read() for p in sorted(glob.glob("*.tf")))
    tfvars = open("terraform.tfvars").read()
    m = re.search(r'instance_type\s*=\s*"([^"]+)"', tfvars)
    instance_type = m.group(1) if m else "t4g.nano"

    failures = []
    if re.search(r"from_port\s*=\s*22\b", src):
        failures.append("SSH (port 22) is open")
    if not re.search(r'http_tokens\s*=\s*"required"', src):
        failures.append("IMDSv2 is not enforced (http_tokens must be \"required\")")
    if not re.search(r"encrypted\s*=\s*true", src):
        failures.append("root volume is not encrypted")
    info = "HTTP 80 open to the internet (intended: public demo page)"

    annotate("notice", "security", f"{len(failures)} blocking finding(s); 1 informational: {info}")
    summary(f"- **Security:** {len(failures)} blocking, 1 informational ({info})")
    failures += cost_gate(instance_type)
    for f in failures:
        annotate("error", "policy", f)
    return 1 if failures else 0


def plan(path):
    data = json.load(open(path))
    adds = changes = destroys = 0
    instance_type = None
    for rc in data.get("resource_changes", []):
        actions = rc["change"]["actions"]
        if "create" in actions:
            adds += 1
        if "delete" in actions:
            destroys += 1
        if actions == ["update"]:
            changes += 1
        after = rc["change"].get("after") or {}
        if rc["type"] == "aws_instance" and after.get("instance_type"):
            instance_type = after["instance_type"]

    line = f"Plan: {adds} to add, {changes} to change, {destroys} to destroy"
    annotate("notice", "plan", line)
    summary(f"- **{line}**")
    if adds == 0 and changes == 0:
        return 0  # pure destroy / no-op: nothing to gate
    failures = cost_gate(instance_type) if instance_type else []
    for f in failures:
        annotate("error", "policy", f)
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "static":
        sys.exit(static())
    if len(sys.argv) >= 3 and sys.argv[1] == "plan":
        sys.exit(plan(sys.argv[2]))
    print(__doc__)
    sys.exit(2)
