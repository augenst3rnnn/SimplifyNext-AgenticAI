from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"


def _load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FragmentingSocket:
    def __init__(self, response: bytes) -> None:
        header = len(response).to_bytes(8, "big")
        self.fragments = [bytes([byte]) for byte in header + response]
        self.sent = bytearray()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def recv(self, size: int) -> bytes:
        if not self.fragments:
            return b""
        fragment = self.fragments.pop(0)
        if len(fragment) > size:
            self.fragments.insert(0, fragment[size:])
            return fragment[:size]
        return fragment


def test_checker_sends_health_request_and_validates_fragmented_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checker = _load_script(
        "test_check_navvlm_endpoint", SCRIPTS / "check_navvlm_endpoint.py"
    )
    response = json.dumps(
        {
            "service": "navila-vlm",
            "status": "ok",
            "protocol_version": 1,
        }
    ).encode("utf-8")
    fake_socket = FragmentingSocket(response)
    monkeypatch.setattr(
        checker.socket, "create_connection", lambda *args, **kwargs: fake_socket
    )

    received = checker.check_endpoint("127.0.0.1", 54321, timeout_s=2.0)

    request_size = int.from_bytes(fake_socket.sent[:8], "big")
    request = json.loads(bytes(fake_socket.sent[8 : 8 + request_size]))
    assert request == {"type": "health"}
    assert received == {
        "service": "navila-vlm",
        "status": "ok",
        "protocol_version": 1,
    }


def test_checker_rejects_an_unrelated_tcp_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checker = _load_script(
        "test_check_navvlm_endpoint_wrong_service",
        SCRIPTS / "check_navvlm_endpoint.py",
    )
    response = json.dumps(
        {"service": "other", "status": "ok", "protocol_version": 1}
    ).encode("utf-8")
    fake_socket = FragmentingSocket(response)
    monkeypatch.setattr(
        checker.socket, "create_connection", lambda *args, **kwargs: fake_socket
    )

    with pytest.raises(checker.EndpointHealthError, match="service identity"):
        checker.check_endpoint("127.0.0.1", 54321)


def _load_server(monkeypatch: pytest.MonkeyPatch):
    torch = ModuleType("torch")
    llava = ModuleType("llava")
    llava.__path__ = []
    constants = ModuleType("llava.constants")
    constants.IMAGE_TOKEN_INDEX = 0
    conversation = ModuleType("llava.conversation")
    conversation.SeparatorStyle = SimpleNamespace(TWO=object())
    conversation.conv_templates = {}
    mm_utils = ModuleType("llava.mm_utils")
    mm_utils.KeywordsStoppingCriteria = object
    mm_utils.get_model_name_from_path = lambda path: str(path)
    mm_utils.process_images = lambda *args, **kwargs: None
    model = ModuleType("llava.model")
    model.__path__ = []
    builder = ModuleType("llava.model.builder")
    builder.load_pretrained_model = lambda *args, **kwargs: None

    for name, module in {
        "torch": torch,
        "llava": llava,
        "llava.constants": constants,
        "llava.conversation": conversation,
        "llava.mm_utils": mm_utils,
        "llava.model": model,
        "llava.model.builder": builder,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    return _load_script(
        "test_navila_vlm_server", SCRIPTS / "navila_vlm_server.py"
    )


class InMemoryConnection:
    def __init__(self, request: bytes) -> None:
        self.incoming = bytearray(len(request).to_bytes(8, "big") + request)
        self.sent = bytearray()

    def recv(self, size: int) -> bytes:
        chunk = bytes(self.incoming[:size])
        del self.incoming[:size]
        return chunk

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)


def test_server_health_request_does_not_call_inference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_module = _load_server(monkeypatch)
    server = server_module.NaVILAServer.__new__(server_module.NaVILAServer)

    def fail_inference(*args, **kwargs):
        raise AssertionError("health request must not run model inference")

    server.infer = fail_inference
    request = json.dumps({"type": "health"}).encode("utf-8")
    connection = InMemoryConnection(request)
    server._handle_request(connection)
    response_size = int.from_bytes(connection.sent[:8], "big")
    response = json.loads(bytes(connection.sent[8 : 8 + response_size]))

    assert response == {
        "service": "navila-vlm",
        "status": "ok",
        "protocol_version": 1,
    }
