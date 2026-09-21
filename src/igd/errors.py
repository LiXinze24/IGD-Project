"""Errors with stable, public messages; remote response bodies are never echoed."""


class ValidationError(ValueError):
    """Invalid configuration, task graph, or task result."""


class BackendError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

