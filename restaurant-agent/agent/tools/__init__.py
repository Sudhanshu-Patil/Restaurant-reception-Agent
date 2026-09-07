"""Tool layer — typed, self-describing functions the LLM can call.

``catalog.py`` holds every tool; ``registry.py`` handles description, dispatch and
defensive validation. Adding a capability is a one-file change in ``catalog.py``.
"""
