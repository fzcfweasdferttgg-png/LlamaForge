# LlamaForge round-robin routing strategy for LiteLLM.
"""Round-robin routing strategy for LiteLLM (LlamaForge).

LiteLLM 1.104.2 has no round-robin: `simple-shuffle` picks a deployment at
random per request, so two simultaneous requests can collide on one model
while the other idles. This strategy rotates deterministically over the
eligible deployments instead: request 1 -> A, request 2 -> B, request 3 -> A.

The eligible set comes from the Router's own `async_get_healthy_deployments`,
so cooldowns, `max_parallel_requests` caps and `order` tiers keep working
exactly as with the stock strategies: a capped primary drops out of the set
and the next turn lands on the reserve. Wired through the official extension
API (`Router.set_custom_routing_strategy`, the same hook the lar1 strategy
uses); installed into the venv's litellm by gateway.ensure_round_robin().
"""
import threading

try:
    from litellm.types.router import CustomRoutingStrategyBase as _Base
except Exception:                       # outside the venv (unit tests)
    _Base = object


def _stable_key(deployment):
    """A stable identity for ordering: without it the rotation would reshuffle
    whenever the upstream list order moves, and two turns could hit one model."""
    params = deployment.get("litellm_params") or {}
    info = deployment.get("model_info") or {}
    return str(params.get("model") or info.get("id") or "")


class RoundRobinRoutingStrategy(_Base):
    def __init__(self, router_instance=None):
        self._router = router_instance
        self._next = {}
        self._lock = threading.Lock()

    def _turn(self, key, count):
        with self._lock:
            i = self._next.get(key, 0)
            self._next[key] = (i + 1) % count
        return i % count

    async def async_get_available_deployment(
        self,
        model: str,
        messages: list | None = None,
        input=None,
        specific_deployment: bool | None = False,
        request_kwargs: dict | None = None,
    ):
        if self._router is None:
            return None
        healthy = await self._router.async_get_healthy_deployments(
            model=model,
            request_kwargs=request_kwargs or {},
            messages=messages,
            input=input,
            specific_deployment=specific_deployment,
        )
        if isinstance(healthy, dict) or not healthy:
            return healthy or None
        ordered = sorted(healthy, key=_stable_key)
        return ordered[self._turn(model or "", len(ordered))]

    def get_available_deployment(self, *args, **kwargs):
        raise NotImplementedError(
            "round-robin routing supports the async path only "
            "(the gateway proxy is async); use acompletion"
        )


def apply_round_robin_strategy(router, routing_strategy_args=None):
    """Attach the strategy to a Router - the lar1 pattern. The args are
    accepted for signature parity and intentionally unused: rotation needs
    no tuning knobs."""
    router.routing_strategy = "round-robin"
    router.set_custom_routing_strategy(RoundRobinRoutingStrategy(router_instance=router))
