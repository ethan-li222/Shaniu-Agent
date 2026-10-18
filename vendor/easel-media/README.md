# Easel media utilities

Copied without changes from the local Easel snapshot. See SOURCE.json for source
paths and SHA256 receipts, and LICENSE for Apache-2.0 terms. No other Easel
dependencies, assets, Skills, publication tools, or video generation providers
are bundled.

Shaniu's scripts/media_post_worker.py imports selected cut, frame, compression,
reframing and subtitle functions. Runtime adapters locate imageio_ffmpeg, probe
with PyAV, impose process limits, and use owned task directories. The original
standalone CLI commands are not exposed to the Agent.
