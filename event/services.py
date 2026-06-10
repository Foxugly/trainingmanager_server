from django.db import transaction
from django.utils import timezone


def apply_generated_training(event, ai_result, *, team_sport, team_language) -> dict:
    """Persist an AI-generated structured training onto ``event``.

    Creates fresh Rounds (stamped with ``team_sport`` / ``team_language``),
    get_or_creates the shared library Exercises and links them, attaches the
    reported equipment (matched against the team's enabled set by localized
    name), and stamps the AI lineage (generated_by_ai / ai_prompt / ai_response
    / ai_generated_at) before saving the event.

    Returns the persistence counts:
    ``{"created_rounds", "created_exercises", "reused_exercises"}``.
    """
    from exercise.models import EnergySegment, Exercise, Modality
    from round.models import Round

    created_rounds = 0
    created_exercises = 0
    reused_exercises = 0

    with transaction.atomic():
        for r_idx, r_data in enumerate(ai_result["rounds"], start=1):
            round_obj = Round.objects.create(
                sport=team_sport,
                language=team_language,
                count=r_data.get("count", 1),
                t_start=r_data.get("t_start", "00:00"),
                t_break=r_data.get("t_break", "00:00"),
                order=r_idx,
            )
            created_rounds += 1

            for ex_idx, ex_data in enumerate(r_data.get("exercises", []), start=1):
                modality = Modality.objects.get(pk=ex_data["modality_id"])
                segment = EnergySegment.objects.get(pk=ex_data["energysegment_id"])

                exercise, created = Exercise.objects.get_or_create(
                    modality=modality,
                    energysegment=segment,
                    distance=ex_data["distance"],
                    repetition=ex_data["repetition"],
                    t_start=ex_data.get("t_start", "00:00"),
                    t_break=ex_data.get("t_break", "00:00"),
                    notes=ex_data.get("notes", ""),
                    language=team_language,
                    defaults={"order": ex_idx},
                )
                if created:
                    created_exercises += 1
                else:
                    reused_exercises += 1

                round_obj.exercises.add(exercise)

            event.rounds.add(round_obj)

        # Attach the equipment the AI reported (matched against the team's
        # enabled set by localized name) and sync the canonical free-text
        # field to the joined names, so the event records what it needs.
        used_names = {n for n in (ai_result.get("equipment_used") or [])}
        if used_names:
            items = [
                e
                for e in event.refer_program.team.equipment.filter(is_active=True)
                if e.name in used_names
            ]
            if items:
                event.equipment_items.add(*items)
                event.equipment = ", ".join(sorted(i.name for i in items))

        event.generated_by_ai = True
        event.ai_prompt = ai_result["prompt_sent"]
        event.ai_response = ai_result["rationale"]
        event.ai_generated_at = timezone.now()
        event.save()

    return {
        "created_rounds": created_rounds,
        "created_exercises": created_exercises,
        "reused_exercises": reused_exercises,
    }
