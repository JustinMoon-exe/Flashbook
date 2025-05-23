# market_simulator/api_client.py

import httpx
import logging
from decimal import Decimal
from typing import Dict, Optional
from .config import API_BASE_URL

log = logging.getLogger(__name__)

async def submit_order_to_api(client: httpx.AsyncClient, order_data: Dict) -> Optional[Dict]:
    """
    Submits an order to the FastAPI /orders endpoint.
    Returns response JSON dictionary on success (status 2xx), None on failure.
    """
    order_url = f"{API_BASE_URL}/orders"
    try:
        if isinstance(order_data.get("price"), Decimal):
            order_data["price"] = str(order_data["price"])

        response = await client.post(order_url, json=order_data, timeout=10.0)

        response.raise_for_status() 
        log.debug(f"API Response ({response.status_code}) for {order_data.get('symbol','N/A')} order")
        return response.json()

    except httpx.RequestError as exc:
        log.error(f"HTTP Request Error submitting order ({order_data.get('symbol', '?')}): {exc}")
        return None
    except httpx.HTTPStatusError as exc:
        error_detail = "Unknown API Error"
        try:
            
            error_detail = exc.response.json().get("detail", error_detail)
        except Exception:
            pass 
        log.error(f"HTTP Status Error submitting order ({order_data.get('symbol', '?')}): {exc.response.status_code} - {error_detail}")
        return None
    except json.JSONDecodeError as exc:
         log.error(f"Failed to decode JSON response from API order submission: {exc}. Response text: {response.text[:200]}")
         return None 
    except Exception as e:
        log.error(f"Generic error submitting order ({order_data.get('symbol', '?')}): {e}", exc_info=True)
        return None