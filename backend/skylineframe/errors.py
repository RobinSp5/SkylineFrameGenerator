"""Error hierarchy: every user-facing failure of the pipeline derives from SkylineError."""


class SkylineError(RuntimeError):
    """Base class for errors whose message is safe to show to the user."""


class FetchError(SkylineError):
    """Overpass could not be reached or answered with an error."""


class MeshError(SkylineError):
    """The geometry could not be turned into a valid solid."""


class ExportError(SkylineError):
    """The final mesh failed verification or could not be written."""


class AreaError(SkylineError):
    """The selected area cannot be processed (e.g. antimeridian)."""


class PipelineError(SkylineError):
    """The selected area cannot produce a model (e.g. no buildings)."""
