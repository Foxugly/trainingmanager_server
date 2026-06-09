# trainingmanager-server

Backend API REST pour TrainingManager — application de planification d'entraînements sportifs.

## Stack

- Python 3.12+ (testé sur 3.14)
- Django 6.0
- Django REST Framework + drf-spectacular (OpenAPI)
- JWT auth (djangorestframework-simplejwt)
- django-allauth (signup + email verification + headless mode)
- Anthropic API (Claude Haiku 4.5 par défaut)
- PostgreSQL (dev et prod ; convention `DB_*` 6 variables — voir `.env.example`)
- Email backend : Microsoft Graph API
- pytest, factory-boy, ruff, black, pre-commit

## Setup local

### Prérequis

- Python 3.12+
- gettext (pour i18n) — sur Windows : https://mlocati.github.io/articles/gettext-iconv-windows.html ou `choco install gettext`
- Une API key Anthropic (https://console.anthropic.com/)
- Un app registration Azure AD pour Microsoft Graph (envoi d'emails)

### Installation

```bash
git clone https://github.com/Foxugly/trainingmanager_server
cd trainingmanager_server
python -m venv .venv
source .venv/bin/activate    # Linux/Mac
.venv\Scripts\activate       # Windows
pip install -r requirements.txt
pip install -r requirements-dev.txt
cp .env.example .env
# Éditer .env : remplir SECRET_KEY, ANTHROPIC_API_KEY, GRAPH_*, FRONTEND_URL
python manage.py migrate            # crée le schéma + seed les référentiels (Sport, Modality, EnergySystem/Segment, AttendanceStatus) via data migrations
python manage.py createsuperuser    # compte admin pour /admin/
python manage.py runserver
```

Doc Swagger UI : http://localhost:8000/api/v1/docs/

### Pre-commit hooks

```bash
pip install pre-commit
pre-commit install
```

Ruff (lint+format) et Black tournent à chaque commit.

## Configuration prod

`manage.py` et `wsgi.py` utilisent `os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'django-trainingmanager.settings.dev')` — le défaut est `dev` pour faciliter le développement local. En production, exporter `DJANGO_SETTINGS_MODULE` avant de lancer le serveur :

```bash
export DJANGO_SETTINGS_MODULE=django-trainingmanager.settings.prod
gunicorn django-trainingmanager.wsgi:application
```

ou en CLI ponctuelle : `python manage.py <cmd> --settings=django-trainingmanager.settings.prod`.

## Variables d'environnement

Voir `.env.example`. Critiques :

| Variable | Description |
|---|---|
| `SECRET_KEY` | Clé Django, à régénérer |
| `DEBUG` | `True` en dev, `False` en prod |
| `DB_ENGINE` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` / `DB_HOST` / `DB_PORT` | Connexion PostgreSQL (convention `DB_*` unifiée de la flotte) |
| `FRONTEND_URL` | URL du frontend (utilisée dans les emails) |
| `TRUSTED_PROXY_COUNT` | Nb de reverse-proxies de confiance pour résoudre l'IP client (X-Forwarded-For) ; défaut `1` |
| `ANTHROPIC_API_KEY` | Clé Anthropic pour les endpoints IA |
| `ANTHROPIC_MODEL_DEFAULT` | Default : `claude-haiku-4-5-20251001` |
| `GRAPH_TENANT_ID` | Tenant Azure AD |
| `GRAPH_CLIENT_ID` | App ID |
| `GRAPH_CLIENT_SECRET` | Secret de l'app |
| `GRAPH_SENDER` | Adresse email d'envoi |

## Structure

```
program/      # Program (ex-Agenda) — regroupe des Events
event/        # Séances d'entraînement (rattachées à un Program)
round/        # Séries d'exercices au sein d'un Event
exercise/     # Exercices individuels (Modality, EnergySegment)
member/       # Athlètes
customuser/   # Extension du User Django (language, is_*_admin)
team/         # Teams (multi-sport via TeamSport M2M + default, language, owner, managers, athlètes, créneaux, lieux, équipement)
sport/        # Sports + Modalities
attendance/   # Présences aux séances (Attendance + AttendanceStatus référentiel)
messaging/    # Discussions d'équipe (Topic / Message / TopicRead — badge non-lu)
attachment/   # Pièces jointes génériques (contenttypes, S3)
place/        # Pool de lieux (Lieu) partagé par sport
equipment/    # Catalogue d'équipement
level/        # Niveaux d'équipe (référentiel)
note/         # Notes coach sur les membres
roti/ rsvp/   # ROTI (ressenti séance) + RSVP
notifications/# Notifications in-app + cloche
performance/  # Records de performance des athlètes
audit/        # Journal d'audit
dashboard/    # Endpoint agrégat /api/v1/dashboard/summary/
ai/ aiusage/  # Endpoints IA + suivi de consommation
tools/        # i18n, throttling, exceptions, ai client, middleware, email
tests/        # pytest + factory_boy
locale/       # Translations (fr complet, nl/it/es à compléter)
```

## Endpoints principaux

| Méthode | URL | Permission | Description |
|---|---|---|---|
| POST | `/api/v1/auth/token/` | Public | JWT login |
| GET, PATCH | `/api/v1/me/` | Auth | Profil user courant (incl. language) |
| POST | `/api/v1/auth/password/change/` | Auth | Changer son mot de passe (current + new) |
| GET, POST | `/api/v1/teams/` | Auth | Liste / créer team |
| GET, POST | `/api/v1/programs/` | Auth | Programs |
| POST | `/api/v1/programs/{id}/generate-events/` | Manager | Génération plan IA |
| POST | `/api/v1/events/{id}/generate-training/` | Manager | Génération séance IA |
| GET, POST | `/api/v1/exercises/` | Auth (write : trainer) | Exercises |
| POST | `/api/v1/exercises/{id}/clone/` | Trainer | Cloner exercise |
| POST | `/api/v1/rounds/{id}/clone/` | Trainer | Cloner round |
| POST | `/api/v1/join-requests/` | Auth | Demander à rejoindre une team |
| POST | `/api/v1/invitations/` | Trainer | Pré-inscrire un athlète |
| GET, POST | `/api/v1/invitations/lookup/<token>/` | Public | Finaliser invitation |
| GET, PUT | `/api/v1/events/{id}/roti/` | Auth (member/coach) | ROTI : résumé (incl. my_score) / upsert son propre score (1..5, si `roti_enabled`) |
| GET | `/api/v1/events/{id}/roti/summary/` | Auth (member/coach) | Agrégat ROTI (average/count/distribution) |
| GET, PUT | `/api/v1/events/{id}/rsvp/` | Auth (member/coach) | RSVP : résumé (counts + my_status, by_member pour managers) / upsert sa propre dispo (going/maybe/not_going, si `rsvp_enabled`) |
| POST | `/api/v1/events/{id}/rsvp/apply_to_attendance/` | Manager | Pré-remplir l'attendance depuis les RSVPs |
| POST | `/api/v1/ai/ping/` | Trainer | Test API Anthropic |
| GET | `/api/v1/sports/` | Auth | Liste des sports |
| GET | `/api/v1/sports/<id>/modalities/` | Auth | Modalities d'un sport |

Détail complet sur `/api/v1/docs/` (Swagger UI).

## Authentification

### Flux signup classique (Flux A)

1. `POST /api/v1/_allauth/app/v1/auth/signup/` — créer compte
2. Email de vérification envoyé via Graph
3. `POST /api/v1/_allauth/app/v1/auth/email/verify/` — confirmer
4. `POST /api/v1/auth/token/` — JWT access+refresh
5. Optionnel : `POST /api/v1/join-requests/` pour rejoindre une team publique

### Flux invitation par trainer (Flux B)

1. Trainer : `POST /api/v1/invitations/` avec firstname/lastname/email
2. Système crée `Member`, envoie email avec token
3. Athlète clique le lien : `GET /api/v1/invitations/lookup/<token>/`
4. Athlète choisit username/password : `POST /api/v1/invitations/lookup/<token>/`
5. Réponse contient access+refresh JWT (auto-login)

## Tests

```bash
pytest
pytest --tb=short
```

## Catalogue partagé par sport et langue

`Exercise` et `Round` forment un catalogue **partagé entre toutes les teams partageant le même couple (sport, langue)**. Concrètement :

- Un coach Natation francophone voit **tous** les Exercises et Rounds Natation+français, peu importe la team d'origine.
- Un coach Natation italophone ne voit pas les Rounds Natation+français (sauf si une de ses teams est aussi en français).
- Un coach Course à pied ne voit pas les Exercises de Natation, et inversement (peu importe la langue).
- L'enrichissement collectif est encouragé entre teams compatibles : un Exercise créé par un coach bénéficie à tous les coaches partageant son couple (sport, langue).
- Le mécanisme **lock + clone** protège l'intégrité : un Exercise utilisé dans 2+ Rounds (ou un Round utilisé dans 2+ Events) devient immutable. Pour modifier, il faut le cloner via `POST /api/v1/exercises/{id}/clone/` ou `POST /api/v1/rounds/{id}/clone/`. Le clone hérite de `(sport, language)` de l'original.
- Un `Round` ou `Exercise` généré par IA hérite de `(sport, language)` de la team du Program/Event.
- `get_or_create` sur Exercise (générateur IA) inclut `language` dans la clé d'unicité : un même libellé en FR et en IT sont **deux entrées distinctes** du catalogue.
- **Permission d'écriture** : owner ou manager d'au moins une team active (permission `IsTrainer`).
- **Permission de lecture** : tout user authentifié, **scopé par (sport, langue)** via `team.utils.user_accessible_sport_language_pairs` (l'union des couples des teams où le user est owner, manager, ou athlète).
- `Round` porte un FK `sport` explicite (PROTECT) et un champ `language` (CharField choices=settings.LANGUAGES). La validation refuse qu'un exercise d'un sport ou d'une langue différente soit attaché à un Round.

## i18n

- **Source** : anglais (`gettext_lazy(_("..."))` partout)
- **Langues supportées** : `fr`, `nl`, `en`, `it`, `es`
- **`LANGUAGE_CODE = 'en'`** (fallback technique). `Team.language` et `CustomUser.language` ont `default='fr'`.
- **Résolution langue requête** : `user.language` > `Accept-Language` > `LANGUAGE_CODE`. Le middleware `tools.middleware.UserLanguageMiddleware` force la langue de l'utilisateur authentifié sur tout le cycle de la requête.
- **Format erreur** : `{"code": "snake_case", "detail": "<localisé>"}`. Le frontend peut matcher sur `code` (identifiant stable) ou afficher `detail` (déjà localisé).
- **Traductions** : `locale/<lang>/LC_MESSAGES/django.po`. `fr.po` est complet ; `nl/it/es` sont des stubs (header seul, fallback EN).

```bash
# Après install gettext :
django-admin makemessages -l fr -l nl -l en -l it -l es \
  --ignore=.venv --ignore=migrations
django-admin compilemessages
```

## Roadmap technique

### Fait

- DRF API-only refactor
- JWT + allauth headless
- Teams + permissions (owner / manager / athlete)
- Lock + Clone sur catalogue d'exercices et de rounds
- Self-signup et trainer invitation
- Sport + Modality
- Génération IA plan + entraînement (Anthropic Claude)
- Throttling endpoints IA (UserRateThrottle scopés)
- Pre-commit hooks (ruff + black)
- i18n niveau 1 (Team.language, User.language) et niveau 2 (codes d'erreur structurés, FR traduit)
- ENUM_NAME_OVERRIDES sur drf-spectacular pour codegen TypeScript propre
- Sentry monitoring (backend + frontend)
- CI/CD GitHub Actions (OIDC → AWS SSM, auto-deploy sur push `main` ; matrix de tests sqlite + postgres)
- Secrets via AWS SSM Parameter Store → `/run/<app>/.env` (tmpfs), jamais sur disque
- Cloudflare Turnstile (register / forgot-password)

### À venir

- Tests permissions affinés
- Translations nl, it, es à compléter
- Multi-langue templates email
