"""Synthetic credentials and mocked HTTP only. Never reads the Pi's config."""
import hashlib
import hmac
import json
from datetime import datetime
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "candidate/setup_gui"))
sys.path.insert(0, str(ROOT / "candidate/gps"))
import presence
from network import SetupError

SECRET = "synthetic-presence-secret"
KEY = bytes.fromhex("daa8b2a229e6cd254815ad4fa1537b2dade638ea06d52b17b185e2e13458f8c1")
CHALLENGE = "a" * 64
RECEIVED = "2026-10-18T04:00:00.123Z"
SIGNATURE = "637014c3fea7fec6a210d6ec9ec13c55bbdf3c0a88e36bcb63e8e644e9cf40ea"
PROOF = "46ed4770036efc7a9d0cbf1eb4fda274d441a7939f474417293dc5340f863632"
OBSERVATION = {"wifiConnected": True, "internet": "full"}


def challenge(**changes):
    return {"ok": True, "deviceId": "GPS-001", "challenge": CHALLENGE, "expiresInSeconds": 30, **changes}


def acknowledgement(**changes):
    return {"ok": True, "deviceId": "GPS-001", "receivedAt": RECEIVED,
            "scope": "setup-presence-only", "proof": PROOF, **changes}


def response(report, status=200):
    item = Mock(status=status)
    item.read.return_value = json.dumps(report).encode() if isinstance(report, dict) else report
    item.__enter__ = Mock(return_value=item)
    item.__exit__ = Mock(return_value=False)
    return item


@unittest.skipUnless(os.name == 'posix', 'Pi owner-private file checks require POSIX')
class PrivateConfigTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.home = Path(self.folder.name)
        self.file = self.home / "synthetic.env"
        self.file.write_text('CENTRABUS_DEVICE_ID=GPS-001\nCENTRABUS_DEVICE_SECRET="' + SECRET + '"\n')
        self.file.chmod(0o600)

    def tearDown(self):
        self.folder.cleanup()

    def test_key_matches_independent_node_crypto_vector(self):
        self.assertEqual(presence.derive_key(SECRET), KEY)
        self.assertEqual(presence.read_key([self.file], "GPS-001", home=self.home), KEY)

    def test_default_discovery_uses_only_documented_existing_file_metadata(self):
        folder = self.home / 'centrabus-device'; folder.mkdir()
        config = folder / 'GPS-001.env'; config.touch()
        with patch.object(Path, 'open', side_effect=AssertionError('must not read configuration')):
            self.assertEqual(presence.default_config_files('GPS-001', home=self.home), (str(config),))
            self.assertEqual(presence.default_config_files('GPS-002', home=self.home), ())

    def test_split_existing_files_are_read_without_evaluating_other_environment_values(self):
        self.file.write_text('CENTRABUS_DEVICE_ID=GPS-001\nCENTRABUS_API_URL=$(must-never-run)\n')
        second = self.home / "identity.env"
        second.write_text('export CENTRABUS_DEVICE_SECRET="' + SECRET + '"\n')
        second.chmod(0o600)
        self.assertEqual(presence.read_key([self.file, second], "GPS-001", home=self.home), KEY)

    def test_other_identity_missing_secret_and_malformed_config_fail_without_echo(self):
        for text in ('CENTRABUS_DEVICE_ID=GPS-002\nCENTRABUS_DEVICE_SECRET=' + SECRET,
                     'CENTRABUS_DEVICE_ID=GPS-001', 'CENTRABUS_DEVICE_SECRET="' + SECRET):
            self.file.write_text(text)
            with self.assertRaises(SetupError) as error:
                presence.read_key([self.file], "GPS-001", home=self.home)
            self.assertNotIn(SECRET, str(error.exception))

    def test_public_permissions_symlinks_and_outside_home_are_rejected(self):
        self.file.chmod(0o644)
        with self.assertRaises(SetupError): presence.read_key([self.file], "GPS-001", home=self.home)
        self.file.chmod(0o600)
        link = self.home / "link.env"; link.symlink_to(self.file)
        with self.assertRaises(SetupError): presence.read_key([link], "GPS-001", home=self.home)
        inside = self.home / "different-home"; inside.mkdir()
        with self.assertRaises(SetupError): presence.read_key([self.file], "GPS-001", home=inside)
        for files in ([], [self.file] * 4):
            with self.assertRaises(SetupError): presence.read_key(files, "GPS-001", home=self.home)


