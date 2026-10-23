"""IP Office user profile values used by the Management API."""

USER_PACKAGE_NAMES = {
    1: "Basic User",
    2: "Teleworker User",
    3: "Mobile User",
    4: "Power User",
    5: "Office Worker User",
    6: "Branch User",
    7: "Centralized User",
    8: "Non-Licensed User",
}


def user_package_code(value: object) -> int | None:
    """Normalize AssignedPackage from the XML/JSON API to its documented code."""
    if value is None:
        return None
    try:
        code = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return code if code in USER_PACKAGE_NAMES else None
