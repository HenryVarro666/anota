import json
import pytest
from app.db import Database
from app import quality

def test_kappa_textbook_2x2():
    # 50 pairs: 20 yes/yes, 15 no/no, 5 yes/no, 10 no/yes -> po=0.7, pe=0.5, kappa=0.4
    a = ["yes"] * 20 + ["no"] * 15 + ["yes"] * 5 + ["no"] * 10
    b = ["yes"] * 20 + ["no"] * 15 + ["no"] * 5 + ["yes"] * 10
    assert quality.cohen_kappa(a, b, ["yes", "no"]) == pytest.approx(0.4, abs=1e-9)

def test_kappa_perfect_and_weighted_extremes():
    sev = ["neutral", "minor", "major", "critical"]
    assert quality.cohen_kappa(sev, sev, sev, weights="linear") == pytest.approx(1.0)
    # systematic swap of adjacent categories -> weighted kappa exactly -1
    assert quality.cohen_kappa(["neutral", "minor"], ["minor", "neutral"], sev,
                               weights="linear") == pytest.approx(-1.0, abs=1e-9)

def test_golden_score():
    ans = {"error_types": ["negation_polarity"], "worst_severity": "critical"}
    good = {"error_types": ["negation_polarity"], "worst_severity": "critical"}
    bad = {"error_types": ["no_error"], "worst_severity": "neutral"}
    assert quality.golden_score(ans, good)["passed"] is True
    assert quality.golden_score(ans, bad)["passed"] is False

def seed(db, overlap=2):
    bid = db.execute("INSERT INTO batches(name, overlap) VALUES('b', ?)", (overlap,))
    db.execute("INSERT INTO tasks(id,batch_id,source,hypothesis,metadata) VALUES(?,?,?,?,?)",
               ("t1", bid, "s", "h", json.dumps({"arm": "wait1"})))
    return bid

def add_ann(db, task, who, types, sev, adequacy=3):
    return db.execute(
        "INSERT INTO annotations(task_id,annotator,error_types,worst_severity,adequacy,fluency)"
        " VALUES(?,?,?,?,?,4)", (task, who, json.dumps(types), sev, adequacy))

def test_final_label_review_wins():
    db = Database(":memory:"); seed(db)
    a1 = add_ann(db, "t1", "chao", ["omission"], "major")
    rep = add_ann(db, "t1", "reviewer:lead", ["negation_polarity"], "critical")
    db.execute("INSERT INTO reviews(task_id,reviewed_annotation_id,reviewer,verdict,"
               "replacement_annotation_id,case_note) VALUES('t1',?,?,'overturned',?,'polarity')",
               (a1, "lead", rep))
    fl = quality.final_label(db, "t1")
    assert fl["worst_severity"] == "critical" and fl["source_kind"] == "review"

def test_final_label_majority_and_tie_severity():
    db = Database(":memory:"); seed(db)
    add_ann(db, "t1", "chao", ["omission", "terminology"], "major", adequacy=2)
    add_ann(db, "t1", "maria", ["omission"], "critical", adequacy=4)
    fl = quality.final_label(db, "t1")
    assert fl["error_types"] == ["omission"]          # strict majority only
    assert fl["worst_severity"] == "critical"         # mode tie -> stricter wins
    assert fl["adequacy"] == 3                        # median of 2,4
    assert fl["source_kind"] == "aggregate"

def test_final_label_median_rounds_half_up():
    db = Database(":memory:"); seed(db)
    add_ann(db, "t1", "chao", ["omission"], "major", adequacy=2)
    add_ann(db, "t1", "maria", ["omission"], "major", adequacy=3)
    fl = quality.final_label(db, "t1")
    assert fl["adequacy"] == 3                          # median(2,3)=2.5 rounds up to 3
    assert fl["source_kind"] == "aggregate"

def test_latest_annotation_wins_per_annotator():
    db = Database(":memory:"); seed(db)
    add_ann(db, "t1", "chao", ["omission"], "major")
    add_ann(db, "t1", "chao", ["no_error"], "neutral")   # re-submit after undo
    fl = quality.final_label(db, "t1")
    assert fl["error_types"] == ["no_error"] and fl["source_kind"] == "single"

def test_error_arm_matrix():
    db = Database(":memory:"); bid = seed(db)
    db.execute("INSERT INTO tasks(id,batch_id,source,hypothesis,metadata) VALUES(?,?,?,?,?)",
               ("t2", bid, "s", "h", json.dumps({"arm": "offline"})))
    add_ann(db, "t1", "chao", ["omission"], "major")
    add_ann(db, "t2", "chao", ["no_error"], "neutral")
    m = quality.error_arm_matrix(db)
    assert m["cells"]["wait1"]["omission"] == 1.0
    assert m["cells"]["offline"]["omission"] == 0.0
    assert m["n"] == {"wait1": 1, "offline": 1}

def test_annotator_stats_and_golden():
    db = Database(":memory:"); seed(db)
    db.execute("UPDATE tasks SET is_golden=1 WHERE id='t1'")
    db.execute("INSERT INTO golden_answers(task_id, answer) VALUES('t1', ?)",
               (json.dumps({"error_types": ["omission"], "worst_severity": "major"}),))
    add_ann(db, "t1", "chao", ["omission"], "major")
    s = quality.annotator_stats(db)[0]
    assert s["annotator"] == "chao" and s["golden_passed"] == 1 and s["golden_total"] == 1

def test_pairwise_kappa_needs_min_shared():
    db = Database(":memory:"); seed(db)
    add_ann(db, "t1", "chao", ["omission"], "major")
    add_ann(db, "t1", "maria", ["omission"], "major")
    assert quality.pairwise_kappa(db, min_shared=3) == []
    assert quality.pairwise_kappa(db, min_shared=1)[0]["n"] == 1