class ProtocolTests(unittest.TestCase):
    def client(self, first=None, second=None, **kwargs):
        opener = Mock()
        opener.open.side_effect = [response(challenge() if first is None else first),
                                    response(acknowledgement() if second is None else second)]
        kwargs.setdefault('wall_clock', lambda: datetime.fromisoformat(RECEIVED.replace('Z','+00:00')).timestamp())
        return presence.PresenceClient("http://192.168.1.5:5000", "GPS-001", KEY, opener=opener, **kwargs), opener

    def test_exact_wire_contract_and_independent_signature_and_proof_vectors(self):
        client, opener = self.client()
        self.assertEqual(client.check_in(OBSERVATION)["receivedAt"], RECEIVED)
        first, second = [call.args[0] for call in opener.open.call_args_list]
        self.assertEqual(first.full_url, "http://192.168.1.5:5000/api/device-presence/challenge/GPS-001")
        self.assertEqual(first.get_method(), "GET")
        self.assertEqual(second.full_url, "http://192.168.1.5:5000/api/device-presence/check-in")
        self.assertEqual(second.get_method(), "POST")
        headers = {key.lower(): value for key, value in second.header_items()}
        self.assertEqual(headers, {"content-type": "application/json", "x-centrabus-signature": SIGNATURE})
        self.assertEqual(json.loads(second.data), {"deviceId": "GPS-001", "challenge": CHALLENGE, **OBSERVATION})
        self.assertNotIn(SECRET, second.data.decode())
        self.assertTrue(all(call.kwargs == {"timeout": 4} for call in opener.open.call_args_list))

    def test_false_and_unknown_wifi_have_exact_signing_text(self):
        for wifi, text in ((False, "false"), (None, "null")):
            client, opener = self.client()
            self.assertEqual(client.check_in({"wifiConnected": wifi, "internet": "unknown"})["receivedAt"], RECEIVED)
            expected = hmac.new(KEY, "\n".join((presence.DOMAIN, CHALLENGE, "GPS-001", text, "unknown")).encode(), hashlib.sha256).hexdigest()
            self.assertEqual(dict(opener.open.call_args.args[0].header_items())["X-centrabus-signature"], expected)

    def test_invalid_network_observations_send_nothing(self):
        for observation in ({"wifiConnected": 1, "internet": "full"}, {"wifiConnected": True, "internet": "invented"}):
            client, opener = self.client()
            with self.assertRaises(SetupError): client.check_in(observation)
            opener.open.assert_not_called()

    def test_invalid_challenge_never_posts(self):
        for change in ({"ok": 1}, {"deviceId": "GPS-002"}, {"challenge": "A" * 64}, {"challenge": "bad"},
                       {"expiresInSeconds": True}, {"expiresInSeconds": 31}, {"expiresInSeconds": 0}):
            client, opener = self.client(first=challenge(**change))
            with self.assertRaises(SetupError): client.check_in(OBSERVATION)
            self.assertEqual(opener.open.call_count, 1)

    def test_cancelled_or_expired_challenge_is_not_posted_or_reused(self):
        client, opener = self.client()
        with self.assertRaises(SetupError): client.check_in(OBSERVATION, cancelled=Mock(side_effect=[False, True]))
        self.assertEqual(opener.open.call_count, 1)
        client, opener = self.client(clock=Mock(side_effect=[100, 131]))
        with self.assertRaises(SetupError): client.check_in(OBSERVATION)
        self.assertEqual(opener.open.call_count, 1)

    def test_forged_or_mismatched_acknowledgements_never_report_success(self):
        for change in ({"ok": 1}, {"deviceId": "GPS-002"}, {"scope": "backend-process-only"}, {"proof": "0" * 64},
                       {"proof": PROOF.upper()}, {"receivedAt": "2026-10-18T04:00:00Z"},
                       {"receivedAt": "2026-13-18T04:00:00.123Z"}, {"receivedAt": "2026-10-18T04:00:01.123Z"}):
            client, opener = self.client(second=acknowledgement(**change))
            with patch.object(presence.hmac, "compare_digest", wraps=hmac.compare_digest) as compare:
                with self.assertRaises(SetupError): client.check_in(OBSERVATION)
            if change.get("proof") == "0" * 64:
                compare.assert_called_once()

    def test_new_challenge_is_requested_for_each_exchange(self):
        nonce = "b" * 64
        proof = hmac.new(KEY, "\n".join((presence.DOMAIN, "ack", "GPS-001", nonce, RECEIVED)).encode(), hashlib.sha256).hexdigest()
        client, opener = self.client()
        opener.open.side_effect = [response(challenge()), response(acknowledgement()),
                                    response(challenge(challenge=nonce)), response(acknowledgement(proof=proof))]
        client.check_in(OBSERVATION); client.check_in(OBSERVATION)
        posts = [call.args[0] for call in opener.open.call_args_list if call.args[0].get_method() == "POST"]
        self.assertEqual([json.loads(item.data)["challenge"] for item in posts], [CHALLENGE, nonce])
        self.assertNotEqual(posts[0].get_header("X-centrabus-signature"), posts[1].get_header("X-centrabus-signature"))

    def test_reused_challenge_is_not_signed_again(self):
        client, opener = self.client()
        opener.open.side_effect = [response(challenge()), response(acknowledgement()), response(challenge())]
        client.check_in(OBSERVATION)
        with self.assertRaises(SetupError): client.check_in(OBSERVATION)
        self.assertEqual(opener.open.call_count, 3)

    def test_old_signed_ack_does_not_extend_presence_and_clock_skew_is_bounded(self):
        epoch = datetime.fromisoformat(RECEIVED.replace('Z','+00:00')).timestamp()
        client, opener = self.client(clock=lambda:100, wall_clock=lambda:epoch+40)
        self.assertEqual(client.check_in(OBSERVATION)["expiresAtMonotonic"], 105)
        for age in (45, -6):
            client, opener = self.client(wall_clock=lambda:epoch+age)
            with self.assertRaises(SetupError): client.check_in(OBSERVATION)

    def test_unbounded_duplicate_malformed_and_http_failures_are_sanitized(self):
        for payload in (b"x" * 4097, b"not-json", b"[]", b'{"ok":true,"ok":false}'):
            client, opener = self.client(first=payload)
            with self.assertRaises(SetupError): client.check_in(OBSERVATION)
            self.assertEqual(opener.open.call_count, 1)
        client, opener = self.client()
        opener.open.side_effect = TimeoutError(SECRET)
        with self.assertRaises(SetupError) as error: client.check_in(OBSERVATION)
        self.assertNotIn(SECRET, str(error.exception))

    def test_default_transport_disables_proxy_and_redirect_and_close_drops_key(self):
        with patch.object(presence, "build_opener", return_value=Mock()) as build:
            client = presence.PresenceClient("https://api.example.test", "GPS-001", KEY)
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsInstance(build.call_args.args[1], presence.NoRedirect)
        client.close()
        self.assertIsNone(client.key)
        with self.assertRaises(SetupError): client.check_in(OBSERVATION)


