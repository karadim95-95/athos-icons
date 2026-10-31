from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.http import base36_to_int

EMAIL_LINK_HOURS = 24


class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    """Links in the "confirm your email" message. They stop working once used or after 24 hours."""

    key_salt = "accounts.tokens.EmailVerificationTokenGenerator"

    def _make_hash_value(self, user, timestamp):
        return f"{user.pk}{user.email}{user.email_verified}{timestamp}"

    def check_token(self, user, token):
        if not super().check_token(user, token):
            return False
        try:
            created = base36_to_int(token.split("-")[0])
        except ValueError:
            return False
        return self._num_seconds(self._now()) - created <= EMAIL_LINK_HOURS * 3600


email_verification_token = EmailVerificationTokenGenerator()
