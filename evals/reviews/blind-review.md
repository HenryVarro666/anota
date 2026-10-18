# Independent reference review, ready to run

The v2 labels are provisional. A new reviewer batch lets two qualified bilingual
reviewers label the same source/hypothesis pairs **without the provisional gold or
either model prediction**. This is preparation for independent review, not a claim
that review has already happened.

```powershell
python scripts/prepare_blind_review.py --db runtime/holdout-review.db
python scripts/run_local.py --db runtime/holdout-review.db --port 8421
```

The first command refuses an existing database and creates 24 tasks, overlap two,
`show_suggestions=0`, zero golden answers and zero judge results. Lint exists in
the server database as in any normal import, but the existing clean-batch policy
strips it from annotator responses. Neither candidate nor baseline predictions
are imported. Keep this database local; `runtime/` is Git-ignored.

Use separate reviewer IDs. Reviewers should not read the result report, provisional
fixture or each other's labels before submitting. Self-reported IDs are a local
demo feature, not production authentication. They should apply a separately agreed
rubric and record source/target evidence, alternative acceptable tags and severity
uncertainty. A lead reviewer adjudicates disagreements, retaining both original
annotations and the decision trail. If a revised golden set is approved, give it
a **new version** and rescore both arms equally; keep the published v1/v2 scores.

Agree on category granularity and consequence-based severity before labeling.
The current guideline modal still displays v1; any revised reviewer guideline must
be explicitly versioned and agreed first. Keep item-specific model analyses away
from reviewers until their independent labels are locked.

This does not make the same 24 items unseen again. After prompt tuning on their
results, use a fresh conversation-disjoint external set for a confirmatory evaluation.
