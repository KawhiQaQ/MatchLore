# Search and evidence

## Problem scope

Given a match prefix and eligible historical matches, find explainable statistical patterns relative to historical comparisons. The product combines an exact phase-change kernel with domain-specific burst, record, and sequence extractors. Final selection applies quality filters and deduplication.

The exactness claim below applies to the **phase-change kernel's fixed grammar and objective**, not to the entire multi-family product ranking or the editorial value of its outputs.

## Phase-change patterns

A candidate consists of `(a, b, end, A, B, cap)`:

- event count `A` in `[a, b)` does not exceed `cap`;
- the current count of `B` in `[b, end)` is compared with historical observations satisfying the same precondition;
- windows obey a configured grid and minimum length.

Three historical reference groups are considered: all eligible observations, those with matching score-sign context, and those with matching opponent-pressure category at `b`. Each conditioned reference must meet minimum support.

For each reference `r`, let `n_r` be its conditioned support and `k_r` the number of observations meeting or exceeding the current post-window count. The kernel minimizes:

```text
max_r (k_r + 1) / (n_r + 1)
```

Fractions are compared with integer arithmetic. Equal scores are ordered lexicographically by the pattern tuple. Returned `quality` is the negative logarithm of the worst reference fraction. These are descriptive searched frequencies, not corrected p-values.

## Implementation

| Module | Role |
|---|---|
| `_solver/core.py` | Prefix-count input, validation, deterministic output ordering |
| `_solver/bitsets.py` | Packed reference/tail masks, bit-sliced prefix subtraction |
| `_solver/witness.py` | Lazy support masks and witness-driven successor search |
| `_solver/ranking.py` | Bounded Top-K insertion |

Within a chain with fixed `b`, event types and threshold, moving the start `a` forward expands the precondition support. Previously counted tails provide lower-bound witnesses. Combined with non-tail capacity and the current Top-K boundary, these bounds identify necessary support intervals and permit skips. Bitsets make support/tail counts inexpensive; lazy caching avoids repeated condition-mask construction.

The implementation is extracted from the product's tested search kernel. Common techniques such as bitsets, prefix counts and bound propagation are not presented as new inventions. Tests compare this kernel against independent exhaustive enumeration on bounded random instances, including ties and partial bitmap words.

## Product pipeline

1. **Adapt** complete source records and record metric coverage.
2. **Select history** strictly earlier than kickoff and in the same season/patch partition.
3. **Generate patterns** with the search kernel plus domain-specific metrics and cross-match timelines.
4. **Select cards** by historical rarity, recency, magnitude, and evidence overlap.
5. **Render original statistics** from structured facts; retain the exact evidence and reference observations.
6. **Optionally draft commentary** with DeepSeek and check numerical roles, ranges, and meaning. Failed drafts never replace original statistics.

`verify.py`, `verify_history.py` and `verify_metrics.py` independently recount the published facts. Source incompleteness still limits what can be claimed: a local record is not automatically a career record, and model review is not a proof of semantic correctness.
