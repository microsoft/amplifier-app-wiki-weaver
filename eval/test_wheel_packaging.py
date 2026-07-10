"""Regression test for the packaging defect that broke every real install.

BACKGROUND: PR #28 wired ``reweave_overview_if_needed()`` into
``wiki_weaver/reweave.py`` (SHIPPED runtime code). That module reached into
``eval/grade_wiki.py`` for ``GradeResult``/``grade_overview`` via
``sys.path.insert(0, ".../eval")`` -- which resolves fine in a source
checkout (``eval/`` sits right there on disk) but is a hard
``ModuleNotFoundError`` for every real ``uv tool install`` user, because
``eval/`` is deliberately excluded from the wheel
(``pyproject.toml``: ``[tool.hatch.build.targets.wheel] packages =
["wiki_weaver"]``). Verified twice in real ``uv tool install`` environments
(see the fix commit for the reproduction).

THE FIX (this commit): ``GradeResult``/``grade_overview`` now live in
``wiki_weaver/grading.py`` (shipped), and ``eval/grade_wiki.py`` re-exports
them for backward compatibility. ``wiki_weaver/reweave.py`` imports directly
from ``wiki_weaver.grading`` -- no more ``eval/`` dependency at all.

THIS TEST builds the ACTUAL wheel from the real packaging config, installs
it into a fresh venv that cannot see this repo's ``eval/`` directory at all
(an isolated temp dir, `--no-deps`, no repo on ``sys.path``/``PYTHONPATH``),
and asserts that ``from wiki_weaver.reweave import
reweave_overview_if_needed`` succeeds. This is the test that would have
caught the regression before merge -- a real wheel build + a real install +
a real import, not a mock of the import machinery.

``unified_llm`` and ``amplifier_module_pipeline_runner`` are each stubbed with
a tiny fake package. Both are SEPARATE, network-installed (git) runtime
dependencies reached at import time -- ``unified_llm`` by
``wiki_weaver.model_resolver``, ``amplifier_module_pipeline_runner`` by
``wiki_weaver.engine_runner`` / ``wiki_weaver.reweave`` (the pipeline-runner
migration) -- a pre-existing condition (now joined by a second one) unrelated
to this fix. Stubbing them isolates this test to the packaging question it
exists to guard, without requiring a live git-dependency install
(``amplifier-unified-llm-client`` / ``amplifier-foundation`` /
``amplifier-module-pipeline-runner``) in CI. This mirrors the "no real LLM
calls, no network access" discipline already used by ``eval/test_reweave.py``
for the same modules.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    shutil.which("uv") is None,
    reason="uv not available to build/install the wheel",
)


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    kwargs.setdefault("timeout", 180)
    return subprocess.run(cmd, **kwargs)


def test_reweave_importable_from_wheel_without_eval_dir(tmp_path: Path) -> None:
    """Build the real wheel, install it where eval/ cannot be seen, import reweave.

    Guards against the exact regression class fixed by this commit: shipped
    code (wiki_weaver/) reaching into unshipped code (eval/) at import time.
    """
    dist_dir = tmp_path / "dist"
    venv_dir = tmp_path / "venv"
    stub_dir = tmp_path / "stubs"
    workdir = tmp_path / "work"
    workdir.mkdir()

    # 1. Build the ACTUAL wheel from the real pyproject.toml/packaging config.
    build = _run(["uv", "build", "--wheel", "-o", str(dist_dir)], cwd=_REPO)
    assert build.returncode == 0, (
        f"wheel build failed:\nstdout={build.stdout}\nstderr={build.stderr}"
    )

    wheels = sorted(dist_dir.glob("wiki_weaver-*.whl"))
    assert wheels, f"no wheel produced in {dist_dir}"
    wheel_path = wheels[-1]

    # Sanity: the wheel must NOT contain eval/ -- this is the packaging
    # boundary this whole test exists to enforce. If this assertion ever
    # fails, the packaging config changed and this test's premise is stale.
    with zipfile.ZipFile(wheel_path) as zf:
        eval_members = [n for n in zf.namelist() if n.startswith("eval/")]
    assert not eval_members, (
        f"wheel unexpectedly contains eval/ files: {eval_members[:5]} -- "
        "packaging boundary changed; update this test's assumptions"
    )

    # 2. Minimal local stubs for the two SEPARATE, network-installed runtime
    # deps reached at import time -- see module docstring for why these are
    # stubbed rather than installed for real.
    stub_pkg = stub_dir / "unified_llm"
    stub_pkg.mkdir(parents=True)
    (stub_pkg / "__init__.py").write_text(
        textwrap.dedent(
            """
            async def resolve_latest_for(provider, glob, stable_only=True):
                raise NotImplementedError("stub -- not exercised by this test")
            """
        ),
        encoding="utf-8",
    )

    pipeline_runner_stub = stub_dir / "amplifier_module_pipeline_runner"
    pipeline_runner_stub.mkdir(parents=True)
    (pipeline_runner_stub / "__init__.py").write_text(
        textwrap.dedent(
            """
            class PipelineResult:
                def __init__(self, status="", notes="", logs_dir=None, raw=""):
                    self.status = status
                    self.notes = notes
                    self.logs_dir = logs_dir
                    self.raw = raw


            async def run_pipeline(dot_source, **kwargs):
                raise NotImplementedError("stub -- not exercised by this test")
            """
        ),
        encoding="utf-8",
    )

    # 3. Fresh venv; install ONLY the built wheel (--no-deps) -- no eval/
    # directory anywhere on the filesystem this venv/process can see, and no
    # network install of the real git-based runtime deps.
    venv = _run(["uv", "venv", str(venv_dir)])
    assert venv.returncode == 0, f"venv creation failed:\n{venv.stderr}"

    python = venv_dir / "bin" / "python"
    install = _run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--no-deps",
            str(wheel_path),
        ]
    )
    assert install.returncode == 0, (
        f"wheel install failed:\nstdout={install.stdout}\nstderr={install.stderr}"
    )

    # 4. The actual regression check: import reweave_overview_if_needed using
    # ONLY what the wheel shipped (+ the unified_llm stub on PYTHONPATH),
    # run from a directory that has no relationship to this repo. Must NOT
    # raise ModuleNotFoundError for grade_wiki/eval.
    probe = textwrap.dedent(
        """
        from wiki_weaver.reweave import reweave_overview_if_needed
        print("IMPORT_OK")
        """
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(stub_dir)
    result = _run(
        [str(python), "-c", probe],
        cwd=str(workdir),  # NOT the repo -- eval/ is not reachable from here
        env=env,
    )

    assert "ModuleNotFoundError" not in result.stderr, (
        "reweave import failed with ModuleNotFoundError -- packaging "
        f"regression reintroduced:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert result.returncode == 0, (
        f"reweave import failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "IMPORT_OK" in result.stdout


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
