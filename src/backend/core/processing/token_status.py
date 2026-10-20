"""Log the tokens a migration relies on, to diagnose expired sessions."""

from django.conf import settings

from core.destinations.drive.drive_backend import describe_drive_tokens
from core.sources.resana.token_manager import ResanaTokenManager


def log_token_status(logger, context: str, user, *, resana: bool, drive: bool):
    """Log the state and lifetime of the user's tokens for each service used:
    the Resana source, and Drive when it authenticates with the user's tokens.
    Never raises: a log must not fail the migration it documents."""
    services = []
    if resana:
        services.append(("Resana", lambda: ResanaTokenManager(user).describe_tokens()))
    if (
        drive
        and getattr(settings, "DRIVE_AUTH_MODE", "service_account") == "user_token"
    ):
        services.append(("Drive (ProConnect)", lambda: describe_drive_tokens(user)))
    for service, describe in services:
        try:
            logger.info("%s: %s", context, describe())
        except Exception as error:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            # The error's message could carry token data: only its type is logged.
            logger.warning(
                "%s: unable to describe the %s tokens of user %s (%s)",
                context,
                service,
                user.id,
                type(error).__name__,
            )
