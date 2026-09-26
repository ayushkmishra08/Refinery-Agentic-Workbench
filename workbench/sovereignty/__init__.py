"""Sovereignty: proving that nothing leaves the premises.

- ``hashchain``     tamper-evident append-only logs (each entry hashes the one before it)
- ``netmonitor``    live network monitor + immutable connection log for the whole session
- ``egress``        in-process egress guard: sockets to anything but loopback/private ranges are refused
- ``model_updates`` signed, checksum-verified model packages; no live downloads

One trust boundary, one operator: a hash chain gives the tamper-evidence property a ledger is
known for without a distributed consensus that solves a problem this deployment does not have.
"""
