"""Exception types shared across the package."""


class ConfigError(ValueError):
    """The agent config (or required environment) is missing or invalid."""


class StateCorruptError(RuntimeError):
    """The state file exists but could not be parsed. It has been quarantined."""


class FeedsUnreachable(RuntimeError):
    """Every configured feed failed to load (network outage, blocking, or wrong URLs)."""
