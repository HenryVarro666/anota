# Problem and verification

> **With a fixed human review budget, can calibrated machine assistance surface more consequential translation and transcription errors than original ordering while preserving blind review and traceable revisions?**

**Why it matters:** Review time limits how much training and evaluation data a team can inspect. A useful workbench must help reviewers find errors and produce corrections that downstream users can trace.

**Central tension:** Hints and ranking may focus attention, but can also anchor reviewers or hide errors outside the model's supported domain. Finding more reference edit errors does not by itself establish faster human review or better trained models.

**Current evidence and gap:** [README.md](README.md) describes blind policies, speech review, calibrated judges and frozen experiments. [docs/project-map.md](docs/project-map.md) records the boundaries of that evidence. Human efficiency and downstream training gains require separate studies.

**Decisive verification:** Counterbalance assisted and blind review over source-disjoint items. Hold reviewer time and annotation guidelines fixed; measure consequential errors corrected, agreement, active review time and unresolved cases. Test that protected fields remain absent from blind responses. If ranking gains disappear with real reviewers or correction quality declines, retain blind review as the default.

This module defines a prospective evaluation; it adds no experimental result.
