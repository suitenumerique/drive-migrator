"""Tests for ResanaTokenManager refreshing ahead of the offline token idle timeout."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.utils import timezone

import jwt
import pytest
import requests
from cryptography.fernet import Fernet
from freezegun import freeze_time

from core.encryption import decrypt_token, encrypt_token
from core.factories import UserFactory
from core.sources.resana.token_manager import ResanaTokenExpired, ResanaTokenManager

pytestmark = pytest.mark.django_db


def _sign(claims):
    return jwt.encode(claims, "test-secret-long-enough-for-hs256", algorithm="HS256")


def _make_access_jwt(exp_delta=timedelta(hours=3)):
    """Build a fake Resana access token JWT carrying an `exp` claim."""
    return _sign({"exp": int((timezone.now() + exp_delta).timestamp())})


def _make_offline_jwt(claims):
    """Build a fake Keycloak offline token carrying the given claims."""
    return _sign(claims)


def _rendered(log_method):
    """Return the messages logged through a mocked logger method, formatted."""
    return [call.args[0] % call.args[1:] for call in log_method.call_args_list]


def _offline_jwt_expiring_in(delta):
    """Build a fake offline token whose exp claim is its idle deadline."""
    exp = int((timezone.now() + delta).timestamp())
    return _make_offline_jwt({"typ": "Offline", "exp": exp})


def _make_user_with_idle_offline_token(settings, offline_expires_in):
    """Build a user whose access token is still valid for 2 h while the offline
    token reaches its idle deadline after offline_expires_in."""
    settings.RESANA_KEYCLOAK_ENDPOINT = "https://kc.example.com/realms/ONHEXAGONE"
    settings.RESANA_MIGRATOR_CLIENT_ID = "resana-migrator"
    settings.RESANA_MIGRATOR_CLIENT_SECRET = "s3cr3t"
    settings.RESANA_WEB_ENDPOINT = "https://resana.example.com"
    settings.OIDC_TOKENS_ENCRYPTION_KEY = Fernet.generate_key().decode()
    return UserFactory(
        resana_access_token=encrypt_token("current-access"),
        resana_refresh_token=encrypt_token(
            _offline_jwt_expiring_in(offline_expires_in)
        ),
        resana_session_id=encrypt_token("old-sess"),
        resana_csrf_token=encrypt_token("old-csrf"),
        resana_token_expires_at=timezone.now() + timedelta(hours=2),
    )


def _get_valid_token_with(
    user, keycloak_response=None, keycloak_error=None, bridge=None
):
    """Call get_valid_token() against mocked Keycloak and bridge, and return the
    token with the Keycloak mock."""
    if bridge is None:
        bridge = {
            "plateformeSessionId": "new-sess",
            "interstis_access": _make_access_jwt(),
            "csrfToken": "new-csrf",
        }
    if keycloak_response is None and keycloak_error is None:
        keycloak_response = {
            "access_token": "kc-access",
            "refresh_token": _offline_jwt_expiring_in(timedelta(hours=1)),
        }
    with (
        patch(
            "core.sources.resana.token_manager.refresh_offline_token",
            return_value=keycloak_response,
            side_effect=keycloak_error,
        ) as mock_keycloak,
        patch(
            "core.sources.resana.token_manager.create_bridge_session",
            **(
                {"side_effect": bridge}
                if isinstance(bridge, Exception)
                else {"return_value": bridge}
            ),
        ),
    ):
        token = ResanaTokenManager(user).get_valid_token()
    return token, mock_keycloak


def _keycloak_refusal():
    error = requests.HTTPError("400 Bad Request")
    error.response = MagicMock(status_code=400)
    return error


def test_get_valid_token_refreshes_ahead_of_the_offline_token_idle_timeout(settings):
    """An offline token close to its idle deadline is refreshed even though the
    access token is still valid, so it never dies of inactivity during a run."""
    user = _make_user_with_idle_offline_token(settings, timedelta(minutes=5))
    new_offline = _offline_jwt_expiring_in(timedelta(hours=1))
    new_access = _make_access_jwt()

    token, mock_keycloak = _get_valid_token_with(
        user,
        keycloak_response={"access_token": "kc-access", "refresh_token": new_offline},
        bridge={
            "plateformeSessionId": "new-sess",
            "interstis_access": new_access,
            "csrfToken": "new-csrf",
        },
    )

    mock_keycloak.assert_called_once()
    assert token == new_access
    user.refresh_from_db()
    assert decrypt_token(user.resana_refresh_token) == new_offline


def test_get_valid_token_does_not_refresh_while_the_offline_token_has_time_left(
    settings,
):
    """No refresh while both tokens are far from their deadline."""
    user = _make_user_with_idle_offline_token(settings, timedelta(minutes=45))

    token, mock_keycloak = _get_valid_token_with(user)

    mock_keycloak.assert_not_called()
    assert token == "current-access"


def test_get_valid_token_refreshes_an_offline_token_20_minutes_from_its_deadline(
    settings,
):
    """The margin is wide enough for a single long download not to step over
    the whole refresh window."""
    user = _make_user_with_idle_offline_token(settings, timedelta(minutes=20))

    _, mock_keycloak = _get_valid_token_with(user)

    mock_keycloak.assert_called_once()


@pytest.mark.parametrize(
    "offline_token",
    [_make_offline_jwt({"typ": "Offline"}), "not-a-jwt"],
    ids=["without-exp", "not-a-jwt"],
)
def test_get_valid_token_does_not_refresh_an_offline_token_without_readable_expiry(
    settings, offline_token
):
    """Without a readable deadline, only the access token expiry triggers a refresh."""
    user = _make_user_with_idle_offline_token(settings, timedelta(minutes=5))
    user.resana_refresh_token = encrypt_token(offline_token)
    user.save()

    token, mock_keycloak = _get_valid_token_with(user)

    mock_keycloak.assert_not_called()
    assert token == "current-access"


def _offline_jwt_issued(issued_ago, expires_in):
    """Build a fake offline token with its issue time and idle deadline."""
    now = timezone.now()
    return _make_offline_jwt(
        {
            "typ": "Offline",
            "iat": int((now - issued_ago).timestamp()),
            "exp": int((now + expires_in).timestamp()),
        }
    )


@pytest.mark.parametrize(
    "issued_ago,expires_in,refreshed",
    [
        (timedelta(0), timedelta(minutes=25), False),
        (timedelta(minutes=20), timedelta(minutes=5), True),
        (timedelta(minutes=-10), timedelta(minutes=-5), True),
    ],
    ids=["first-half", "second-half", "issued-after-exp"],
)
def test_get_valid_token_refreshes_a_short_lived_offline_token_at_half_life(
    settings, issued_ago, expires_in, refreshed
):
    """Near the offline session maximum lifespan, Keycloak issues tokens living
    less than the margin: they are refreshed at half life, not on every call."""
    user = _make_user_with_idle_offline_token(settings, timedelta(minutes=45))
    user.resana_refresh_token = encrypt_token(
        _offline_jwt_issued(issued_ago, expires_in)
    )
    user.save()

    _, mock_keycloak = _get_valid_token_with(user)

    assert mock_keycloak.called is refreshed


def test_refused_refresh_ahead_keeps_the_access_token(settings):
    """A refused refresh ahead must not end the run: the still valid access
    token and the bridge session stay stored, only the offline token goes, so
    the user is asked to log in again."""
    user = _make_user_with_idle_offline_token(settings, timedelta(minutes=5))

    token, _ = _get_valid_token_with(user, keycloak_error=_keycloak_refusal())

    assert token == "current-access"
    user.refresh_from_db()
    assert decrypt_token(user.resana_access_token) == "current-access"
    assert decrypt_token(user.resana_session_id) == "old-sess"
    assert user.resana_refresh_token == ""
    assert not ResanaTokenManager(user).is_connected()


def test_refused_refresh_ahead_is_not_retried(settings):
    """Once Keycloak refused the offline token, the next calls don't submit it again."""
    user = _make_user_with_idle_offline_token(settings, timedelta(minutes=5))

    _get_valid_token_with(user, keycloak_error=_keycloak_refusal())
    token, mock_keycloak = _get_valid_token_with(
        user, keycloak_error=_keycloak_refusal()
    )

    mock_keycloak.assert_not_called()
    assert token == "current-access"


