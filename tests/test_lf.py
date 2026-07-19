from app.lf import run_lfs, ERROR, OK, ABSTAIN

def get(results, name):
    return next(r for r in results if r["lf"] == name)

def test_negation_drop_en_es():
    r = run_lfs("Do not stop taking this medication.", "Deje de tomar este medicamento.", "en-es")
    assert get(r, "lf_negation_drop")["label"] == ERROR
    r2 = run_lfs("Do not stop taking this medication.", "No deje de tomar este medicamento.", "en-es")
    assert get(r2, "lf_negation_drop")["label"] == OK
    r3 = run_lfs("Take with food.", "Tome con comida.", "en-es")
    assert get(r3, "lf_negation_drop")["label"] == ABSTAIN  # no negation in source

def test_negation_drop_zh_en():
    r = run_lfs("他没有过敏史。", "He has a history of allergies.", "zh-en")
    assert get(r, "lf_negation_drop")["label"] == ERROR
    r2 = run_lfs("他没有过敏史。", "He has no history of allergies.", "zh-en")
    assert get(r2, "lf_negation_drop")["label"] == OK

def test_number_mismatch():
    r = run_lfs("Take 5 mg twice a day.", "Tome 50 mg dos veces al día.", "en-es")
    assert get(r, "lf_number_mismatch")["label"] == ERROR
    assert "5" in get(r, "lf_number_mismatch")["evidence"]
    r2 = run_lfs("Take 5 mg twice a day.", "Tome 5 mg dos veces al día.", "en-es")
    assert get(r2, "lf_number_mismatch")["label"] == OK
    r3 = run_lfs("Rest well.", "Descanse bien.", "en-es")
    assert get(r3, "lf_number_mismatch")["label"] == ABSTAIN

def test_number_zh_normalization():
    r = run_lfs("每天两次，每次五毫克。", "Twice daily, 5 mg each time.", "zh-en")
    assert get(r, "lf_number_mismatch")["label"] == OK  # 五->5, 两->2 both found

def test_zh_compound_numerals():
    r = run_lfs("每二十分钟测量一次。", "Check every 20 minutes.", "zh-en")
    assert get(r, "lf_number_mismatch")["label"] == OK  # 二十->20
    r2 = run_lfs("每次服用十五毫升。", "Take 15 ml each time.", "zh-en")
    assert get(r2, "lf_number_mismatch")["label"] == OK  # 十五->15

def test_zh_en_contraction_negation():
    r = run_lfs("他不开车。", "He doesn't drive.", "zh-en")
    assert get(r, "lf_negation_drop")["label"] == OK  # n't counts as negation counterpart

def test_untranslated_fragment_en_es():
    r = run_lfs("Apply the ointment to the affected area every night.",
                "Aplique the ointment to the affected area cada noche.", "en-es")
    assert get(r, "lf_untranslated_fragment")["label"] == ERROR
    r2 = run_lfs("Apply the ointment nightly.", "Aplique la pomada cada noche.", "en-es")
    assert get(r2, "lf_untranslated_fragment")["label"] == OK

def test_untranslated_cjk_zh_en():
    r = run_lfs("请按时服药。", "Please take 药 on time.", "zh-en")
    assert get(r, "lf_untranslated_fragment")["label"] == ERROR

def test_length_ratio():
    r = run_lfs("Take one tablet every morning before breakfast with a full glass of water.",
                "Sí.", "en-es")
    assert get(r, "lf_length_ratio")["label"] == ERROR
    r2 = run_lfs("Short.", "Corto.", "en-es")
    assert get(r2, "lf_length_ratio")["label"] == ABSTAIN  # source under 10 chars

def test_spanish_once_is_eleven():
    r = run_lfs("Take the pill every 11 hours.", "Tome la pastilla cada once horas.", "en-es")
    assert get(r, "lf_number_mismatch")["label"] == OK  # es side maps once->11

def test_twice_dos_equivalence_en_es():
    r = run_lfs("Apply the cream twice daily to the wound.",
                "Aplique la crema dos veces al día en la herida.", "en-es")
    assert get(r, "lf_number_mismatch")["label"] == OK

def test_single_double_are_not_numbers():
    r = run_lfs("Apply a single thin layer twice daily.",
                "Aplique una capa fina dos veces al día.", "en-es")
    assert get(r, "lf_number_mismatch")["label"] == OK  # src numbers = {2} from twice only

def test_always_four_results():
    r = run_lfs("Take with food.", "Tome con comida.", "en-es")
    assert [x["lf"] for x in r] == ["lf_negation_drop", "lf_number_mismatch",
                                    "lf_untranslated_fragment", "lf_length_ratio"]
