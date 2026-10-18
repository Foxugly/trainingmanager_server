from django.db.models import Exists, OuterRef, Q

from .models import Team


def user_accessible_sport_language_pairs(user):
    """(sport_id, language) tuples for which the user has team membership.

    Membership = owner, manager, or athlete on an active team. Multi-sport: a
    team contributes one pair per sport it practises (the union of its sports ×
    its language). Used to scope catalog reads by both sport AND language.
    """
    if not user.is_authenticated:
        return []
    teams = Team.objects.filter(
        Q(owner=user)
        | Q(managers=user)
        | Q(memberships__member__user=user, memberships__left_at__isnull=True),
        is_active=True,
    ).distinct()
    return list(
        teams.filter(team_sports__sport__isnull=False)
        .values_list("team_sports__sport_id", "language")
        .distinct()
    )


def scope_by_sport_language(queryset, user, sport_field="sport_id"):
    """Filter a queryset by the (sport, language) pairs accessible to the user.

    The queryset's model must have a `language` field and a sport reference
    matching `sport_field` (default 'sport_id'; pass 'modality__sport_id'
    for Exercise where sport is reached via Modality).

    Returns `queryset.none()` when the user has no team membership.
    """
    pairs = user_accessible_sport_language_pairs(user)
    if not pairs:
        return queryset.none()
    query = Q()
    for sport_id, lang in pairs:
        query |= Q(**{sport_field: sport_id, "language": lang})
    return queryset.filter(query)


def scope_rounds_to_user_teams(queryset, user):
    """Restrict Rounds to library rounds + rounds of the caller's own teams.

    A round is *team content* when an Event or a session EventTemplate links
    it; everything else is shared library content. A round linked only to
    events / templates of teams the user does not belong to (owner, manager,
    active member) is hidden, even when sport and language match. Combine
    with `scope_by_sport_language`.
    """
    from event.models import Event, EventTemplate

    from .queries import user_member_teams

    teams = user_member_teams(user)
    event_links = Event.rounds.through.objects.filter(round_id=OuterRef("pk"))
    template_links = EventTemplate.rounds.through.objects.filter(round_id=OuterRef("pk"))
    return queryset.filter(
        (~Exists(event_links) & ~Exists(template_links))
        | Exists(event_links.filter(event__refer_program__team__in=teams))
        | Exists(template_links.filter(eventtemplate__team__in=teams))
    )


def scope_exercises_to_user_teams(queryset, user):
    """Restrict Exercises to those held by no round, or by at least one round
    the caller may read (see `scope_rounds_to_user_teams`).

    Exercises form a shared library (session rounds re-link them), so an
    exercise is hidden only when every round holding it is team content of
    teams the user does not belong to.
    """
    from round.models import Round

    holdings = Round.exercises.through.objects
    readable_rounds = scope_rounds_to_user_teams(Round.objects.all(), user)
    return queryset.filter(
        Exists(holdings.filter(exercise_id=OuterRef("pk"), round__in=readable_rounds))
        | ~Exists(holdings.filter(exercise_id=OuterRef("pk")))
    )