def test_expired_access_token_after_a_refused_refresh_ahead_raises(settings):
    """When the kept access token finally expires, the user must log in again."""
    user = _make_user_with_idle_offline_token(settings, timedelta(minutes=5))
    _get_valid_token_with(user, keycloak_error=_keycloak_refusal())
    user.resana_token_expires_at = timezone.now() - timedelta(seconds=1)
    user.save()

    with pytest.raises(ResanaTokenExpired):
        ResanaTokenManager(user).get_valid_token()


def test_refresh_ahead_network_error_keeps_the_tokens(settings):
    """A Keycloak outage during a refresh ahead keeps every token, to retry later."""
    user = _make_user_with_idle_offline_token(settings, timedelta(minutes=5))
    offline_before = decrypt_token(user.resana_refresh_token)

    token, _ = _get_valid_token_with(
        user, keycloak_error=requests.ConnectionError("down")
    )

    assert token == "current-access"
    user.refresh_from_db()
    assert decrypt_token(user.resana_refresh_token) == offline_before


def test_bridge_rejecting_a_refresh_ahead_keeps_the_access_token(settings):
    """A bridge rejection during a refresh ahead doesn't end the run."""
    user = _make_user_with_idle_offline_token(settings, timedelta(minutes=5))

    token, _ = _get_valid_token_with(
        user, bridge=requests.HTTPError("401 Unauthorized")
    )

    assert token == "current-access"
    user.refresh_from_db()
    assert decrypt_token(user.resana_access_token) == "current-access"


