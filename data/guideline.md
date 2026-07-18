# MQM-lite Translation QA Guideline — v1.0

## Decision tree (apply in order)
1. **Meaning first**: is anything missing (omission) or added (addition)?
2. **Critical triggers**: dosage/number changed (number_unit)? negation flipped (negation_polarity)? medical term swapped (terminology)?
3. **Everything else**: wrong meaning (mistranslation), grammar, punctuation.
4. If nothing is wrong: `no_error` (severity neutral, adequacy 5 unless style issues).

## Severity (weights 0 / 1 / 5 / 25)
| Severity | Rule of thumb | Example |
|---|---|---|
| neutral | no error | — |
| minor | noticeable, meaning intact | awkward word order |
| major | meaning distorted but detectable | a clause silently dropped |
| critical | would change clinical action | "do not take" → "take"; 5 mg → 50 mg |

## Error types
`no_error` exclusive · `mistranslation` wrong meaning · `omission` content dropped ·
`addition` content invented · `terminology` wrong domain term (hypertension≠hipotensión) ·
`number_unit` any digit/dose/unit changed · `negation_polarity` negation added/dropped ·
`grammar` · `punctuation`.

## Hard rules (enforced by the tool)
- `no_error` cannot be combined with other labels and forces severity neutral.
- A real error can never be severity neutral.
- `critical` requires a note quoting the evidence.
- Judge suggestions are shown only in routing batches — never while collecting golden labels
  (anchoring discipline). Form your own judgment first.

## Revision log
- v1.0 (2026-07): initial version, adapted from the MQM top-level typology with the three
  medical critical triggers (dosage, negation, terminology) promoted to first-class checks.