class SessionTests(unittest.TestCase):
    def test_expiry_is_monotonic_and_does_not_extend_after_failed_attempt(self):
        now = [100.0]
        session = presence.PresenceSession(Mock(), lambda: OBSERVATION, deadline=200, clock=lambda: now[0])
        session.thread = Mock(); session.thread.is_alive.return_value = True
        session.confirmed_at = now[0]; session.received_at = RECEIVED
        self.assertEqual(session.status()["state"], "online")
        now[0] = 144; session.last_failed = True
        self.assertEqual(session.status()["state"], "online")
        self.assertEqual(session.status()["expiresInSeconds"], 1)
        now[0] = 145
        self.assertEqual(session.status()["state"], "expired")
        self.assertNotIn(SECRET, json.dumps(session.status()))

    def test_failure_waits_for_normal_interval_without_replaying_or_rapid_retry(self):
        client = Mock(); client.check_in.side_effect = RuntimeError(SECRET)
        session = presence.PresenceSession(client, lambda: OBSERVATION, deadline=200, clock=lambda: 100)
        event = Mock(); event.is_set.return_value = False
        event.wait.side_effect = lambda delay: setattr(event.is_set, "return_value", True)
        session.stop_event = event
        session._run()
        event.wait.assert_called_once_with(15)
        client.check_in.assert_called_once()
        client.close.assert_called_once()
        self.assertIsNone(session.confirmed_at)

    def test_expired_gui_session_sends_nothing(self):
        client = Mock()
        session = presence.PresenceSession(client, lambda: OBSERVATION, deadline=99, clock=lambda: 100)
        session._run()
        client.check_in.assert_not_called(); client.close.assert_called_once()

    def test_stop_joins_only_owned_worker_and_never_creates_another_attempt(self):
        client = Mock(); client.check_in.return_value = {"receivedAt": RECEIVED, "expiresAtMonotonic": time.monotonic() + 45}
        session = presence.PresenceSession(client, lambda: OBSERVATION, deadline=time.monotonic() + 60)
        session.start()
        limit = time.monotonic() + 2
        while session.confirmed_at is None and time.monotonic() < limit:
            time.sleep(.005)
        self.assertIsNotNone(session.confirmed_at)
        with self.assertRaises(SetupError): session.start()
        session.stop()
        self.assertFalse(session.running)
        self.assertEqual(session.status()["state"], "stopped")
        self.assertEqual(client.check_in.call_count, 1)


if __name__ == "__main__":
    unittest.main()
