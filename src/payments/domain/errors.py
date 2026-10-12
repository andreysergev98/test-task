class PaymentNotFound(Exception):
    pass


class IdempotencyConflict(Exception):
    pass


class WebhookHostNotAllowed(Exception):
    pass


class GatewayError(Exception):
    pass


class WebhookError(Exception):
    pass
