# Audit of the frozen local pilot (before the v2 comparison)

This review uses only the original 20 examples, rubric v1 and their two baseline
outputs. Original labels, raw responses, scores and figures are unchanged.
The review below is an agent's proposed adjudication, not a new expert golden set.
No proposal is substituted into the headline v1 metrics or the new holdout scores.

## Three mechanisms, not one accuracy problem

1. **Observed semantic misses.** Both baseline passes miss the omitted time span in
   `t003::p2-omis1` and medication→supplement in `t008::p2-term2`. These are visible
   source/target differences, irrespective of the disputed severity. They are
   failures of this model + prompt configuration; the original run cannot identify
   whether model weights, quantization or instructions caused them.
2. **Unspecified label granularity.** v1 asks for any subset of labels. It never says
   whether `mistranslation` should accompany a more specific type for the same
   discrepancy. Exact-set scoring punishes that defensible double annotation. The
   candidate makes a minimal-specific-label convention explicit. Gains here measure
   better adherence to a label policy, not necessarily better semantic understanding.
3. **Reference and severity ambiguity.** The synthetic generator assigns critical
   to every injected number/negation/term change, while the prose rubric ties critical
   to changed clinical action. It assigns major to every omission, including lost
   safety conditions. The model also undergrades some clear meaning changes as minor.
   These sources of disagreement must be separated.

## Item-level review of all 12 development disagreements

| Example | Evidence / baseline behavior | Diagnosis | Proposed reference treatment |
|---|---|---|---|
| `t003::p2-omis1` | “throughout the day” disappears; model says clean | Semantic miss; severity depends on the interpretation of this general instruction | Keep omission; minor/major requires adjudication |
| `t003::p2-nega1` | “Beba”→“No beba”; model adds grammar and calls it minor | Negative Spanish imperative is grammatical; polarity changes the instruction. Critical is not justified solely by the type name | Keep polarity; review major vs critical with clinical context |
| `t001::p2-term0` | tablet→spoonful; extra mistranslation | Specific/general tag duplication; clinical administration changes | Terminology / critical remains reasonable under the operational rubric |
| `t004::p2-omis2` | Temperature threshold removed; remainder is awkward | Both omission and a malformed fragment can be defensible; the synthetic single label is incomplete | Keep omission as required; adjudicate extra tag and major vs critical |
| `t004::p2-numb0` | 38→380; optional extra mistranslation differs between repeats | Primarily tag granularity; threshold changes a clinical contact instruction | Number / critical retained provisionally |
| `t002` | Medication stop prohibition becomes instruction; extra mistranslation | Category redundancy, not a missed polarity error | Polarity / critical retained provisionally |
| `t001::p2-omis0` | “with breakfast” missing; model says minor | Meaningful administration qualifier omitted; drug-specific harm is unstated | Omission / major is a reasonable provisional label; no inferred drug facts |
| `t004::p2-term1` | Fever→cough with a temperature phrase; extra mistranslation | Specific/general tags, and an unnatural resulting phrase | Terminology required; additional error and severity need adjudication |
| `t012` | “unless ... next dose” missing; model says minor, reference major | Model undergrades lost condition; reference may also undergrade the changed authorization to take a dose | Propose omission / critical under explicit-action rubric; expert review required |
| `t008::p2-term2` | medication→supplement; model says clean | Semantic miss; blanket critical reference is too strong without a stated changed action | Propose terminology / major; independent review needed |
| `t006::p2-numb1` | Appointment 10→100 a.m.; model says minor, reference critical | Malformed administrative time meaning changes, but no clinical action is stated | Propose number / major, not automatic critical |
| `t001::p2-nega0` | Taking instruction negated; extra mistranslation | Tag granularity; actual polarity error was detected | Polarity / critical retained provisionally |

This is not a causal attribution to Q4 quantization. No second precision, model or
H100 result exists. The new paired experiment isolates a prompt/policy clarification
with identical model, server, output budget, context and parser. Residual failures
remain failures of that configuration; they do not prove an inherent model limit.

## Frozen candidate and independent texts

The candidate explicitly checks propositions and qualifiers, chooses minimal
specific types, separates grammatical form from meaning, and grades by explicit
consequences. It contains no holdout source, hypothesis, ID or answer.
The 24 holdout parents are newly authored, not mutations of the 11 development
parents. Clean controls and administrative cases test false positives and prevent
“always critical” from scoring well. Labels and reference rationales are fixed
before requests. The same agent authored them and the candidate, so they are not
independent expert annotations. Domain expertise and a real conversation-disjoint
external set remain necessary before claims of deployment quality.

MQM's severity guidance bases impact on intended use; it does not equate every
number error with critical. This repository uses a task-specific operational rubric,
not an assertion of full MQM certification. See
[MQM severity guidance](https://www.themqm.org/guidance/values-and-scores/) and
[MQM error typology](https://www.themqm.org/mqm-pillars/typology/).
