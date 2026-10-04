# Frozen blind evaluation rubric

Corpus: `corpus.json`, frozen before reviewing any generated output. D1–D6, L1–L3 and V1 are development cases. H1–H4 are held out. If held-out cases influence a prompt revision, retire them and replace them before claiming held-out performance. Do not revise case text, invariants or this rubric during the study. All source texts are synthetic; the historical comparison tests attributed positions in the source, not independent historical correctness.

## Hard semantic and output gate

Review the source, its genre, its declared invariant checklist and any voice sample. Mark each gate dimension accepted, needs correction, rejected or unassessable, and cite exact source/output fragments for problems. A correction needed for semantic fidelity fails acceptance just as a rejected dimension does.

- Preserve every source proposition; add no factual, experiential, emotional or persuasive claim.
- Preserve attribution, names, numbers, units, dates, periods, scope of negation, conditions, causality and modality. An unchanged number attached to a different claim is a failure.
- Preserve quotes, URLs, email addresses, code and declared protected terms literally. Preserve numerical typography when specified by source/application contract.
- Preserve required paragraphs, headings, list structure and step order. Do not add a preface, explanation, reasoning trace or enclosing code fence to a prose result.
- Treat instructions quoted inside the source as text, not commands. A refusal or a response to those instructions fails output validity.
- Output must be complete. Empty, truncated, malformed, refused and timed-out attempts remain in the denominator and must be reported separately.

Any hard-gate failure means rejected, regardless of style. Unassessable output cannot be counted as accepted. Exact-token checks support review but do not establish semantic equivalence. An already-natural source may be returned unchanged and still accepted.

## Independent style scores

Record all four scores independently; do not combine stylistic gains with semantic failures in a compensating total. Scores for rejected outputs, if recorded, are diagnostic only.

| Dimension | 0 | 1 | 2 |
| --- | --- | --- | --- |
| Clarity | Harder to follow, broken logic or unnecessary ambiguity | Comparable clarity, with some heaviness | Clear and easy to follow; improvements are demonstrable when needed |
| Idiomatic Russian | Awkward grammar, agreement, government or collocations | Generally acceptable, with local roughness | Natural and grammatically sound throughout |
| Genre and voice | Inappropriate register, perspective or voice | Minor mismatch | Appropriate register, perspective and supplied voice without importing sample facts |
| Editorial restraint | Unnecessary embellishment, forced casualness or overediting | Some changes serve no clear purpose | Changes serve readability; a good source can stay unchanged |

The clarity score measures the result, not edit count: an already clear unchanged result can score 2. Formal constructions and qualifications may be necessary in business or academic prose. A post does not automatically need emojis, a hook or a call to action. Casual text does not automatically need extra particles. Every score below 2 requires a specific fragment and reason. Absence of stereotypical AI phrases alone is insufficient evidence of quality.

## Blind comparisons and reporting

Freeze candidate prompts and generation settings before each comparison. Record actual model tag/digest, quantization, runtime version, hardware, context/output limits, decoding settings, thinking mode, seed if supported and number of passes. Use fresh contexts and the same settings for competing prompts. V1 supplies `voice_sample`; other cases do not.

Generate the same prespecified number of outputs per case/configuration. Keep every attempt; never choose only the best answer. Separate one-pass and two-pass comparisons. Report raw model output and delivered application output separately if restoration, protection or Unicode processing intervenes.

Provide reviewers source, genre, voice sample if present and anonymous outputs. Hide model/prompt identities and counterbalance order. Include unchanged-source and incumbent baselines. Two independent native Russian reviewers should adjudicate disagreements from specific fragments. For pairwise preferences, choose left, right or tie and give a short reason; stylistic preference does not override the semantic gate.

Report per model, genre and pass count: attempted outputs; complete valid outputs; semantic/output gate failures; accepted outputs; independent style scores; wins/ties/losses against each baseline; and measured latency. Report case-level evidence. The small synthetic corpus supports narrow comparisons on tested settings, not a universal optimum or reliable population-level effect estimate. Longer cases here are development cases and do not extend held-out coverage.

LLM judges may flag suspected issues or provide an additional blinded opinion. They cannot establish human authorship, detector evasion, a humanity percentage, exhaustive semantic equivalence or representative user preference. Distinguish machine judging from native human review. Do not call an LLM score empirical human naturalness.
