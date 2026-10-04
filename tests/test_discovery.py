from spyder_fastapi.core import discover_targets


def test_discovery_finds_fastapi_assignments_without_importing(tmp_path):
    (tmp_path / "main.py").write_text(
        "from fastapi import FastAPI as API\n"
        "app = API()\n"
        "raise RuntimeError('must never be imported by discovery')\n",
        encoding="utf-8",
    )

    package = tmp_path / "services"
    package.mkdir()
    (package / "books.py").write_text(
        "import fastapi as fa\n"
        "application = fa.FastAPI()\n",
        encoding="utf-8",
    )

    ignored = tmp_path / ".venv"
    ignored.mkdir()
    (ignored / "hidden.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n",
        encoding="utf-8",
    )

    candidates = discover_targets(tmp_path)

    assert [candidate.target for candidate in candidates] == [
        "main:app",
        "services.books:application",
    ]
    assert all(candidate.source.file for candidate in candidates)
    assert all(candidate.source.line == 2 for candidate in candidates)


def test_discovery_accepts_a_single_python_file(tmp_path):
    file_path = tmp_path / "api.py"
    file_path.write_text(
        "from fastapi import FastAPI\napi = FastAPI()\n",
        encoding="utf-8",
    )

    candidates = discover_targets(file_path)

    assert [candidate.target for candidate in candidates] == ["api:api"]
