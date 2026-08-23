"""
Where data comes from: the exchange, and the object store it lands in.

Everything here talks to something outside the process and depends on nothing
but `config`, which is what keeps the layers above it testable in isolation.
"""
