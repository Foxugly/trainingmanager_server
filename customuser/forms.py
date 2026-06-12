from django.contrib.auth.forms import UserChangeForm, UserCreationForm

from customuser.models import CustomUser


class CustomUserCreationForm(UserCreationForm):
    """Email-only creation form. Django's stock UserCreationForm is hardwired to
    ``username``; override Meta to key on ``email`` instead (the USERNAME_FIELD).
    """

    class Meta:
        model = CustomUser
        fields = ("email",)


class CustomUserChangeForm(UserChangeForm):
    class Meta:
        model = CustomUser
        fields = "__all__"
