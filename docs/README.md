# Documentation

| Document | Read it for |
| --- | --- |
| [Report](report.md) | The study: question, method, results with figures, related work, limitations |
| [How to read the results](reading-results.md) | Units, protocols, evidence levels and comparison limits, before quoting a number |
| [Result card: 487 vs 488 blocks](result-card-487-488.md) | The headline result: claim, conditions, evidence, scope |
| [What is new here](contribution.md) | Prior work subtracted, and the contribution that remains |
| [Assumptions](assumptions.md) | Every modeling and measurement choice, why it matters, and how it was checked |
| [Reproduce](reproduce.md) | Environments, the pinned engine launch and every experiment, in order |
| [Proxy reference](proxy.md) | Forwarding, capture format, session limits and their flags |
| [Released results](../results/README.md) | Per-request tables behind the report, with a validator that recomputes its numbers |
| [Run records](records/README.md) | Dated records behind each result, with run directories, spec hashes and commits |
| [Examples](../examples/README.md) | Synthetic checks that reproduce small versions of two results without an engine |

## Figures

Every figure in [figures/](figures/) is generated, never edited by hand, and
follows one theme that switches with the reader's light or dark setting.

| Figure | Generator | Source data |
| --- | --- | --- |
| `reuse-loss.svg`, `pipeline.svg`, `banner.svg` | `experiments/figures/make_diagrams.py` | None: explanatory diagrams and the README header |
| `eviction-curve.svg`, `recompute-split.svg`, `gpu-ttft.svg`, `scheduling.svg`, `session-cap.svg` | `experiments/figures/make_figures.py` | Private run directories under `artifacts/`; every plotted number is written to `figures/data.json` |
