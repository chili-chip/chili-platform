#!/usr/bin/env python3
"""Record the D1 id and WRANGLER_COMMAND for the Worker boot guard.

Wrangler sets WRANGLER_COMMAND while it runs the build: ``dev`` for local
development, ``deploy`` or ``versions upload`` for a bundle that can be
published. The generated module lives under ``src/`` so the Worker bundle
includes it. ``wrangler.jsonc`` itself stays outside that bundle.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from app.worker_guard import write_worker_boot_embed  # noqa: E402


def main() -> None:
    destination = write_worker_boot_embed(ROOT)
    print(f"Wrote {destination.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
