# AUDIT — django-trainingmanager

> ⚠️ **DOCUMENT HISTORIQUE — NE PAS S'Y FIER POUR L'ÉTAT ACTUEL.**
> Instantané pré-migration daté du **2026-04-27**, conservé pour mémoire. Le
> dépôt a depuis été reconstruit sur `main`, scindé en
> `Foxugly/trainingmanager_server` (V2 API-only), migré sur PostgreSQL et
> déployé via GitHub Actions → AWS SSM. Pour l'état courant, voir `README.md`
> et `CLAUDE.md`.

Audit complet du dépôt avant migration. Snapshot pris le 2026-04-27.

---

## 1. Versions

### Python
- **Aucun fichier `runtime.txt`, `.python-version`, ou `pyproject.toml`** — pas de version Python épinglée dans le repo.
- `requirements.txt` ne contient pas de marqueur `python_requires`.
- Le venv local (`.venv/`) tourne sous **Python 3.14.4** (`.venv/Scripts/python.exe --version`).
- Un second venv `venv/` est aussi présent dans le repo (probablement legacy / non utilisé).

### Django
- **Spec dans `requirements.txt`** : `Django>=4.2.1` (borne basse uniquement, pas de borne haute → installation flottante).
- **Version réellement installée dans `.venv`** : **Django 6.0.4** (`pip list`).
- ⚠️ **Écart important** : la contrainte autorise n'importe quelle version >= 4.2.1, et l'environnement local a déjà sauté en 6.0. La question « migration vers Django 5.x » est donc partiellement caduque — l'app *fonctionne potentiellement déjà* sous 6.0 (ou plante au démarrage, à vérifier en lançant `runserver`). Le code source, lui, n'a pas été adapté (voir §8).

---

## 2. Structure

```
django-trainingmanager/
├── manage.py
├── common_tags.py                 # tags template projet (hash, verbose_name, app_name)
├── requirements.txt
├── db.sqlite3                     # ⚠️ DB committée
├── django-trainingmanager/        # ⚠️ package projet AVEC TIRET
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── agenda/                        # app Django
├── event/                         # app Django
├── round/                         # app Django
├── exercise/                      # app Django
├── member/                        # app Django
├── customuser/                    # app Django (AUTH_USER_MODEL)
├── tools/                         # scaffolding générique (pas une app Django)
│   ├── generic_class.py           # base abstraite GenericClass
│   ├── generic_views.py           # GenericCreate/List/Update/Detail/DeleteView
│   ├── generic_urls.py            # introspection URL par suffixe de classe
│   └── buildclass.py              # générateur de boilerplate
├── templates/                     # 22 templates HTML (dont 8 dans registration/)
├── static/                        # static collecté (admin/, debug_toolbar/, etc.)
├── locale/                        # traductions fr, nl
├── .venv/                         # venv principal (Python 3.14.4, Django 6.0.4)
├── venv/                          # ⚠️ venv legacy également dans le repo
└── .idea/                         # config PyCharm (non gitignorée)
```

**Apps Django (6) déclarées dans `INSTALLED_APPS`** :
- `agenda`, `event`, `round`, `exercise`, `member`, `customuser`

**Apps tierces dans `INSTALLED_APPS`** :
- `bootstrap_modal_forms`, `widget_tweaks`, `qr_code`, `debug_toolbar`, `hijack`, `hijack.contrib.admin`, `bootstrap4`, `wkhtmltopdf`

---

## 3. Dépendances

Source : `requirements.txt`. Les versions « stable connue » sont indicatives (cutoff knowledge = janvier 2026) ; à recroiser avec `pip index versions <pkg>` au moment de la migration.

