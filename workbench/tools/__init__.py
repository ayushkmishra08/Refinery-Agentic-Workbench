"""Tools = the only functions agents may call on the outside world.
Each tool is a small, typed wrapper over a service, registered in ``registry.py``
so the Planner can list them and the audit store can log every invocation.
"""
