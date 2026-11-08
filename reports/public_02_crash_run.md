# Grade report — public_02_incidents

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.290 | +17.0% | 0.199 | +8.3% | 0.509 | +6.8% | 38% | 81 (330) |
| nw_kyc | 0.480 | +28.1% | 0.305 | +26.0% | 0.592 | -0.0% | 0% | 50 (31) |
| nw_renewal | 0.424 | -0.5% | 0.260 | -0.8% | 0.548 | +0.0% | 7% | 53 (55) |
| ALL | 0.375 | +12.2% | 0.240 | +8.3% | 0.544 | +2.6% | 22% | 184 (416) |

## Changes applied by the controller

11 applied — beneficial: 10, neutral: 1

- nw_renewal v2 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.040 didΔ=+0.000 → **beneficial**
- ks_cancel v2 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.023 didΔ=+0.000 → **beneficial**
- nw_kyc v2 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.283 → **beneficial**
- nw_kyc v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.075 → **beneficial**
- nw_renewal v3 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.130 didΔ=+0.000 → **beneficial**
- ks_cancel v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.192 → **beneficial**
- ks_cancel v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.160 → **beneficial**
- ks_cancel v5 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.068 → **beneficial**
- ks_cancel v7 (controller_approved) dims=['prompt_version'] engageΔ=+0.102 didΔ=+0.000 → **beneficial**
- nw_renewal v4 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.055 didΔ=+0.000 → **beneficial**
- nw_kyc v4 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.001 → **neutral**

## Governance

```
{
  "approval_requests": 10,
  "decisions": {
    "approved": 8,
    "rejected": 2,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 2
  },
  "applied_via_approval": 8,
  "auto_applied": 3,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nw_renewal": 1,
    "nw_kyc": 1,
    "ks_cancel": 1
  }
}
```

## Incidents

```
[
  {
    "type": "did_flagged",
    "workflow": "ks_cancel",
    "at": "D2 10:00",
    "flagged_did_share_by_day": {
      "2": 0.204,
      "3": 0.0,
      "4": 0.0,
      "5": 0.0,
      "6": 0.0,
      "7": 0.0,
      "8": 0.0,
      "9": 0.0
    }
  },
  {
    "type": "supervisor_edit",
    "workflow": "ks_cancel",
    "at": "D3 11:30",
    "mttr_minutes": 390,
    "fixed_by": "controller_approved"
  },
  {
    "type": "supervisor_edit",
    "workflow": "nw_renewal",
    "at": "D5 15:00"
  }
]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{
  "nw_renewal:welcome_message=W3": {
    "2": 1.0,
    "3": 1.0,
    "4": 0.678,
    "5": 0.333,
    "6": 0.334,
    "7": 0.326,
    "8": 0.336,
    "9": 0.338
  }
}
```

Credits used: 3 · validation runs: 1 · LLM fix calls: 0