| Paquet                         | Pin actuel  | Installé (.venv) | Dernière stable approx. | Compat Django 5.x ? |
|--------------------------------|-------------|------------------|--------------------------|---------------------|
| `Django`                       | `>=4.2.1`   | `6.0.4`          | 6.0.x / 5.2 LTS          | ✅ déjà en 6.0 |
| `django-bootstrap-modal-forms` | `==2.0.0`   | `2.0.0`          | `2.2.0` (≈)              | ⚠️ peu maintenu, à valider |
| `django-widget-tweaks`         | `==1.4.2`   | `1.4.2`          | `1.5.x`                  | ✅ |
| `selenium`                     | `==3.14.0`  | `3.14.0`         | `4.x`                    | N/A (test only) — **3.14 EOL**, à passer en 4.x |
| `pytz`                         | `==2018.5`  | `2018.5`         | obsolète                 | ⚠️ Django 4+ utilise `zoneinfo` (stdlib) ; `pytz` est facultatif |
| `django-wkhtmltopdf`           | (libre)     | `3.4.0`          | `3.4.0` (≈ 2018, abandonné) | ❌ projet abandonné, pas de release récente — risque sur Django ≥ 5 |
| `django-hijack`                | (libre)     | `3.7.8`          | `3.7.x`                  | ✅ |
| `django-hijack-admin`          | (libre)     | `2.1.10`         | déprécié                 | ❌ **fusionné dans `django-hijack` 3.x** (`hijack.contrib.admin`) — paquet à supprimer |
| `django-qr-code`               | (libre)     | `4.2.0`          | `4.x`                    | ✅ |
| `django-debug-toolbar`         | (libre)     | `6.3.0`          | `6.x`                    | ✅ |
| `django-bootstrap4`            | (libre)     | `26.1`           | `26.x`                   | ✅ (mais Bootstrap 4 lui-même est EOL côté CSS) |

**Paquets installés mais absents de `requirements.txt`** :
- `django-compat 1.0.15` (compat Django <2 / Python 2 — **obsolète**, à dégager)
- `pydantic`, `beautifulsoup4`, `annotated-types`, etc. : transitifs, ignorables.

