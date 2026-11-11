class InsufficientStockError(Exception):
    """
    Raised when an operational transaction (sales dispatch, manufacturing order,
    or internal allocation) requests more quantity than physically available on hand.
    """
    def __init__(self, message: str, item_name: str = "", required_qty=None, available_qty=None):
        super().__init__(message)
        self.message = message
        self.item_name = item_name
        self.required_qty = required_qty
        self.available_qty = available_qty

    def __str__(self) -> str:
        return self.message


class BOMNotFoundError(Exception):
    """
    Raised when an active Bill of Materials cannot be resolved for a finished good
    during a manufacturing order execution.
    """
    pass
