"""Shared tool errors and strict object schemas."""
class ToolError(RuntimeError):
    def __init__(self, message, code='tool_error'):
        super().__init__(message)
        self.code = code


def schema(properties, required=()):
    return dict(type='object', properties=properties, required=list(required), additionalProperties=False)


