"""Create the pre-inference v3 fixtures once; never overwrite a frozen file."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build():
    # (source, hypothesis, category, severity, mechanism, reference rationale)
    cases = [
        ("Administer the sedative only after the anesthetist authorizes it.", "Administre el sedante.", "omission", "critical", "clinical_condition", "The explicit authorization prerequisite for administering a sedative is removed."),
        ("Before starting dialysis, confirm that the access line has been checked.", "Comience la diálisis.", "omission", "critical", "clinical_condition", "A required check before a clinical procedure is removed."),
        ("Remove the catheter only when the physician has confirmed that it is no longer needed.", "Retire el catéter.", "omission", "critical", "clinical_condition", "The condition authorizing catheter removal is missing."),
        ("If the patient becomes unresponsive, stop the infusion immediately.", "Si el paciente deja de responder, continúe la infusión inmediatamente.", "mistranslation", "critical", "clinical_condition", "The response to an explicit stopping condition is reversed."),
        ("Do not give the contrast agent if a previous severe reaction is documented.", "Administre el medio de contraste si se documenta una reacción grave previa.", "negation_polarity", "critical", "clinical_condition", "The prohibition under an explicit contraindication is reversed."),
        ("Skip the injection if the safety seal is broken.", "Administre la inyección si el sello de seguridad está roto.", "mistranslation", "critical", "clinical_condition", "A condition requiring withholding the injection instead authorizes giving it."),
        ("Give 2 milligrams of the prescribed medicine.", "Administre 20 miligramos del medicamento recetado.", "number_unit", "critical", "dose", "The stated medication dose changes tenfold."),
        ("Apply the prescribed cream to the left leg.", "Aplique la crema recetada en la pierna derecha.", "mistranslation", "critical", "treatment_site", "The specified treatment site changes."),
        ("The prescribed dose must be given intravenously.", "La dosis recetada debe administrarse por vía oral.", "terminology", "critical", "route", "The route of administering the prescribed dose changes."),
        ("Repeat the prescribed dose every eight hours.", "Repita la dosis recetada cada dos horas.", "number_unit", "critical", "treatment_schedule", "The explicit medication interval changes."),
        ("Do not restart the anticoagulant today.", "Reinicie el anticoagulante hoy.", "negation_polarity", "critical", "medication_action", "A prohibition on restarting medication becomes an instruction to restart."),
        ("Call the emergency service immediately if breathing stops.", "Llame al servicio de emergencias mañana si la respiración se detiene.", "mistranslation", "critical", "emergency_action", "An immediate emergency instruction is delayed until tomorrow."),
        ("No ticket is needed to enter the public orientation session.", "Se necesita un boleto para entrar a la sesión de orientación pública.", "negation_polarity", "major", "administrative", "An administrative admission requirement is reversed without a clinical action."),
        ("Collect your visitor badge at counter 4.", "Recoja su credencial de visitante en el mostrador 9.", "number_unit", "major", "administrative", "The required administrative destination changes."),
        ("Please bring the completed survey and a stamped envelope.", "Por favor, traiga la encuesta completada.", "omission", "major", "administrative", "A required administrative item is omitted."),
        ("The registration desk closes at three in the afternoon.", "El mostrador de registro cierra por la tarde.", "omission", "major", "administrative", "The specific administrative closing time is lost."),
        ("The case coordinator will send the attendance record.", "El cajero enviará el registro de asistencia.", "terminology", "major", "administrative", "The responsible professional role changes without an explicit clinical action."),
        ("We will email the decision after the committee meets.", "Le enviaremos la decisión por correo electrónico el viernes después de que se reúna el comité.", "addition", "major", "administrative", "A specific date commitment is invented."),
        ("The printed maps are on the table.", "Los mapas impreso están sobre la mesa.", "grammar", "minor", "form_only", "Adjective agreement is faulty; location, objects and proposition remain intact."),
        ("The new chairs are comfortable.", "Las sillas nuevas es cómodas.", "grammar", "minor", "form_only", "Verb agreement is faulty but the proposition remains intact."),
        ("Do not use the device until the technician completes the safety inspection.", "No utilice el dispositivo hasta que el técnico termine la inspección de seguridad.", "no_error", "neutral", "clean_condition", "The prohibition and its prerequisite are preserved."),
        ("The prescription specifies one capsule every twelve hours.", "La receta indica una cápsula cada doce horas.", "no_error", "neutral", "clean_dose", "The dose and interval are preserved with number words."),
        ("You may reschedule the orientation by calling reception.", "Puede cambiar la fecha de la orientación llamando a recepción.", "no_error", "neutral", "clean_paraphrase", "An idiomatic paraphrase preserves the administrative action."),
        ("Leave the dressing in place unless the nurse tells you to remove it.", "Deje el apósito en su sitio, salvo que la enfermera le indique que lo retire.", "no_error", "neutral", "clean_exception", "The exception governing removal is preserved."),
    ]
    rows = []
    for i, (source, hypothesis, category, severity, mechanism, rationale) in enumerate(cases, 1):
        rows.append({"id": f"s{i:02}", "parent_id": f"severity-v3-{i:02}", "source": source,
                     "hypothesis": hypothesis, "mechanism": mechanism, "reference_status": "provisional_policy_assigned",
                     "expert_adjudicated": False, "gold": {"error_types": [category], "worst_severity": severity,
                     "adequacy": {"neutral": 5, "minor": 4, "major": 3, "critical": 2}[severity]},
                     "reference_rationale": rationale, "rubric_version": "severity-v3"})
    disputed = [
        {"id": "d01", "parent_id": "disputed-v3-01", "source": "Take the tablet with a snack.",
         "hypothesis": "Tome la pastilla.", "plausible_severities": ["major", "critical"],
         "reason": "Is a food qualifier part of the clinical administration schedule under this policy? Drug-specific context is unstated; reviewer agreement is needed."},
        {"id": "d02", "parent_id": "disputed-v3-02", "source": "Let us know if you have any new concerns.",
         "hypothesis": "Avísenos si tiene alguna inquietud.", "plausible_severities": ["neutral", "minor", "major"],
         "reason": "The loss of 'new' may or may not restrict relevant concerns in the intended conversational context."},
        {"id": "d03", "parent_id": "disputed-v3-03", "source": "The specialist will discuss the result with you soon.",
         "hypothesis": "El médico hablará pronto con usted sobre el resultado.", "plausible_severities": ["neutral", "major"],
         "reason": "Specialist versus physician depends on the unstated professional role and intended specificity."},
        {"id": "d04", "parent_id": "disputed-v3-04", "source": "Return next week for the follow-up.",
         "hypothesis": "Regrese dentro de dos semanas para el seguimiento.", "plausible_severities": ["major", "critical"],
         "reason": "A follow-up interval changes, but the text does not establish whether a time-sensitive clinical action is affected."},
    ]
    for row in disputed:
        row.update(reference_status="disputed_before_inference", expert_adjudicated=False)
    old = []
    for file in ("translation_probe_v1.jsonl", "holdout_v2.jsonl"):
        old += [json.loads(s) for s in (ROOT / "evals/data" / file).read_text(encoding="utf-8").splitlines() if s.strip()]
    regressions = []
    for id_ in ("h19", "t012", "t003::p2-omis1", "t008::p2-term2"):
        row = next(e.copy() for e in old if e["id"] == id_)
        # Preserve original gold; this phase is descriptive, never a primary v3 gate.
        row["historical_gold_unchanged"] = True
        regressions.append(row)
    payloads = {"severity_holdout_v3.jsonl": rows, "severity_disputed_v3.jsonl": disputed,
                "severity_regression_v3.jsonl": regressions}
    destinations = [ROOT / "evals/data" / name for name in payloads]
    if any(path.exists() for path in destinations):
        raise SystemExit("Frozen fixture already exists; use a new version")
    for name, items in payloads.items():
        (ROOT / "evals/data" / name).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in items), encoding="utf-8")
    print("Created 24 primary, 4 predeclared disputed and 4 historical regression items.")


if __name__ == "__main__":
    build()
