# CLTV Engine

Customer Lifetime Value engine for AI Sales Operating System.

## Purpose

Predict long-term customer value from telesales signals and prioritize leads by value, not only by near-term close probability.

## Inputs

- Call history (connect, convert, QA, objections, duration)
- Conversation DNA (rapport, value building, closing)
- Intent + emotion
- Buying signal
- CRM (past revenue, segment, AOV, tickets, tenure)
- Follow-up discipline (completed / missed)

## Outputs

- `cltv_score`, `retention_score`, `upsell_score`, `cross_sell_score`, `referral_score`
- `lifetime_value`, `repeat_purchase_probability`, `churn_risk`, `referral_probability`
- `priority`: `high_value` | `medium` | `low`
- Evidence + explanations for every prediction

## API

- `POST /v1/cltv/predict`
- `POST /v1/cltv/prioritize`
- `GET /v1/cltv/dashboard`
- `GET /v1/cltv/quality`

## Dashboard widgets

- CLTV forecast
- Churn forecast
- Average lifetime value
- Upsell opportunity
- Referral opportunity
- Prediction count / priority counts

## Quality gate

- Forecast accuracy
- Stability
- Explainability
- Evidence validation
- High/low value separation

## Integration

Wired into AI Sales OS navigation as **CLTV**. Complements Negotiation Strategy (near-term close path) with long-horizon value prioritization.
