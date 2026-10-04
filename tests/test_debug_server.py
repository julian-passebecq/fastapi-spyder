import sys
from types import SimpleNamespace

import pytest

from spyder_fastapi.core import local_debug_server_address
from spyder_fastapi.debug_server import main


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1:8000", ("127.0.0.1", 8000)),
        ("http://localhost:9000/", ("localhost", 9000)),
        ("http://[::1]:7000", ("::1", 7000)),
    ],
)
def test_local_debug_server_address_accepts_loopback_origins(url, expected):
    assert local_debug_server_address(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8000",
        "http://0.0.0.0:8000",
        "http://example.com:8000",
        "http://127.0.0.1:8000/api",
        "http://user:password@127.0.0.1:8000",
        "http://127.0.0.1:8000?debug=1",
    ],
)
def test_local_debug_server_address_rejects_unsafe_or_ambiguous_origins(url):
    with pytest.raises(ValueError):
        local_debug_server_address(url)


def test_debug_server_launcher_calls_uvicorn_without_reload(monkeypatch):
    calls = []

    fake_uvicorn = SimpleNamespace(
        run=lambda *args, **kwargs: calls.append((args, kwargs))
    )
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    exit_code = main(
        [
            "service.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8123",
        ]
    )

    assert exit_code == 0
    assert calls == [
        (
            ("service.main:app",),
            {
                "host": "127.0.0.1",
                "port": 8123,
                "reload": False,
            },
        )
    ]


def test_debug_server_configures_native_telemetry_before_uvicorn(
    monkeypatch,
    tmp_path,
):
    calls = []
    telemetry_calls = []

    fake_uvicorn = SimpleNamespace(
        run=lambda *args, **kwargs: calls.append((args, kwargs))
    )
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    import spyder_fastapi.telemetry_capture as telemetry_capture

    monkeypatch.setattr(
        telemetry_capture,
        "configure_native_telemetry",
        lambda path: telemetry_calls.append(path)
        or {"message": "capture ready"},
    )

    telemetry_file = tmp_path / "native.jsonl"
    exit_code = main(
        [
            "service.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8124",
            "--telemetry-file",
            str(telemetry_file),
        ]
    )

    assert exit_code == 0
    assert telemetry_calls == [str(telemetry_file)]
    assert calls[0][0] == ("service.main:app",)
    assert calls[0][1]["port"] == 8124


def test_debug_server_continues_when_telemetry_setup_fails(
    monkeypatch,
    tmp_path,
):
    calls = []
    fake_uvicorn = SimpleNamespace(
        run=lambda *args, **kwargs: calls.append((args, kwargs))
    )
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    import spyder_fastapi.telemetry_capture as telemetry_capture

    def fail_capture(_path):
        raise RuntimeError("telemetry boom")

    monkeypatch.setattr(
        telemetry_capture,
        "configure_native_telemetry",
        fail_capture,
    )

    exit_code = main(
        [
            "service.main:app",
            "--telemetry-file",
            str(tmp_path / "native.jsonl"),
        ]
    )

    assert exit_code == 0
    assert calls

