# Initial related-work check

Reviewed 2026-11-01. This is a focused overlap check, not an exhaustive survey.
Read status below distinguishes inspected sections from a full paper or code
reproduction. Results are authors' reports, not independently replicated here.
No literature source establishes novelty for Suez.

| Source | Inspected evidence | Overlap and consequence |
| --- | --- | --- |
| [Agentic Coding in the Wild, v1](https://arxiv.org/html/2608.00101v1), Liu et al., July 2026 | Introduction, cache findings and limitations | Production Copilot characterization already covers cache degradation around turn boundaries, model switches and compaction. Its limitations explicitly identify missing quality signals and server-side telemetry. General observations that agent sessions reuse prefixes or lose reuse are insufficient differentiation. |
| [TraceLab, v2](https://arxiv.org/html/2606.30560v2), Zhu et al., June 2026 | Introduction, anonymization, prefix-cache analysis and limitations; [author project note](https://syfi.cs.washington.edu/blog/2026-06-25-tracelab/) | Collects Claude Code/Codex sessions and analyzes workload/cache behavior. Released traces drop raw messages and tool text; backend mechanisms remain inferred from external logs. Raw request-level token attribution may complement this, but a collector/profiler alone is not a new contribution. Repository is linked by authors; implementation not audited or reproduced yet. |
| [The Cache Wars, v2.0](https://agnt.gg/whitepapers/the-cache-wars-prompt-cache-efficiency-llm-agent-harnesses), AGNT Labs, July 2026 | Experimental protocol, telemetry tables, interpretation; [artifact index](https://agnt.gg/whitepapers/cache-wars-artifacts/) | Already compares six harnesses using provider accounting, logging proxies, pause tests and identical-content marker-strategy replay. This directly overlaps our hosted measurement direction. It is a vendor-authored technical report; code and raw artifact listings are visible, but experiments were not independently run. Its raw-byte descriptions should not replace validation of model rendering or engine eligibility. |
| [The Physical Cost of Context, v1](https://arxiv.org/html/2610.34763v1), Yu et al., October 2026 | Policy definitions, evaluation controls, measurements and limitations | Directly connects context policies, prefix reuse, uncached prefill, task quality and physical resources. Tests include local vLLM/SGLang stacks and a patch-generation study. Thus “shorter history can increase work by breaking reuse” and “evaluate quality as well as tokens” are already covered. Stated limits include sequential serving and untested batching/cache pressure; these are leads for investigation, not proof of a gap. Artifacts not reproduced. |
| [TurnCost, v1](https://arxiv.org/html/2610.15093v1), Kumar and Pandey, October 2026 | Abstract, cache-aware cost model, cost-comparison results and implications | Analyzes coding trajectories and the economics of truncation/delegation under high reuse. Cost-only contrasts do not establish quality equivalence. Relevant to our choice of baselines and to avoiding flat-input-price savings claims. Full methodology/code review remains pending. |

## Decision for Suez

The broad research premise is already studied. Do not present request capture,
harness comparisons, prefix caching, compaction penalties or quality-aware
resource measurement as new merely because Suez implements them.

A narrower provisional question is worth checking: which concrete harness
operations change the *server-rendered token prefix*, which only change request
metadata or cache-control policy, and how much of the measured reuse gap remains
after controlling residency and routing? This is our proposed question, not a
verified literature gap. Physical Cost of Context is an especially close baseline.

Our current two-call example motivates care: system-text metadata changes while
the provider still reports reads. We do not know its preprocessing or rendering.
That example cannot establish the cause or magnitude of lost reuse.

Before increasing hosted spending, inspect TraceLab's collector/analysis and the
closest study's artifact definitions, then choose one source-inspectable harness
and one frozen real task. Reuse existing datasets/tools where they satisfy the
measurement contract. Continue broad source verification before contribution
selection; keep negative or unexplained outcomes.

## Retrieval record and limits

Queries included exact checklist titles and coding-agent prefix-cache profiling.
Used primary arXiv HTML, Microsoft Research's publication record, author project
pages and the technical report's own artifact index. Initial v1 HTML fetches for
TraceLab/TurnCost failed; following official abstract-page HTML links resolved
TraceLab v2 and TurnCost v1. The TraceLab `.git` URL fetch failed; that is not
evidence that its repository or code is unavailable. No claims based on those
failed fetches or on third-party search summaries are adopted here.

This note does not resolve every title in the shared literature queue. No code
was installed, benchmark run, venue selected, or submission claim made.