def test_bridge_server_error_during_a_refresh_ahead_keeps_the_offline_token(
    settings,
):
    """A passing bridge 5xx is not a rejection: the offline token stays, to
    retry on the next call."""
    user = _make_user_with_idle_offline_token(settings, timedelta(minutes=5))
    offline_before = decrypt_token(user.resana_refresh_token)
    server_error = requests.HTTPError("503 Service Unavailable")
    server_error.response = MagicMock(status_code=503)

    token, _ = _get_valid_token_with(user, bridge=server_error)

    assert token == "current-access"
    user.refresh_from_db()
    assert decrypt_token(user.resana_refresh_token) == offline_before


@freeze_time("2026-10-15 15:18:08")
def test_failed_refresh_ahead_logs_that_the_access_token_is_kept(settings):
    """The warning tells until when the run can go on without a refresh."""
    user = _make_user_with_idle_offline_token(settings, timedelta(minutes=5))

    with patch("core.sources.resana.token_manager.logger") as mock_logger:
        _get_valid_token_with(user, keycloak_error=_keycloak_refusal())

    assert _rendered(mock_logger.warning)[-1] == (
        f"Resana token refresh ahead failed for user {user.id}: keeping the "
        "access token, which expires at 2026-10-15T17:18:08+00:00 (in 7200s)"
    )


def test_concurrent_refresh_ahead_is_done_once(settings):
    """A worker holding a stale copy of the user doesn't refresh again a token
    another worker just refreshed ahead."""
    user_a = _make_user_with_idle_offline_token(settings, timedelta(minutes=5))
    user_b = type(user_a).objects.get(pk=user_a.pk)

    _, first_keycloak = _get_valid_token_with(user_a)
    _, second_keycloak = _get_valid_token_with(user_b)

    first_keycloak.assert_called_once()
    second_keycloak.assert_not_called()


@freeze_time("2026-10-15 15:18:08")
def test_refresh_logs_the_offline_token_deadlines(settings):
    """The refresh logs give the offline token deadline before and after."""
    user = _make_user_with_idle_offline_token(settings, timedelta(minutes=5))

    with patch("core.sources.resana.token_manager.logger") as mock_logger:
        _get_valid_token_with(user)

    assert _rendered(mock_logger.info) == [
        f"Resana token refresh for user {user.id}: access token expires at "
        "2026-10-15T17:18:08+00:00 (in 7200s), offline token expires at "
        "2026-10-15T15:23:08+00:00 (in 300s), refreshing with the offline token",
        f"Resana token refresh succeeded for user {user.id}: new access token "
        "expires at 2026-10-15T18:18:08+00:00 (in 10800s), offline token "
        "rotated, expires at 2026-10-15T16:18:08+00:00 (in 3600s)",
    ]


# ---------------------------------------------------------------------------
# ensure_offline_token() — before a task locks anything
# ---------------------------------------------------------------------------


