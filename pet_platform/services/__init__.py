"""Business logic. Services never flash/redirect/render; they raise ServiceError instead."""


class ServiceError(Exception):
    """A business-rule failure the route turns into a flash message or a JSON error."""

    def __init__(self, message, category='error', status=400):
        super().__init__(message)
        self.message = message
        self.category = category
        self.status = status
