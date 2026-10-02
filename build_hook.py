"""Bake `YYYY.MM.DD-<short sha>` into the wheel so `--version` works without git."""

import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        r = subprocess.run(
            ["git", "log", "-1", "--format=%cd-%h", "--date=format:%Y.%m.%d"],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode == 0 and r.stdout.strip():
            info = Path(self.root) / "wiki_weaver" / "_build_info.txt"
            info.write_text(r.stdout.strip() + "\n")
            build_data.setdefault("force_include", {})[str(info)] = "wiki_weaver/_build_info.txt"
