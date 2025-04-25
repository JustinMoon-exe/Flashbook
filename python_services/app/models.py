from pydantic import BaseModel, Field, model_validator, ConfigDict
from typing import Literal, Optional
import uuid
from datetime import datetime, timezone
from decimal import Decimal

class Order(BaseModel):
    model_config = ConfigDict(
        json_encoders={Decimal: str},
        populate_by_name=True,
        from_attributes=True,
    )

    order_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    symbol: str
    side: Literal['buy', 'sell']
    price: Decimal = Field(gt=Decimal(0), description="Order price (must be positive)")
    quantity: int = Field(gt=0, description="Order quantity (must be positive)")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp of order creation (UTC)"
    )
    status: Literal['new', 'accepted', 'rejected', 'filled', 'partially_filled', 'cancelled'] = Field(
        default='new',
        description="Current status of the order"
    )
    remaining_quantity: int = Field(
        default=0,
        ge=0,
        description="Quantity remaining to be filled"
    )

    @model_validator(mode='after')
    def set_remaining_quantity(self) -> 'Order':
        if self.remaining_quantity == 0 and self.quantity > 0:
            self.remaining_quantity = self.quantity
        elif self.remaining_quantity > self.quantity:
            raise ValueError("remaining_quantity cannot be greater than quantity")
        return self

class MarketData(BaseModel):
    model_config = ConfigDict(
        json_encoders={Decimal: str},
        populate_by_name=True,
        from_attributes=True,
    )

    symbol: str
    bid: Optional[Decimal] = Field(default=None, description="Highest bid price")
    ask: Optional[Decimal] = Field(default=None, description="Lowest ask price")
    last: Optional[Decimal] = Field(default=None, description="Price of the last trade")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp of last market data update (UTC)"
    )
