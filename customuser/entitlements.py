"""Source unique du verdict de droits payants (spec lot A §A.3).

TrainingManager n'a pas de facturation : le seul verrou est CustomUser.team_quota,
relevé à la main par un admin. Ce module existe pour que les trois appelants qui
recalculaient le verdict (le modèle, la vue de création d'équipe, /me/) partagent
la même règle, et pour que le jour où Stripe est porté ici, seul l'intérieur de
ces fonctions change.
"""

# Quota "illimité" : valeur haute plutôt qu'un None, pour que les comparaisons
# numériques des appelants restent valides sans cas particulier.
UNLIMITED = 10_000


def user_quota(user) -> int:
    """Nombre maximum d'équipes actives que l'utilisateur peut posséder."""
    if getattr(user, "subscription_bypass", False):
        return UNLIMITED
    return user.team_quota


def can_create_team(user) -> bool:
    return user.active_owned_teams_count() < user_quota(user)
