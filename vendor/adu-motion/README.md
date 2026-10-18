# Adu source integration

Copied from the user's local `adu-motion-video` 3.14.0-rc.1 checkout on 2026-10-12, whose upstream is recorded in SOURCE.json. MIT copyright and permission notice are retained in LICENSE. THIRD_PARTY.md preserves the source project's distribution/provenance notes; it does not imply its demonstration media is included here.

Actually used by Shaniu:

- `motion-primitives.js`: the unchanged helpers and EZ easing prefix extracted from `template/lib.js`. `Shaniu/motion/motion.js` uses these functions for title/point entrances and exits.
- `subtitles.js`: copied unchanged from `template/subtitles.js`. It renders literal SRT text against the output clock, with explicit responsive position supplied by Shaniu.
- `audiolib.py`: copied unchanged from `scripts/audiolib.py`. The worker uses deterministic `render_sfx`/`render_event` card effects and WAV output. Legacy synthesized background music routines are not called.

SOURCE.json contains copied-file SHA-256 receipts and the extraction description. Runtime reads only these vendored files, not the original checkout.

Shaniu's responsive scene wrapper, asynchronous task orchestration, Chrome CDP renderer and ownership-checked delivery are new integration code. The audio filter graph in `motion/render.py` adapts the voice compression, music ducking, limiter and loudness recipe in Adu `scripts/pipeline.py:mix`; the source license remains included in every exported editable project.

This integration does not include the full macro-pack catalog, author demo media, actor footage, font binaries, source songs, scene-rematch planner, or automatic semantic selection engine. Its two responsive looks are Shaniu compositions using Adu primitives; they are not the upstream catalog's named full-template styles.
