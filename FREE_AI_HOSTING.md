# Free & Low-Cost AI Hosting Options

Consolidated notes from evaluating how to run AI models (local/open-weight and agentic
workflows) with as close to zero cost as possible, using this project's OCI setup as the
starting point.

## 1. What we already have (untouched)

- **`ai-orchestration-vm`** (OCI, `ap-hyderabad-1`, `VM.Standard.E2.1.Micro`, Always Free) —
  runs `server.py` (backend, systemd unit `ai-studio.service`) and the
  `oci-arm-harvester.service` (autonomous script polling for a free Ampere A1 ARM instance).
- **`sensex-bot`** (OCI, same region, same shape, Always Free) — separate bot, untouched.
- Harvester script: `/home/ubuntu/create_oci_arm_instance.py`, now alternates every attempt
  between a primary shape (1 OCPU/6GB) and a fallback shape (2 OCPU/12GB) via
  `--fallback-ocpus`/`--fallback-memory`, since smaller shape requests land more easily than
  a full 4 OCPU/24GB ask.

## 2. Always-Free compute across the 4 major clouds

| Provider | Always Free (perpetual)? | Specs | Notes |
|---|---|---|---|
| **OCI** | Yes | 4 OCPU/24GB Ampere A1 **or** 2× AMD Micro (1GB) | Both AMD Micro slots already used (`ai-orchestration-vm`, `sensex-bot`). ARM allowance still unclaimed — capacity-constrained. |
| **GCP** | Yes | `e2-micro`: 0.25 vCPU / 1GB | Genuinely free forever, but too small to run an agent + proxy + headless browser together |
| **AWS** | No | t2.micro, 12 months only | Not "always free" under current terms |
| **Azure** | No | B1s, 12 months only | Same — time-limited, not perpetual |

**Important policy change (confirmed, effective 2026-06-15):** Oracle cut the
Always-Free-*only* Ampere A1 allocation from 4 OCPU/24GB down to **2 OCPU/12GB**.
Pay-As-You-Go (PAYG) tenancies reportedly retain the full 4 OCPU/24GB eligibility (Oracle
support gave contradictory answers to different users, so not 100% guaranteed, but
corroborated by community tooling docs).

## 3. Getting the free ARM instance to actually provision

- Community consensus (GitHub: `hitrov/oci-arm-host-capacity`, `sam-bee/oracle-cloud-repeater`,
  `oeufmeister/oci-arm-host-capacity`): persistent retry loop is the only real method — no
  special trick beyond retry + jitter, which our harvester already does.
- **Multi-Availability-Domain rotation** helps a lot, but only in regions with multiple ADs
  (Ashburn, Phoenix, Frankfurt = 3 ADs each). **`ap-hyderabad-1` has only 1 AD**, a real
  structural disadvantage versus 3-AD regions.
- **Best regions reported for actually catching capacity:** Frankfurt (`eu-frankfurt-1`) and
  Singapore (`ap-singapore-1`) — reported to provision within minutes rather than days. US
  regions are the most saturated despite having 3 ADs each.
- **PAYG upgrade** is reported (not officially documented) to get priority over free-trial-only
  tenancies when capacity is allocated.
- Home region is **fixed at tenancy signup and cannot be changed later** — to get a different
  region you need a new, separate tenancy (see §7).

## 4. Cheapest GPU compute, normalized (~24GB VRAM tier, ~8 vCPU/32GB)

| Provider | Instance | GPU | $/hour |
|---|---|---|---|
| **GCP** | g2-standard-8 | 1× L4 (24GB) | **~$0.85** (cheapest of the 4 hyperscalers) |
| AWS | g5.2xlarge | 1× A10G (24GB) | $1.21 |
| Azure | NC8ads_A10_v4 | 1× A10 (22GB, partitioned) | $1.43 |
| OCI | VM.GPU.A10.1 | 1× A10 (24GB) | $2.00 (most expensive of the 4) |

Note: AWS/Azure/OCI all use the same A10 die — same raw performance between them. GCP's L4
is a different, newer chip with **lower memory bandwidth** than A10 (~300GB/s vs ~600GB/s) —
since LLM token generation is memory-bandwidth-bound, A10-based instances likely generate
tokens faster despite GCP being cheaper.

### Beyond the 4 hyperscalers — neoclouds (40-85% cheaper)

| Provider | ~24GB GPU | $/hour | SLA for commercial use |
|---|---|---|---|
| **RunPod (Secure Cloud)** | RTX 4090 | $0.34–0.69 | ✅ Formal SLA — best cost/reliability balance |
| RunPod (Community Cloud) | RTX 4090 | ~$0.34 | ❌ No SLA, third-party hosts |
| Vast.ai | Various | $0.18–0.35 | ❌ P2P marketplace, no uptime guarantee |
| TensorDock | RTX 3090/4090 | $0.35–0.40 | ⚠️ Variable by host |
| Lambda Labs | A100/H100 (bigger tier) | $1.49+ | ✅ 99.9% SLA, real support |
| Hyperstack | Various | from $0.15 | NVIDIA Cloud Partner, enterprise-oriented |

**Recommendation for a production/commercial workload:** RunPod Secure Cloud — cheapest
option with a real SLA.

### Serverless pay-per-token (⚠️ reintroduces per-usage API cost)

| Provider | Example rate | Notes |
|---|---|---|
| DeepInfra | Llama 3.3 70B: $0.10 in / $0.32 out per 1M tokens | Usually cheapest overall |
| Together AI | Llama 3.1 8B: $0.18/M | Enterprise SLA, prompt caching |
| Fireworks AI | Qwen 3.6 Plus: $0.50 in / $3.00 out | Fastest managed throughput |
| Groq | Llama 70B: $0.59 in / $0.79 out, ~394 tok/s | LPU hardware — speed over cost; **has a free tier** |
| Modal / Replicate | per-second billing | Not evaluated in depth, same category as RunPod Serverless |

