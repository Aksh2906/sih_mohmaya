# Economic value of reducing breach risk

This is an illustrative sensitivity analysis, not a measurement or forecast of Dev Privacy Guard's economic impact.

## Evidence

[IBM Cost of a Data Breach Report 2025, Figure 31, printed p. 37](https://na.ingrammicro.com/Ingram/media/North-America-US/EN-US/I/ibm/docs/IBM-cost-of-a-data-breach-2025-full-report.pdf#page=19) reports a USD 4.63 million average breach cost when shadow AI is involved. This enterprise-study figure is a severity benchmark, not an annual loss estimate, probability, or a browser-automation-specific cost. It may not represent our target users. IBM did not evaluate this product.

## Model

For a simplified annual model with one relevant breach event, unchanged average severity, and an assumed absolute probability reduction d:

Gross annual expected loss avoided = d × USD 4,630,000.

| Assumed reduction (percentage points) | Gross annual expected loss avoided per organization |
|---|---:|
| 0 | $0 |
| 1 | $46,300 |
| 2 | $92,600 |
| 3 | $138,900 |

One percentage point means, for example, moving from 10% to 9%, not reducing 10% to zero. That example is not an estimated starting probability. Scenarios require starting probability at least as large as the proposed reduction. Actual impact can be zero or negative; the graph does not estimate a likely range.

Net annual benefit = gross expected loss avoided + measured productivity savings − total annual product costs. Include server inference, installation, maintenance, human review, false-positive delays and support. Do not claim positive ROI until those inputs are known. Reduced sensitive-data exposure alone does not prove the assumed breach-probability reduction.

## What to say on the slide

“Using IBM's breach-cost benchmark, each one-percentage-point reduction in relevant annual breach probability would represent $46,300 in gross expected annual loss avoided per organization. Our pilot must establish the actual risk reduction and operating cost.”

## Establishing actual product impact

Run matched workflows with and without the product on synthetic labeled data. Measure sensitive values escaping into model-bound requests, task completion, elapsed time including approvals, model/compute costs, and manual corrections. Keep privacy coverage distinct from proven breach prevention. Use customer-specific incident frequency/severity or an independently validated risk model for the economic conversion. Report measured task-cost savings separately from modeled security benefits. Do not test by exposing real private data to an unprotected provider.

Files: `dev-privacy-guard-economic-scenarios.png`, matching CSV, and reproducible `create_economic_impact_chart.py`.
