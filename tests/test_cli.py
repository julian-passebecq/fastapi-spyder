from spyder_fastapi.cli import inspect_target


def test_inspection_captures_user_import_output(tmp_path, monkeypatch):
    module = tmp_path / "noisy_app.py"
    module.write_text(
        "print('hello from import')\n"
        "from fastapi import FastAPI\n"
        "app = FastAPI(title='Noisy')\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    model, noise = inspect_target("noisy_app:app")

    assert model.title == "Noisy"
    assert "hello from import" in noise
