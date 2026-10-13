# INT-FLOWOPS-PEER-PROFILE: explicit no-spend refusal

Local adapter version: `0.2.0-contracts.2`. Canonical Contracts remains pinned at `b55d1a9dc5d2bab23817b984b9801ccff7304f04` (release 1.0.0 / wire 1.0). FlowOps caller source: `9e57bd7dfa6dc8eb7f11249b32e77fa70e438945`. The exact tested AgentOps SHA and receipts are recorded below after verification. Production remains on the remediated 0.1.3 image.

## Profile and response

`GET /inference/capabilities` publishes the app-owned `agentops-peer-profiles-1` discovery document. It distinguishes executable synthetic routing from the **refusal-only** profile `flowops-review-no-spend-refusal-1`. It is not a permission grant and does not modify a canonical schema.

The refusal profile matches FlowOps' unchanged test request: application flowops; environment test; SANDBOX_DETERMINISTIC; task `order_resolution_review`; HIGH risk; PUBLIC data; STRUCTURED_OUTPUT; region `eu-test`; deadline 2,000 ms; input/output caps 1,024/256; EUR currency; maximum cost zero; policy `flowops-sandbox-no-spend-1`; no extensions. `context_refs` must equal `[trace_id]` and `operation_id` must equal `inference:<trace_id>`. Tenant/environment/application and inference:request permission are checked against private credentials before matching this profile.

The response is HTTP 200 with a canonical `route-decision` whose outcome is **POLICY_BLOCKED**, fallback_kind HUMAN_REVIEW, selected_deployment null, and attempts empty. Operation, trace, correlation, mode and policy are echoed unchanged. Usage is NONE; all token and provider-call counts are zero; cost is NONE, zero **EUR**. `X-AgentOps-Capability-Profile` identifies this refusal profile. HTTP 200 means that the routing decision was evaluated; it does not mean successful inference, approved business action or completed review. The caller must inspect `outcome`.

No risk downgrade, context stripping, currency conversion, region substitution, policy substitution, context dereference, model selection, budget reservation, provider call, run, event or usage record occurs. The caller retains ownership of deterministic business checks or human review. The implementation does not execute either fallback. The untouched FlowOps PeerClient can validate this refusal and retain its original constraints.

Changed profiles keep the existing typed errors; there is no retry with relaxed constraints. Incorrect identity or scope returns 401/403. Unknown HIGH/context-bearing/currency/region profiles still return typed 403 POLICY_BLOCKED. Malformed bounds/tasks use 422; unavailable inference modes use 503. The public supported-routing profile continues to use its existing LOW/MEDIUM, synthetic USD policy.

This refusal is stateless except for admission counters: identical requests return the same deterministic response; there is no persistent operation or ledger record to reconcile. Global/per-caller rate limits still apply, including repeated refusals (429/Retry-After). No X-AgentOps-Run-ID is returned for this profile. If future work adds a selectable model or side effect, it must go through the durable operation/worker/budget path rather than extending this refusal shortcut.

## Verification and reproduction

The example is `examples/contracts-v1/flowops-review-refusal.json`. The regression suite verifies the original constraints, zero side effects, deterministic repeats, caller authentication, rate limits and rejection of modified profiles.

Run the peer drill from AgentOps, supplying existing local FlowOps checkout/runtime paths:

```sh
uv run python scripts/verify_flowops_peer.py \
  --flowops-repo /path/to/flowops \
  --flowops-python /path/to/flowops/.venv/bin/python \
  --output work/flowops-peer-receipt.json
```

The script reads `git archive 9e57bd7... flowops` from the caller repository into an AgentOps-owned temporary directory. It runs that exact unmodified PeerClient with FlowOps' installed interpreter, an unpersisted synthetic Run and an explicit private PeerGrant. The client uses actual loopback HTTP to a temporary AgentOps Uvicorn server with fresh private credentials and SQLite storage. No transport is mocked. It checks the canonical response, repeat, caller-side denial without scope, and zero AgentOps runs/attempts/events/budget usage. Credentials pass through stdin and private temporary files, never command arguments or committed receipts. Temporary processes and files are removed when the script finishes. No other project's checkout is edited.

This proves peer-library compatibility for a refused inference. It does not prove a persisted FlowOps worker invokes this client or that the default workflow is connected. The integration finding can be marked compatible at the refusal boundary only after independent verification; model-backed review remains unavailable.

## Signed usage path remains blocked

**BLOCKED_NOT_IMPLEMENTED.** No outbox or automatic delivery is enabled. This particular refusal produces no usage record to send. Existing successful synthetic routes still expose canonical per-attempt usage payloads through authenticated GET endpoints only.

The pinned `cross-project-event` schema requires `ai.usage.recorded.v1` audience **agentops**. A delivery edge therefore needs a caller-owned outbox, AgentOps' authenticated usage inbox, trusted application/environment/tenant/key binding, the usage:write grant, atomic deduplication/accounting, retention and receipt reconciliation. It cannot be replaced with an arbitrary outbound AgentOps sender or by signing a payload and claiming delivery. The current AgentOps build has no such inbox/outbox and no approved receiver/signing configuration. These are an explicit separate implementation gap; keys, receipts and delivery success are not fabricated. No `/events` endpoint is advertised.

No deployment, provider enablement, shared proxy change, publication, production credentials or paid calls are part of this patch. Use the existing private startup instructions in `CONTRACTS_ADOPTION.md`.

## Pinned verification result

AgentOps tested revision: `f9b63cdc05be43d6953e27f02e117dda4079cdf9`. FlowOps caller: `9e57bd7dfa6dc8eb7f11249b32e77fa70e438945`. Verified `2026-10-13T16:50:02.508389Z`.

**167 tests passed; 92.98% statement coverage.** The actual FlowOps PeerClient accepted the POLICY_BLOCKED/HUMAN_REVIEW response, repeated it unchanged and denied an unscoped request locally. AgentOps stored zero runs, attempts and events; budget used plus reserved remained zero. No business review was completed.

Receipts: `docs/evidence/flowops-peer-profile/receipt.json`, `peer-http.json`, `standard-http.json`, `tests.txt`. The standard HTTP drill ran at `e1b779a253bc6f99cfb9f4c8e993bc8069a9cc05`; application runtime/dependencies are byte-identical at the tested revision. The only intervening change corrected the test harness venv interpreter path. Canonical aggregate evidence now references the current local profile; previous aggregate evidence is retained in this receipt directory.
