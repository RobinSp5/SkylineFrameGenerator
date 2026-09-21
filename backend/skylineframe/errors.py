"""Error hierarchy: every user-facing failure of the pipeline derives from SkylineError."""


class SkylineError(RuntimeError):
    """Base class for errors whose message is safe to show to the user."""


class FetchError(SkylineError):
    """Overpass could not be reached or answered with an error."""
