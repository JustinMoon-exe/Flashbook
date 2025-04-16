# python_services/app/models.py
from pydantic import BaseModel, Field
from typing import Literal, Optional
import uuid
# Import datetime and timezone
from datetime import datetime, timezone # <--- CHANGE HERE

class Order(BaseModel):
    order_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    symbol: str
    side: Literal['buy', 'sell']
    price: float = Field(gt=0)
    quantity: int = Field(gt=0)
    # Use timezone-aware UTC time
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc)) # <--- CHANGE HERE
    status: Literal['new', 'filled', 'partially_filled', 'cancelled'] = 'new'

class MarketData(BaseModel):
    symbol: str
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    # Use timezone-aware UTC time
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc)) # <--- CHANGE HERE