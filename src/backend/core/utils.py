import os

from django.utils import timezone

from core.models import FeatureFlag


def get_dir_size(path="."):
    total = 0
    with os.scandir(path) as it:
        for entry in it:
            if entry.is_file():
                total += entry.stat().st_size
            elif entry.is_dir():
                total += get_dir_size(entry.path)
    return total


def sizeof_fmt(num, suffix="B"):
    for unit in ("", "Ki", "Mi", "Gi", "Ti", "Pi", "Ei", "Zi"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"


def describe_expiry(expires_at) -> str:
    """Describe a token expiry date relative to now, for logs."""
    if expires_at is None:
        return "no expiry"
    seconds = int((expires_at - timezone.now()).total_seconds())
    if seconds >= 0:
        return f"expires at {expires_at.isoformat()} (in {seconds}s)"
    return f"expired since {expires_at.isoformat()} ({-seconds}s ago)"


def describe_access_expiry(expires_at) -> str:
    """Describe an access token expiry for logs: without a date, the backends
    refresh the token rather than trusting it."""
    if expires_at is None:
        return "expiry unknown (will be refreshed)"
    return describe_expiry(expires_at)


def describe_oauth_error(error) -> str:
    """Return the status and OAuth error of a failed token endpoint call, e.g.
    '400 invalid_grant (Offline session not active)'. Never carries a token."""
    response = error.response
    if response is None:
        return f"{type(error).__name__} without response"
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if not isinstance(payload, dict) or not payload.get("error"):
        return f"{response.status_code} without OAuth error"
    oauth_error = _one_line(payload["error"], 64)
    description = _one_line(payload.get("error_description") or "", 200)
    return f"{response.status_code} {oauth_error}" + (
        f" ({description})" if description else ""
    )


def _one_line(value, limit: int) -> str:
    """Keep a value sent by a remote server on one bounded line in the logs."""
    return " ".join(str(value).split())[:limit]


def describe_request_error(error) -> str:
    """Describe a request error by its type and HTTP status for logs: its
    message could carry request data."""
    response = getattr(error, "response", None)
    if response is None:
        return type(error).__name__
    return f"{type(error).__name__} {response.status_code}"


def is_feature(name: str) -> bool:
    flag = FeatureFlag.objects.filter(name=name).first()
    if flag is None:
        return True
    return flag.is_active


def ensure_file_uniqueness(file_path, exists=os.path.exists):
    """
    Make sure the file_path is non existent, if so it appends a counter ( "/path/to/my_file (1)" ) at the end.
    """
    base, extension = os.path.splitext(file_path)
    count = 0
    uniq_path = file_path
    while exists(uniq_path):
        count += 1
        uniq_path = base + " (" + str(count) + ")" + extension
    return uniq_path


def truncate_file_name(filename, max_length=200):
    """
    Reduce the size of a filename in path keeping its extension.
    """
    if len(filename) <= max_length:
        return filename
    base, extension = os.path.splitext(filename)
    new_length = max_length - len(extension) - 1 if extension else max_length
    new_base = base[:new_length]
    return new_base + extension


def sanitize_path_component(name):
    """
    Replace path separators in a single path segment (folder or file name)
    so a source name containing "/" can't be split into unintended sub-paths.
    """
    return name.replace("/", "-").replace(os.sep, "-")


def truncate_path_parts(path, max_folder_length=200, max_files_length=190):
    """
    Reduce each parts of path if needed.
    """
    head, filename = os.path.split(path)
    parts = head.split(os.sep)
    output = []
    for part in parts:
        output.append(part[:max_folder_length])
    output.append(truncate_file_name(filename, max_files_length))
    output_path = os.path.join(*output)
    if path.startswith(os.sep):
        output_path = os.sep + output_path
    return output_path