def _ensure_offline_token_with(user, keycloak_error=None):
    """Call ensure_offline_token() against mocked Keycloak and bridge, and
    return the Keycloak mock."""
    with (
        patch(
            "core.sources.resana.token_manager.refresh_offline_token",
            return_value={
                "access_token": "kc-access",
                "refresh_token": _offline_jwt_expiring_in(timedelta(hours=1)),
            },
            side_effect=keycloak_error,
        ) as mock_keycloak,
        patch(
            "core.sources.resana.token_manager.create_bridge_session",
            return_value={
                "plateformeSessionId": "new-sess",
                "interstis_access": _make_access_jwt(),
                "csrfToken": "new-csrf",
            },
        ),
    ):
        ResanaTokenManager(user).ensure_offline_token()
    return mock_keycloak


def test_ensure_offline_token_raises_when_the_offline_token_is_refused(settings):
    """A task that waited past the idle timeout fails before locking anything,
    even though the access token would still work for a while. That access
    token stays stored, for another run of the user to clean up with."""
    user = _make_user_with_idle_offline_token(settings, timedelta(minutes=-30))

    with pytest.raises(ResanaTokenExpired):
        _ensure_offline_token_with(user, keycloak_error=_keycloak_refusal())

    user.refresh_from_db()
    assert not ResanaTokenManager(user).is_connected()
    assert decrypt_token(user.resana_access_token) == "current-access"


def test_ensure_offline_token_refreshes_an_offline_token_close_to_its_deadline(
    settings,
):
    """An offline token close to its idle deadline is renewed right away."""
    user = _make_user_with_idle_offline_token(settings, timedelta(minutes=5))

    mock_keycloak = _ensure_offline_token_with(user)

    mock_keycloak.assert_called_once()
    user.refresh_from_db()
    assert ResanaTokenManager(user).is_connected()


def test_ensure_offline_token_does_nothing_while_the_offline_token_has_time_left(
    settings,
):
    """No refresh when the offline token is far from its idle deadline."""
    user = _make_user_with_idle_offline_token(settings, timedelta(minutes=45))

    mock_keycloak = _ensure_offline_token_with(user)

    mock_keycloak.assert_not_called()


def test_ensure_offline_token_raises_without_offline_token(settings):
    """Without an offline token, the access token can't outlast its lifetime:
    the user must log in again before the task starts."""
    user = _make_user_with_idle_offline_token(settings, timedelta(minutes=30))
    user.resana_refresh_token = ""
    user.save()

    with pytest.raises(ResanaTokenExpired):
        _ensure_offline_token_with(user)

    user.refresh_from_db()
    assert not ResanaTokenManager(user).is_connected()
    assert decrypt_token(user.resana_access_token) == "current-access"


def test_ensure_offline_token_does_not_refresh_again_after_another_worker(settings):
    """A stale copy of the user doesn't refresh again a token another worker
    just renewed."""
    user_a = _make_user_with_idle_offline_token(settings, timedelta(minutes=5))
    user_b = type(user_a).objects.get(pk=user_a.pk)

    first_keycloak = _ensure_offline_token_with(user_a)
    second_keycloak = _ensure_offline_token_with(user_b)

    first_keycloak.assert_called_once()
    second_keycloak.assert_not_called()


# ---------------------------------------------------------------------------
# Last minute of the access token — cleanup after a failed run
# ---------------------------------------------------------------------------


def _make_user_in_the_last_minute(settings):
    """Build a user whose access token still works for 30 s, within the refresh
    buffer, with an offline token far from its idle deadline."""
    user = _make_user_with_idle_offline_token(settings, timedelta(minutes=45))
    user.resana_token_expires_at = timezone.now() + timedelta(seconds=30)
    user.save()
    return user


def test_refused_refresh_in_the_last_minute_keeps_the_access_token(settings):
    """The run stops, but the access token that still works stays stored, so
    the cleanup that follows can still unlock the workspace."""
    user = _make_user_in_the_last_minute(settings)

    with pytest.raises(ResanaTokenExpired):
        _get_valid_token_with(user, keycloak_error=_keycloak_refusal())

    user.refresh_from_db()
    assert decrypt_token(user.resana_access_token) == "current-access"
    assert user.resana_refresh_token == ""


def test_missing_offline_token_in_the_last_minute_keeps_the_access_token(settings):
    """Without an offline token the run stops, keeping the working access token."""
    user = _make_user_in_the_last_minute(settings)
    user.resana_refresh_token = ""
    user.save()

    with pytest.raises(ResanaTokenExpired):
        ResanaTokenManager(user).get_valid_token()

    user.refresh_from_db()
    assert decrypt_token(user.resana_access_token) == "current-access"


