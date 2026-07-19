import json
from pathlib import Path

from app.db import Database
from app import importer, perturb

DATA = Path(__file__).resolve().parent.parent / "data"


def test_perturbers_deterministic():
    assert perturb.perturb_negation("Tome una tableta.") == "No tome una tableta."
    assert perturb.perturb_number("Tome 5 mg cada 8 horas.") == "Tome 50 mg cada 8 horas."
    assert perturb.perturb_number("Sin cifras aquí.") is None
    assert (perturb.perturb_terminology("Tome el medicamento con agua.")
            == "Tome el suplemento con agua.")
    assert perturb.perturb_terminology("Nada que cambiar en esta frase.") is None
    src = "Eleve la pierna y aplique hielo, durante veinte minutos."
    out = perturb.perturb_omission(src)
    assert out is not None and len(out) < len(src)


def test_build_probe_on_demo():
    db = Database(":memory:")
    importer.import_demo(db, DATA)
    res = perturb.build_probe(db, 1, per_type=3)
    assert res["name"] == "probe-1"
    assert res["counts"]["clean"] == 3
    for etype in ("negation_polarity", "number_unit", "terminology", "omission"):
        assert res["counts"][etype] == 3, etype
    rows = db.query("SELECT * FROM tasks WHERE batch_id=?", (res["batch_id"],))
    assert len(rows) == res["n"] == 15
    assert all("::p" in r["id"] and r["is_golden"] == 1 for r in rows)
    n_golden = db.one(
        "SELECT COUNT(*) n FROM golden_answers ga JOIN tasks t ON t.id=ga.task_id "
        "WHERE t.batch_id=?", (res["batch_id"],))["n"]
    assert n_golden == res["n"]
    perturbed = [r for r in rows if "clean" not in r["id"]]
    for r in perturbed:
        assert json.loads(r["lf_flags"]), r["id"]      # lint ran on the probe pair


def test_probe_rejects_missing_batch():
    db = Database(":memory:")
    import pytest
    with pytest.raises(LookupError):
        perturb.build_probe(db, 99)
