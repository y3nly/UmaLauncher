from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest import mock

import msgpack
from Cryptodome.Cipher import AES


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "umalauncher"))

import carrotjuicer


def frame(message_type: int, payload: bytes) -> bytes:
    return bytes((message_type,)) + len(payload).to_bytes(2, "big") + payload


class CarrotBlenderProtocolFunctionalTests(unittest.TestCase):
    def test_encrypted_multipart_response_and_request_are_dispatched(self):
        response_payload = {"data": {"chara_info": {"turn": 3}}}
        plaintext = b"\0\0\0\0" + msgpack.packb(response_payload)
        plaintext += b"\0" * (-len(plaintext) % AES.block_size)
        key = bytes(range(16))
        iv = bytes(range(16, 32))
        encrypted = AES.new(key, AES.MODE_CBC, iv=iv).encrypt(plaintext)

        juicer = carrotjuicer.CarrotJuicer.__new__(carrotjuicer.CarrotJuicer)
        juicer.encrypted_data = None
        juicer.key = None
        juicer.iv = None
        juicer._multipart_chunks = None
        juicer.handle_response = mock.Mock()
        juicer.handle_request = mock.Mock()

        split_at = len(encrypted) // 2
        chunks_left = juicer.process_carrotblender_datagram(bytes((4, 2)))
        chunks_left = juicer.process_carrotblender_datagram(
            frame(5, encrypted[:split_at]), chunks_left
        )
        chunks_left = juicer.process_carrotblender_datagram(
            frame(5, encrypted[split_at:]), chunks_left
        )
        juicer.process_carrotblender_datagram(frame(1, key), chunks_left)
        juicer.process_carrotblender_datagram(frame(2, iv), chunks_left)

        self.assertEqual(chunks_left, 0)
        juicer.handle_response.assert_called_once_with(
            response_payload, is_json=True
        )

        request_payload = b"\0\0\0\0" + msgpack.packb({"current_turn": 4})
        juicer.process_carrotblender_datagram(frame(3, request_payload))
        juicer.handle_request.assert_called_once_with(
            request_payload, is_json=True
        )


if __name__ == "__main__":
    unittest.main()