def test_get_token_for_cleanup_uses_the_last_minute_of_the_access_token(settings):
    """After the refresh was refused, the cleanup still gets the access token
    while it works."""
    user = _make_user_in_the_last_minute(settings)
    with pytest.raises(ResanaTokenExpired):
        _get_valid_token_with(user, keycloak_error=_keycloak_refusal())

    assert ResanaTokenManager(user).get_token_for_cleanup() == "current-access"


def test_get_token_for_cleanup_refreshes_when_it_can(settings):
    """The cleanup prefers a refreshed token, which outlasts the last minute."""
    user = _make_user_in_the_last_minute(settings)
    new_access = _make_access_jwt()

    with (
        patch(
            "core.sources.resana.token_manager.refresh_offline_token",
            return_value={
                "access_token": "kc-access",
                "refresh_token": _offline_jwt_expiring_in(timedelta(hours=1)),
            },
        ),
        patch(
            "core.sources.resana.token_manager.create_bridge_session",
            return_value={
                "plateformeSessionId": "new-sess",
                "interstis_access": new_access,
                "csrfToken": "new-csrf",
            },
        ),
    ):
        token = ResanaTokenManager(user).get_token_for_cleanup()

    assert token == new_access


def test_get_token_for_cleanup_raises_once_the_access_token_expired(settings):
    """An access token past its expiry is of no use: the tokens are cleared."""
    user = _make_user_in_the_last_minute(settings)
    user.resana_refresh_token = ""
    user.resana_token_expires_at = timezone.now() - timedelta(seconds=1)
    user.save()

    with pytest.raises(ResanaTokenExpired):
        ResanaTokenManager(user).get_token_for_cleanup()

    user.refresh_from_db()
    assert not ResanaTokenManager(user).is_connected()


def test_get_token_for_cleanup_uses_the_last_minute_when_keycloak_is_down(settings):
    """A Keycloak outage during the cleanup doesn't prevent the unlock while the
    access token still works."""
    user = _make_user_in_the_last_minute(settings)

    with patch(
        "core.sources.resana.token_manager.refresh_offline_token",
        side_effect=requests.ConnectionError("down"),
    ):
        token = ResanaTokenManager(user).get_token_for_cleanup()

    assert token == "current-access"


def _make_user_with_five_minutes_left(settings, offline_token=True):
    user = _make_user_with_idle_offline_token(settings, timedelta(minutes=45))
    user.resana_token_expires_at = timezone.now() + timedelta(minutes=5)
    if not offline_token:
        user.resana_refresh_token = ""
    user.save()
    return user


def test_run_stops_early_without_offline_token(settings):
    """Without an offline token nothing can renew the access token: the run
    stops 10 minutes before it expires, leaving the cleanup time to unlock."""
    user = _make_user_with_five_minutes_left(settings, offline_token=False)

    with pytest.raises(ResanaTokenExpired):
        ResanaTokenManager(user).get_valid_token()

    assert ResanaTokenManager(user).get_token_for_cleanup() == "current-access"


def test_access_token_with_minutes_left_is_used_while_it_can_be_renewed(settings):
    """With an offline token, the access token is used until its last minute."""
    user = _make_user_with_five_minutes_left(settings)

    token, mock_keycloak = _get_valid_token_with(user)

    assert token == "current-access"
    mock_keycloak.assert_not_called()


def test_ensure_offline_token_goes_on_after_a_keycloak_blip(settings):
    """A network failure doesn't fail the task start while the offline token
    is still alive: the next calls refresh it ahead."""
    user = _make_user_with_idle_offline_token(settings, timedelta(minutes=5))
    offline_before = decrypt_token(user.resana_refresh_token)

    _ensure_offline_token_with(user, keycloak_error=requests.ConnectionError("down"))

    user.refresh_from_db()
    assert decrypt_token(user.resana_refresh_token) == offline_before


def test_ensure_offline_token_raises_on_a_keycloak_blip_after_the_deadline(settings):
    """Once past its idle deadline the offline token is dead anyway: the task
    start fails instead of locking the workspace."""
    user = _make_user_with_idle_offline_token(settings, timedelta(minutes=-5))

    with pytest.raises(requests.ConnectionError):
        _ensure_offline_token_with(
            user, keycloak_error=requests.ConnectionError("down")
        )
