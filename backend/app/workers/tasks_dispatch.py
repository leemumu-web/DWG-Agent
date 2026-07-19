"""Reserved dispatch worker boundary.

No task is registered here until transactional outbox delivery is implemented.
The module exists so the named queue/process can be started and observed safely.
"""