**Recommandations dépendances** :
1. Borner haut Django (`Django>=5.2,<6.1` par ex.) pour éviter les sauts de version surprises.
2. Supprimer `django-hijack-admin`, `django-compat`, `pytz`.
3. Remplacer `django-wkhtmltopdf` (abandonné) par `WeasyPrint` ou `xhtml2pdf`.
4. Mettre à jour `selenium` 3.14 → 4.x (les tests n'existent pas, donc risque nul).
5. Geler les versions exactes dans un `requirements.lock` une fois la matrice validée.

---

## 4. Settings

**Fichier unique** : `django-trainingmanager/settings.py` — **monolithique**, pas de split base/dev/prod.

| Paramètre        | Valeur                                                  | Verdict |
|------------------|---------------------------------------------------------|---------|
| `DEBUG`          | `True` en dur (ligne 8)                                  | ❌ committé à `True`, aucune lecture d'env |
| `SECRET_KEY`     | string littérale en dur (ligne 6)                        | ❌ committée dans le repo (compromise de fait) |
| `ALLOWED_HOSTS`  | `['*']`                                                  | ❌ accepte tout — OK en dev, danger en prod |
| `DATABASES`      | SQLite (`db.sqlite3` à côté de `manage.py`)              | ⚠️ DB committée, pas de Postgres |
| `STATE`          | `'INT'` en dur (custom — utilisé par `context_processors.debug`) | ⚠️ devrait venir d'une env var |
| `WEBSITE`        | `"www.example.com"`                                      | ⚠️ placeholder, utilisé par `GenericClass.get_full_url()` |
| `LANGUAGE_CODE`  | `'en-us'`                                                | OK |
| `TIME_ZONE`      | `'UTC'`                                                  | OK |
| `USE_TZ`         | `True`                                                   | OK |
| `USE_I18N`       | `True`                                                   | OK |
| `USE_L10N`       | `True`                                                   | ❌ **déprécié Django 4.0, retiré Django 5.0** — à supprimer |
| `STATIC_ROOT` / `STATICFILES_DIRS` | switch manuel par commentaires (lignes 117–120) | ⚠️ pas de bascule auto basée sur `DEBUG` |
| `WKHTMLTOPDF_CMD`| `'xvfb-run /usr/bin/wkhtmltopdf'`                        | ⚠️ Linux-only, casse sur Windows |
| `AUTH_USER_MODEL`| `"customuser.CustomUser"`                                | OK |
| `DEFAULT_AUTO_FIELD` | `'django.db.models.BigAutoField'`                    | OK |
| `DEBUG_TOOLBAR_CONFIG` | `SHOW_TOOLBAR_CALLBACK` pointe vers **`django_timesheets.settings.show_toolbar`** (ligne 140) | ❌ **référence cassée** — module inexistant (probablement copié-collé d'un autre projet) |

**Recommandations settings** :
1. Splitter en `settings/{base,dev,prod}.py`.
2. Lire `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `DATABASE_URL` depuis l'env (ex. `django-environ`).
3. Régénérer `SECRET_KEY` (l'actuelle est compromise).
4. Supprimer `USE_L10N`.
5. Corriger `SHOW_TOOLBAR_CALLBACK` (`'django-trainingmanager.settings.show_toolbar'` ou autre).
6. Bouger `WKHTMLTOPDF_CMD` dans `settings/prod.py`.

---

## 5. Frontend actuel

✅ **Confirmé : Django Templates** classiques, aucun framework JS (pas de `package.json`, pas de `node_modules`, pas de bundler).

- `templates/` (racine projet, déclaré dans `TEMPLATES.DIRS`) : **22 fichiers HTML**
  - Génériques CRUD : `list.html`, `update.html`, `detail.html`, `delete.html`, `modal.html`, `modal_round.html`, `_modal.html`, `_modal_large.html`
  - Layout : `base.html`, `_header.html`, `_footer.html`, `index.html`
  - Métier : `agenda.html`, `event.html`, `event_raw.html`
  - `templates/registration/` : 8 fichiers (login + password reset/change flows)
- Aucune surcharge `<app>/templates/` côté apps custom — tout est centralisé.
- `static/` : majoritairement du contenu **collecté** (`admin/`, `debug_toolbar/`, vendor) — pas d'assets statiques propres au projet identifiés. `STATIC_ROOT == STATIC_DIRS == BASE_DIR/static` : risque d'écrasement par `collectstatic`.
- Côté CSS/JS runtime : Bootstrap 4 + jQuery 3.3.1 chargés via CDN (config `BOOTSTRAP4` dans `settings.py`). Bootstrap 4 lui-même est **EOL** depuis janvier 2023.

---

## 6. Apps custom

| App          | Modèles                                       | Description (1 ligne) |
|--------------|-----------------------------------------------|------------------------|
| `agenda`     | `Agenda`                                      | Calendrier regroupant des `Event`s et des `Member`s, expose un endpoint JSON pour rendu type FullCalendar. |
| `event`      | `Event`                                       | Séance de training datée (heure début/fin, couleur, distance totale) rattachée à un `Agenda`, contient des `Round`s et des `Member`s présents. |
| `round`      | `Round`                                       | Bloc ordonné dans un `Event` (compteur de répétitions, temps de départ/repos), agrège des `Exercise`s. |
| `exercise`   | `Exercise`, `Stroke`, `EnergySystem`, `EnergySegment` | Unité atomique d'exercice (distance × répétitions, nage, segment énergétique) ; trois lookups pour styles de nage et zones d'effort. |
| `member`     | `Member`                                      | Adhérent (nom, email, téléphone) inscrit aux `Agenda`s et marqué présent sur les `Event`s. |
| `customuser` | `CustomUser`, `CustomUserManager`             | Utilisateur Django étendu avec une langue préférée et un flag `is_foo_admin` ; sert de `AUTH_USER_MODEL`. |

Hiérarchie domaine : `Agenda → Event → Round → Exercise` (ForeignKey + `related_name='back_*'`) et symétriquement par M2M (`events`, `rounds`, `exercises`).

---

## 7. Tests

**Verdict : aucun test métier.**

- 5 fichiers `tests.py` présents : `agenda/`, `event/`, `exercise/`, `member/`, `round/`.
- Tous **vides** (contiennent uniquement `# Create your tests here.`) — ce sont les stubs auto-générés par `startapp`.
- Pas de répertoire `tests/`, pas de `pytest.ini`, pas de `conftest.py`.
- `customuser/` n'a même pas de `tests.py`.
- `selenium==3.14.0` est dans `requirements.txt` mais aucun test fonctionnel n'utilise selenium.

**Couverture estimée : 0 %.** Aucun filet de sécurité avant migration → toute régression passera silencieusement.

**Recommandation** : avant la migration, écrire au minimum un smoke test par vue CRUD générée (≈ 25 vues × 5 modèles = priorité aux List + Detail) pour détecter les régressions de routing après mise à jour de Django.

---

## 8. Dette technique évidente

### Bloquants pour Django ≥ 4.1
- ❌ **`request.is_ajax()`** : retiré en Django 3.1. Trois usages :
  - `agenda/views.py:70` (`create_events`)
  - `member/views.py:25`
  - `round/views.py:43`
- ❌ **`USE_L10N = True`** dans `settings.py:100` : déprécié 4.0, **retiré en Django 5.0**. Sous Django 6.0.4 (installé), génère a minima un warning.

### Bloquants probables
- ❌ `DEBUG_TOOLBAR_CONFIG.SHOW_TOOLBAR_CALLBACK` pointe vers `django_timesheets.settings.show_toolbar` (`settings.py:140`) — module qui n'existe pas dans ce repo (copié-collé d'un autre projet). Casse l'init de la debug toolbar.
- ⚠️ `selenium 3.14.0` + `pytz 2018.5` : versions très anciennes, risque d'incompatibilité avec Python 3.14 (le venv tourne déjà en 3.14.4).
- ⚠️ `django-wkhtmltopdf` : projet **abandonné** (dernière release 2018), inconnu compat Django ≥ 5.

### Anti-patterns code
- `from tools.generic_views import *` (wildcard imports) dans `agenda/`, `event/`, `member/`, `exercise/`, `round/` `views.py` — masque les dépendances et réimporte `reverse_lazy`, `_`, etc. par effet de bord.
- `print("round:form_valid")`, `print("exform")` dans `round/views.py:42,50` — debug oublié en prod.
- `tools/generic_class.py:GenericClass.__init__` accède à `self._meta` à **chaque instanciation** pour reconstruire les noms d'URL — surcoût inutile (pourrait être `@classmethod` ou attributs lazy).
- `tools/generic_urls.py:add_url_from_generic_views` dépend du **suffixe textuel** des classes (`CreateView`, `ListView`, etc.) : silencieux si la convention est cassée. Aucun test ne le couvre.
- `agenda/models.py:38` : `verbose_name = _('Agenda    ')` — espaces à droite dans le label traduisible (probable typo).
- `event/models.py:53–74` : génération HTML par concaténation de strings dans le modèle (`get_table`, `get_table_raw`, `get_title_raw`) — XSS-risqué si `Member.firstname/lastname` ou `Event.name` contiennent du HTML, et viole la séparation modèle/vue.
- `round/models.py:23–47` : idem, `get_row()` retourne du HTML brut.
- `customuser/views.py:16–18` : `widget.attrs['readonly']` + `disabled` posés côté widget seulement — un POST forgé peut quand même réécrire `is_foo_admin` / `is_superuser` (validation côté serveur absente).
- `agenda/views.py:64`, `qdict_to_dict` : utilitaire ad-hoc qu'on retrouve typiquement dupliqué — ici unique mais à surveiller.

### Hygiène repo
- `db.sqlite3` (410 KB) présent dans le working tree mais correctement ignoré par `.gitignore` (jamais committé).
- `.idea/` non ignoré (apparaît dans `git status`).
- Deux venvs (`venv/` et `.venv/`) coexistent — `.venv` est dans `.gitignore` mais `venv/` ne l'est pas explicitement (sauvé par la règle générique `venv/`).
- Migrations **non commitées** (cf. `git status` : 9 fichiers `0001_initial.py` / `0002_initial.py` untracked) — un clone propre ne pourra pas reproduire le schéma de la DB sans `makemigrations`. (Note 2026-05 : les migrations sont désormais commitées et seedent les référentiels — ce point est résolu.)
- `LICENSE` (35 KB) et `LICENSE.txt` (1 KB) coexistent — doublon.
- `tools/buildclass.py` : générateur qui imprime du code dépendant de `view_breadcrumbs` (paquet absent de `requirements.txt`) — le script lui-même tourne, mais le code généré ne s'exécutera pas tel quel.

### Sécurité
- `SECRET_KEY` committée → considérer comme compromise, à régénérer.
- `DEBUG=True`, `ALLOWED_HOSTS=['*']` committés → si déploiement prod a déjà eu lieu avec ce settings, fuite probable de stack traces et de tokens.
- `customuser/models.py:CustomUserManager.create_user` : ne hash pas le password si `set_password` plante silencieusement (OK ici car `set_password` est appelé), mais `is_active` n'est pas explicitement géré.

---

## Synthèse migration

**Verdict global** : code prêt pour Django 4.2 sur le papier, mais l'environnement local exécute déjà Django 6.0.4 alors que le code contient au minimum :
- 3 appels à `request.is_ajax()` (cassé depuis 3.1),
- `USE_L10N = True` (retiré en 5.0),
- une dépendance abandonnée (`django-wkhtmltopdf`),
- un settings `DEBUG_TOOLBAR_CONFIG` qui référence un module inexistant.

**Avant toute migration formelle** :
1. Lancer `python manage.py check` et `runserver` pour observer ce qui pète réellement sous Django 6.
2. Écrire 5–10 tests smoke (au moins un GET par ListView).
3. Corriger les 4 points ci-dessus.
4. Splitter les settings et sortir les secrets.
5. Décider du sort de `wkhtmltopdf` (remplacement ou suppression de la feature PDF).
