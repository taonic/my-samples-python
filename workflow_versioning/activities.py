from dataclasses import dataclass
import asyncio

from temporalio import activity


@dataclass
class OrderInput:
    """Single dataclass argument, so fields can be added compatibly later."""
    order_id: str
    amount_cents: int


@activity.defn
async def charge_order(order: OrderInput) -> str:
    """Pretend to charge a card and return a confirmation number."""
    await asyncio.sleep(3)  # Simulate a delay for charging the card
    activity.logger.info(
        "Charging order %s for %d cents", order.order_id, order.amount_cents
    )
    return f"charge-{order.order_id}"
