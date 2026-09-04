import base64
import io
import json
import socket

from PIL import Image

from navila_orca.vlm_client import LengthPrefixedJsonVLMClient


def test_client_preserves_length_prefixed_json_protocol_and_handles_fragmented_response(
    monkeypatch,
):
    class FragmentingSocket:
        def __init__(self, response):
            self.sent = bytearray()
            header = len(response).to_bytes(8, "big")
            self.fragments = [bytes([byte]) for byte in header]
            self.fragments.extend(
                response[offset : offset + 3] for offset in range(0, len(response), 3)
            )

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def settimeout(self, timeout):
            self.timeout = timeout

        def sendall(self, data):
            self.sent.extend(data)

        def recv(self, size):
            if not self.fragments:
                return b""
            fragment = self.fragments.pop(0)
            if len(fragment) > size:
                self.fragments.insert(0, fragment[size:])
                fragment = fragment[:size]
            return fragment

    response_payload = json.dumps("The next action is stop.").encode("utf-8")
    fake_socket = FragmentingSocket(response_payload)
    monkeypatch.setattr(
        socket, "create_connection", lambda *args, **kwargs: fake_socket
    )
    images = [Image.new("RGB", (8, 8), (index, 0, 0)) for index in range(8)]
    response = LengthPrefixedJsonVLMClient("unused", 54321, timeout_s=2).infer(
        images, "go there"
    )

    request_size = int.from_bytes(fake_socket.sent[:8], "big")
    received = json.loads(bytes(fake_socket.sent[8 : 8 + request_size]).decode("utf-8"))

    assert response == "The next action is stop."
    assert received["query"] == "go there"
    assert len(received["images"]) == 8
    for payload in received["images"]:
        assert Image.open(io.BytesIO(base64.b64decode(payload))).convert(
            "RGB"
        ).size == (8, 8)
