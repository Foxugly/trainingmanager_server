import factory
from factory.django import DjangoModelFactory

from django.contrib.auth import get_user_model

from event.models import Event
from exercise.models import EnergySegment, EnergySystem, Exercise, Modality
from level.models import Level
from member.models import Member
from program.models import Program
from roti.models import Roti
from round.models import Round
from sport.models import Sport
from team.models import Team


class UserFactory(DjangoModelFactory):
    class Meta:
        model = get_user_model()
        # set_password is a post_generation hook that calls .save() itself;
        # opt out of factory_boy's implicit second save (deprecated in
        # current versions, removed in next major) — eliminates a noisy
        # DeprecationWarning without changing semantics.
        skip_postgeneration_save = True

    # Email-only: no username column. email is the USERNAME_FIELD. Tests that
    # used to pass username= now pass email= (or rely on the Sequence).
    email = factory.Sequence(lambda n: f"user_factory_{n}@local.test")
    is_active = True
    email_confirmed = True
    password = factory.PostGenerationMethodCall("set_password", "pass1234")


class SportFactory(DjangoModelFactory):
    class Meta:
        model = Sport
        django_get_or_create = ("slug",)

    name = factory.Sequence(lambda n: f"Sport {n}")
    slug = factory.Sequence(lambda n: f"sport-{n}")
    is_active = True


class LevelFactory(DjangoModelFactory):
    class Meta:
        model = Level
        django_get_or_create = ("code",)

    code = factory.Sequence(lambda n: f"level-{n}")
    name = factory.Sequence(lambda n: f"Level {n}")
    description = "test level"
    order = factory.Sequence(lambda n: n)
    is_active = True


class TeamFactory(DjangoModelFactory):
    class Meta:
        model = Team
        skip_postgeneration_save = True

    name = factory.Sequence(lambda n: f"Team {n}")
    owner = factory.SubFactory(UserFactory)
    is_active = True
    is_public = False

    @factory.post_generation
    def sport(self, create, extracted, **kwargs):
        """Multi-sport: attach the team's default sport via a TeamSport row.

        `TeamFactory(sport=<Sport>)` flags that sport default; omitted → a fresh
        SportFactory (matches the legacy single-sport default)."""
        if not create:
            return
        from team.models import TeamSport

        sport = extracted if extracted is not None else SportFactory()
        TeamSport.objects.create(team=self, sport=sport, is_default=True)


class ModalityFactory(DjangoModelFactory):
    class Meta:
        model = Modality

    name = factory.Sequence(lambda n: f"Modality {n}")
    sport = factory.SubFactory(SportFactory)


class EnergySystemFactory(DjangoModelFactory):
    class Meta:
        model = EnergySystem

    name = factory.Sequence(lambda n: f"ES {n}")


class EnergySegmentFactory(DjangoModelFactory):
    class Meta:
        model = EnergySegment

    abv = factory.Sequence(lambda n: f"abv{n}")
    description = "test segment"
    energysystem = factory.SubFactory(EnergySystemFactory)


class MemberFactory(DjangoModelFactory):
    class Meta:
        model = Member
        skip_postgeneration_save = True

    firstname = factory.Sequence(lambda n: f"First{n}")
    lastname = factory.Sequence(lambda n: f"Last{n}")
    email = factory.LazyAttribute(lambda o: f"{o.firstname}.{o.lastname}@local.test".lower())
    phonenumber = "+32400000000"

    @factory.post_generation
    def teams(self, create, extracted, **kwargs):
        if not create or not extracted:
            return
        from team.models import TeamMembership

        for team in extracted:
            TeamMembership.objects.create(team=team, member=self)


class RotiFactory(DjangoModelFactory):
    class Meta:
        model = Roti

    event = factory.SubFactory("tests.factories.EventFactory")
    member = factory.SubFactory(MemberFactory)
    score = 3


class ProgramFactory(DjangoModelFactory):
    class Meta:
        model = Program

    name = factory.Sequence(lambda n: f"Program {n}")
    team = factory.SubFactory(TeamFactory)
    is_active = True


class EventFactory(DjangoModelFactory):
    class Meta:
        model = Event
        skip_postgeneration_save = True

    name = factory.Sequence(lambda n: f"Event {n}")
    refer_program = factory.SubFactory(ProgramFactory)

    @factory.post_generation
    def rounds(obj, create, extracted, **kwargs):
        if not create or not extracted:
            return
        for r in extracted:
            obj.rounds.add(r)


class RoundFactory(DjangoModelFactory):
    class Meta:
        model = Round
        skip_postgeneration_save = True

    order = 1
    count = 1
    sport = factory.SubFactory(SportFactory)
    language = "fr"

    @factory.post_generation
    def event(obj, create, extracted, **kwargs):
        if not create:
            return
        if extracted is not None:
            extracted.rounds.add(obj)

    @factory.post_generation
    def exercises(obj, create, extracted, **kwargs):
        if not create or not extracted:
            return
        for ex in extracted:
            obj.exercises.add(ex)


class ExerciseFactory(DjangoModelFactory):
    class Meta:
        model = Exercise
        skip_postgeneration_save = True

    order = 1
    repetition = 1
    distance = 100
    language = "fr"

    @factory.post_generation
    def round(obj, create, extracted, **kwargs):
        if not create:
            return
        if extracted is not None:
            extracted.exercises.add(obj)
