"""Multi-model backend: a capability registry, a router, and an LLM facade that routes per call.

- ``registry``  models declare what they are good at (``registry.yaml`` + a local override file);
                a new model is one YAML entry, nothing else in the system changes
- ``router``    task kind -> best installed model that fits the hardware; every decision is logged
                to a hash-chained routing log so "which model handled which sub-task" is answerable
- ``routed``    ``RoutedLLM``: the ``BaseLLM`` the agents already use, choosing the model per call
                from the call's ``purpose``
"""
from workbench.models.registry import ModelProfile, ModelRegistry, load_registry
from workbench.models.router import ModelRouter, RoutingDecision, RoutingPlan, SubTask, kind_of_purpose

__all__ = ["ModelProfile", "ModelRegistry", "load_registry", "ModelRouter", "RoutingDecision",
           "RoutingPlan", "SubTask", "kind_of_purpose"]
