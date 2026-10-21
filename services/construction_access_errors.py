class ConstructionAccessGrantConflictError(Exception):
    """The requested operation no longer applies to the original effective grant."""

    status_code = 409
    code = "ACCESS_GRANT_CONFLICT"

    def __init__(self):
        super().__init__("Construction access grant effective state has changed.")
