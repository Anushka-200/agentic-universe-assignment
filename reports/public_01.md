# Grade report — public_01_dev

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.348 | +4.5% | 0.240 | +0.7% | 0.507 | +3.3% | 2% | 44 (47) |
| nw_kyc | 0.457 | +25.6% | 0.292 | +20.7% | 0.599 | +1.4% | 0% | 31 (21) |
| nw_renewal | 0.436 | +4.0% | 0.262 | -0.4% | 0.571 | +4.6% | 29% | 48 (42) |
| ALL | 0.400 | +8.7% | 0.258 | +4.1% | 0.549 | +3.7% | 11% | 123 (110) |

## Changes applied by the controller

8 applied — beneficial: 6, neutral: 2

- nw_renewal v2 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.067 didΔ=+0.000 → **beneficial**
- ks_cancel v2 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=-0.002 didΔ=+0.000 → **neutral**
- nw_kyc v2 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.300 → **beneficial**
- nw_renewal v3 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.073 didΔ=+0.000 → **beneficial**
- nw_kyc v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.082 → **beneficial**
- nw_kyc v4 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.000 → **neutral**
- nw_renewal v4 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.010 didΔ=+0.000 → **beneficial**
- ks_cancel v3 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.088 didΔ=+0.000 → **beneficial**

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
    "4": 0.894,
    "5": 0.326,
    "6": 0.324,
    "7": 0.318
  }
}
```

Credits used: 0 · validation runs: 0 · LLM fix calls: 0