"""Slow motion for hero moments: 2x frame interpolation, with a confidence fallback (P1).

- ``limits``: the 2x ceiling, the one place a speed is validated.
- ``weights``: the pinned interpolation weights and the only way they load.
- ``ifnet``: the vendored network (needs torch).
- ``rife``: the interpolator - device choice, memory steps, CPU fallback (needs torch).
- ``confidence``: does the interpolated picture hold up? Pure numpy.
- ``shot``: one slow-motion shot, end to end, with its accept/revert decision.
"""
