"""Reconstruct the pre-inference, agent-authored synthetic holdout and candidate profile."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CANDIDATE = """Apply the rubric using a separate meaning, category, and consequence check.
Compare every proposition: actor, action, polarity, object, quantity/unit, time/duration, and condition/exception. A fluent, plausible sentence can still omit or replace information. Check domain entities and professional roles precisely.
Use the smallest sufficient set of specific categories for distinct discrepancies. Do not add mistranslation for the same discrepancy already covered by omission, addition, number_unit, negation_polarity, or terminology. Use mistranslation for other meaning changes. Add grammar only for a separate real grammatical fault; a Spanish negative imperative is not inherently ungrammatical.
Determine severity from consequences, not the category name. Minor preserves meaning. Major changes meaning, an instruction, or an administrative outcome without an explicit clinical action risk. Critical changes an explicit clinical action, medication/dose, contraindication, or a condition governing whether a clinical action is performed. A changed number or term is not automatically critical. A meaningful missing qualifier is not merely a fluency issue. Do not infer unstated clinical facts.
Return one short rationale quoting the decisive source and target evidence, or identifying the missing span. /no_think"""

# Each row has its own source parent; none is a variant of a development-set parent.
# These labels express the explicit operational rubric, not certified medical judgments.
CASES = [
    ("clean", "Your interpreter will join by video before registration begins.", "Su intérprete se conectará por video antes de que comience la inscripción.", "no_error", "neutral", "Role, channel and temporal order are preserved."),
    ("clean", "You do not need to bring printed documents.", "No necesita traer documentos impresos.", "no_error", "neutral", "Negation is correctly preserved."),
    ("clean", "The label reads 12 milligrams per capsule.", "La etiqueta indica 12 miligramos por cápsula.", "no_error", "neutral", "Quantity, unit and denominator are preserved."),
    ("clean", "Please remain on the line while I connect the interpreter.", "Por favor, permanezca en la línea mientras conecto al intérprete.", "no_error", "neutral", "The instruction and simultaneous condition are preserved."),
    ("negation", "Do not inject the medicine if the liquid is cloudy.", "Inyecte el medicamento si el líquido está turbio.", "negation_polarity", "critical", "A prohibited injection becomes an affirmative clinical action."),
    ("negation", "No signature is required for the parking voucher.", "Se requiere una firma para el vale de estacionamiento.", "negation_polarity", "major", "The administrative requirement is reversed; no clinical action is stated."),
    ("negation", "Do not give this medicine to a child who has had an allergic reaction to it.", "Dé este medicamento a un niño que haya tenido una reacción alérgica a él.", "negation_polarity", "critical", "A stated medication prohibition is reversed."),
    ("negation", "The interpreter cannot see the document on your screen.", "El intérprete puede ver el documento en su pantalla.", "negation_polarity", "major", "Access to a document is reversed; no clinical action is stated."),
    ("number", "Administer 0.4 mL from the marked syringe.", "Administre 4 mL con la jeringa marcada.", "number_unit", "critical", "The quantity in an explicit administration instruction changes tenfold."),
    ("number", "The room number for your interview is 206.", "El número de sala para su entrevista es 260.", "number_unit", "major", "The administrative location changes."),
    ("number", "The oxygen flow on the order is 2 liters per minute.", "El flujo de oxígeno en la orden es de 20 litros por minuto.", "number_unit", "critical", "The flow specified by a clinical order changes tenfold."),
    ("number", "Your reference number ends in 71.", "Su número de referencia termina en 17.", "number_unit", "major", "The identifier changes without a stated clinical instruction."),
    ("term", "Use the inhaler, not the nasal spray, for this prescribed dose.", "Use el aerosol nasal, no el inhalador, para esta dosis recetada.", "terminology", "critical", "Two device names are exchanged in an explicit administration instruction."),
    ("term", "The attached document is the consent form.", "El documento adjunto es la factura.", "terminology", "major", "The document type changes; no immediate clinical action is stated."),
    ("term", "This bottle contains insulin for the scheduled injection.", "Este frasco contiene heparina para la inyección programada.", "terminology", "critical", "The named drug in an injection context is replaced."),
    ("term", "A certified interpreter will translate the meeting.", "Un recepcionista certificado interpretará la reunión.", "terminology", "major", "The professional role is replaced."),
    ("omission", "Give the next dose only after the clinician confirms the laboratory result.", "Administre la siguiente dosis.", "omission", "critical", "A prerequisite to administering a dose is removed."),
    ("omission", "Bring the signed application and the original identity document.", "Traiga la solicitud firmada.", "omission", "major", "A required document is omitted."),
    ("omission", "Start the transfusion only after the identity check is complete.", "Inicie la transfusión.", "omission", "critical", "A prerequisite to a clinical action is removed."),
    ("omission", "The interpreter is available on weekdays from noon until four.", "El intérprete está disponible los días laborables.", "omission", "major", "The availability interval is omitted."),
    ("meaning", "The appointment was postponed to a later date.", "La cita se adelantó a una fecha anterior.", "mistranslation", "major", "The direction of rescheduling is reversed without an explicit negation or number change."),
    ("meaning", "Reduce the infusion rate according to the written order.", "Aumente la velocidad de infusión según la orden escrita.", "mistranslation", "critical", "The clinical action verb is reversed; this is not an added or dropped negation token."),
    ("addition", "The office will contact you after reviewing the form.", "La oficina se comunicará con usted mañana después de revisar el formulario.", "addition", "major", "An unsupported date commitment is added."),
    ("grammar", "The interpreter is ready to begin.", "El intérprete están listo para comenzar.", "grammar", "minor", "Subject-verb agreement is faulty; the proposition remains clear."),
]


def main():
    dataset = ROOT / "evals/data/holdout_v2.jsonl"
    profile_path = ROOT / "evals/profiles/local-lmstudio-v2.json"
    if dataset.exists() or profile_path.exists():
        raise SystemExit("Refusing to overwrite a frozen fixture/profile; create a new version.")
    rows = [{"id": f"h{i:02d}", "parent_id": f"holdout-parent-{i:02d}", "slice": category,
             "source": source, "hypothesis": hypothesis, "language_pair": "en-es",
             "gold": {"error_types": [kind], "worst_severity": severity,
                      "adequacy": {"neutral": 5, "minor": 4, "major": 3, "critical": 2}[severity]},
             "label_origin": "agent_authored_provisional_pre_inference", "reference_rationale": rationale,
             "expert_adjudicated": False}
            for i, (category, source, hypothesis, kind, severity, rationale) in enumerate(CASES, 1)]
    dataset.write_bytes(("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)).encode("utf-8"))
    profile = json.loads((ROOT / "evals/profiles/local-lmstudio.json").read_text(encoding="utf-8"))
    profile["name"] = "local-lmstudio-qwen3-14b-rubric-v2"
    profile["request"]["prompt_suffix"] = CANDIDATE
    profile["purpose"] = "One frozen prompt/rubric clarification candidate; same weights/runtime as baseline; no post-holdout tuning"
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(rows)} new source parents and one candidate profile. No model was queried.")


if __name__ == "__main__":
    main()
