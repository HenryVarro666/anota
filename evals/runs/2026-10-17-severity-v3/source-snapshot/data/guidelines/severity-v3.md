# Anota operational translation rubric — severity-v3

This is one ordered policy for the judge and reference reviewers. Grade the change
in meaning and permitted action, not the error-type name, fluency, or length of the
missing span. Evaluate the translation, not the medical correctness of the source.

## 1. Compare meaning

Compare actor, action, polarity, entity, quantity/unit, time, prerequisite, exception,
and stopping condition. Fluent text can lose a condition. A correct paraphrase is not
an error. Use only the supplied source and hypothesis; do not invent patient facts.

## 2. Assign the highest applicable severity, in this order

**CRITICAL — changed clinical action or its authorization.** An error changes a
treatment substance, dose, route, treatment schedule, patient/site, contraindication,
emergency instruction, or whether/when a clinical action is allowed, required, or
must stop. Removing or reversing an explicit prerequisite, exception, restriction,
or stopping condition for a clinical action is CRITICAL. The source's stated condition
is sufficient evidence; additional proof of patient harm is not required. This rule
also applies when the action itself is translated correctly and only a short clause
is missing. Never downgrade such a case to major/minor merely because it is an
omission, sounds fluent, or the reader could notice/correct the error.

**MAJOR — changed meaning without the critical criterion above.** A proposition,
required administrative step, identity/document description, appointment/location,
availability interval, or commitment is altered, added, or lost. Administrative
numbers, negation, and job titles are not automatically critical. Meaning-changing
administrative omissions are major, not minor. Clinical context alone does not make
an unrelated administrative discrepancy critical.

**MINOR — form error with meaning intact.** A real grammar/spelling/punctuation fault
does not change any proposition, instruction, condition, entity, quantity, or time.
Small wording differences that are correct paraphrases are neutral, not minor.

**NEUTRAL — no translation error.** All relevant meaning is preserved and the target
is acceptable. Use no_error exclusively.

If the source lacks context needed to choose major versus critical, state what is
missing in the rationale and do not invent an action or harm. Reviewers flag these
cases for adjudication; the current model schema still requires one provisional
severity. A model confidence number is not evidence of reviewer agreement.

## 3. Assign categories separately

Use the smallest sufficient set for distinct discrepancies: omission for removed
content; addition for invented content; number_unit for changed numbers/units;
negation_polarity for added/dropped explicit negation; terminology for a substituted
domain entity/role; mistranslation for other meaning changes. Do not duplicate a
specific type with mistranslation for the same discrepancy. Grammar requires a real
form error; a grammatical Spanish negative imperative is not a grammar fault.

## 4. Output evidence

Give one concise sentence identifying the changed or missing span and the applicable
severity criterion. Preserve the JSON schema. no_error is exclusive and requires
neutral; a real error cannot be neutral. Adequacy: neutral 5, minor 4, major 3,
critical 1–2 according to how much meaning remains. These anchors are an operational
convention, not a calibrated clinical risk score.

Version: severity-v3. Provisional research policy; independent bilingual/domain review
is pending. Existing v1/v2 labels and results remain historical and unchanged.
