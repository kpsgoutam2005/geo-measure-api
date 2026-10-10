"""Domain errors. Their messages are safe to show to API clients."""


class GeoFileError(Exception):
    """The uploaded file is invalid or cannot be processed."""


class UnsupportedFileType(GeoFileError):
    pass


class FileTooLarge(GeoFileError):
    pass