### Hugging Face (managed layer, not independently cheap)

Inference Endpoints (T4 $0.50/hr, L4 $0.80/hr, A10G $1.00/hr) and Spaces GPU upgrades are
priced roughly at or above renting the same GPU directly from AWS/GCP — you're paying for
managed convenience, not a discount. **`ZeroGPU`** on Spaces gives Pro/Team accounts free
shared A100 access, but it's shared/queued, not suitable for guaranteed multi-user quota.

## 5. Which open-weight model fits which hardware

| Tier | Hardware | Model | Notes |
|---|---|---|---|
| CPU-only, 24GB RAM | OCI Ampere A1 (if harvested) | `gpt-oss-20b` | OpenAI's own open-weight model, designed for ~16GB, single-digit tok/s on CPU |
| 24GB GPU | RTX 4090 / L4 / A10 | `gpt-oss-20b` or Qwen3-Coder-30B-A3B (MoE) | Qwen3-Coder purpose-built for agentic/tool-use |
| 48–80GB GPU | A100/H100 80GB | `gpt-oss-120b`, Qwen3-Coder-Next (80B), GLM-4.5-Air (106B MoE) | All explicitly agent-trained |
| Frontier open (300B+) | Multi-GPU cluster only | GLM-5.2, DeepSeek-V4, Kimi K2.6/K3 | **Not feasible on a single GPU** |

`gpt-oss-120b` (OpenAI's own open-weight, ~80GB) is the closest "OpenAI-branded" model that
fits a single high-end GPU — `gpt-oss-120b`'s bigger sibling is the ceiling; nothing larger
from OpenAI is open-weight.

## 6. Getting "agy-like" (agentic + tool-use) behavior from an open model

`agy` (Antigravity CLI, used in `server.py`'s `AgyWarmSession`) isn't just a model call — it's
a persistent agent process with MCP tool execution, file writes, and a custom stream-json
protocol. A raw open-weight model can't replicate that alone; it needs an **agentic CLI
harness** wired to the same class of tools.

| Agentic CLI | MCP support | Local model support | Notes |
|---|---|---|---|
| **goose** (Block) | Native, first-class | Yes, built for Ollama | Best fit for this use case |
| **OpenCode** | Yes | Yes, multi-provider | Claude-Code-like TUI |
| Codex CLI (OpenAI) | Yes (recent) | Yes via custom base_url | |
| Aider | Limited | Yes via LiteLLM | More chat-focused than agent-focused |
| OpenHands | Yes | Yes | Heavier, Docker-based |

**Browser automation ("Claude in Chrome" equivalent):** MCP is a standard, not
Claude-specific. **Playwright MCP** (open-source, free, self-hosted) is confirmed compatible
with both goose and OpenCode — gives the same navigate/click/screenshot/read-page capability.

## 7. Zero-out-of-pocket architecture for scaling to 50–100 users

Key insight: a **flat hourly GPU rental bills 24/7 regardless of usage** — wrong model for
"pay only from money users already gave me." **Pay-per-token serverless is the correct shape**
for a zero-upfront, revenue-funded service, despite being the more expensive-per-token option
in isolation.

1. **Orchestration** — goose or OpenCode, running free on the existing `ai-orchestration-vm`
   (lightweight, just makes outbound API calls).
2. **Model compute** — Groq (free tier to pilot) → DeepInfra (cheapest per-token) once past
   free-tier limits. Both expose OpenAI-compatible endpoints goose/OpenCode can target instead
   of local Ollama.
3. **Quota + billing** — **LiteLLM Proxy**, also free on the same VM. Issues a virtual API key
   per user, enforces real-dollar budget caps (not just rate limits) per key — this is what
   makes "club charges from users" operationally real.
4. **Browser tool access** — Playwright MCP attached to the same goose/OpenCode instance.
5. **Payment collection** — Stripe/Razorpay, set up directly by the account owner (not
   something to automate) — require prepayment before issuing a budget-capped key, so usage
   never runs ahead of collected revenue.
6. **Caveat:** "zero from pocket" means no idle/fixed infrastructure cost — a payment method
   still needs to be on file with the serverless provider (Groq/DeepInfra) to cover metered
   usage, funded by money already collected from users.

## 8. Second OCI tenancy (in progress)

To get a genuinely isolated ARM VM (not touching the existing Hyderabad tenancy) with better
odds of catching capacity:

- New tenancy, new email (`keshavkarn1976@gmail.com`), **Home Region: Germany Central
  (Frankfurt)** — selected and confirmed during signup.
- ⚠️ **Blocked once already**: Oracle's duplicate-account detection flagged the signup at the
  payment step — reusing the same card as the existing tenancy triggered *"It looks like you
  already have an account using a different email address. Oracle allows one promotion per
  person."* Email alone doesn't bypass this; the **card** (and possibly phone number) also
  gets fingerprinted.
- Next attempt: restart the signup from scratch with a **different card** (and ideally a
  different phone number) to reduce duplicate-detection risk.

## 9. Bottom-line recommendations

- **For genuinely free, low-traffic use:** keep chasing the OCI Always-Free ARM harvester
  (Hyderabad + new Frankfurt attempt), run `gpt-oss-20b` once/if it lands.
- **For a small paid pilot with real users:** Groq free tier → DeepInfra serverless, fronted by
  goose/OpenCode + LiteLLM Proxy on the existing free VM — zero fixed cost, scales with
  collected revenue.
- **For serious, reliable production scale:** RunPod Secure Cloud (flat hourly, real SLA) once
  usage is predictable enough to justify a fixed cost instead of per-token billing.
