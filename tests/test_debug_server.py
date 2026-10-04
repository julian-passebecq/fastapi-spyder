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
