from django.db.models import Q

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
