# Grade report — public_01_dev

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.341 | +2.5% | 0.239 | +0.4% | 0.500 | +1.9% | -2% | 40 (47) |
| nw_kyc | 0.476 | +31.0% | 0.306 | +26.3% | 0.595 | +0.8% | 0% | 35 (21) |
| nw_renewal | 0.442 | +5.4% | 0.266 | +0.8% | 0.571 | +4.6% | 30% | 46 (42) |
| ALL | 0.404 | +9.6% | 0.261 | +5.4% | 0.546 | +3.1% | 10% | 121 (110) |

## Changes applied by the controller

8 applied — beneficial: 4, neutral: 4

- nw_renewal v2 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.067 didΔ=+0.000 → **beneficial**
- ks_cancel v2 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=-0.002 didΔ=+0.000 → **neutral**
- nw_kyc v2 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.300 → **beneficial**
- nw_renewal v3 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.073 didΔ=+0.000 → **beneficial**
- nw_kyc v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.082 → **beneficial**
- nw_renewal v4 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.001 didΔ=+0.000 → **neutral**
- nw_kyc v4 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.001 → **neutral**
- nw_renewal v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.006 didΔ=+0.000 → **neutral**

## Governance

```
{
  "approval_requests": 9,
  "decisions": {
    "approved": 8,
    "rejected": 1,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 1
  },
  "applied_via_approval": 8,
  "auto_applied": 0,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nw_renewal": 1,
    "nw_kyc": 2,
    "ks_cancel": 1
  }
}
```

## Incidents

```
[]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{
  "nw_renewal:welcome_message=W3": {
    "3": 1.0,
    "4": 0.721,
    "5": 0.334,
    "6": 0.195,
    "7": 0.0
  }
}
```

Credits used: 0 · validation runs: 0 · LLM fix calls: 0