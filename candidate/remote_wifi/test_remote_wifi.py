import base64
from datetime import datetime, timezone, timedelta
import hashlib
import hmac
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'qa'))
from agent import Client, Journal, Worker, SetupError, digest
from device_config import DOMAIN, derive_key
from remote_wifi_fixture import FakeManager

def command(worker, operation, args=None, **overrides):
    now = datetime.now(timezone.utc)
    return {'commandId': uuid.uuid4().hex, 'deviceId': worker.device_id, 'agentId': worker.agent_id, 'operation': operation,
            'args': args or {}, 'issuedAt': now.isoformat(timespec='milliseconds').replace('+00:00','Z'),
            'expiresAt': (now+timedelta(seconds=60)).isoformat(timespec='milliseconds').replace('+00:00','Z'), **overrides}

class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.path = Path(self.temp.name)/'journal.json'
        self.manager = FakeManager(); self.worker = Worker(self.manager, 'GPS-001', Journal(self.path, 'GPS-001'))
    def tearDown(self): self.worker.close(); self.temp.cleanup()
    def deliver(self, item):
        self.worker.deliver(item)
        if self.worker.thread: self.worker.thread.join(2); self.assertFalse(self.worker.thread.is_alive())
    def scan(self):
        self.deliver(command(self.worker, 'scan'))
        scan = self.worker.scan
        return {'scanId': scan['scanId'], 'networkId': scan['networks'][0]['id'], 'password': 'synthetic-wifi-password', 'priority': 50}
    def test_wrong_device_agent_or_unknown_operation_never_mutates(self):
        for change in [{'deviceId':'GPS-002'}, {'agentId':'f'*32}, {'operation':'start-gps'}]:
            item=command(self.worker,'scan'); item.update(change)
            with self.assertRaises(SetupError): self.deliver(item)
        self.assertEqual(self.manager.created, 0); self.assertFalse(self.path.exists())
    def test_expired_command_is_reported_without_execution(self):
        now=datetime.now(timezone.utc)
        self.deliver(command(self.worker,'scan',issuedAt=(now-timedelta(seconds=61)).isoformat(timespec='milliseconds').replace('+00:00','Z'),
                             expiresAt=(now-timedelta(seconds=1)).isoformat(timespec='milliseconds').replace('+00:00','Z')))
        self.assertEqual(self.worker.last_result['outcome'],'expired'); self.assertIsNone(self.worker.scan)
    def test_scan_connect_keep_journal_never_contains_password(self):
        args=self.scan(); connect=command(self.worker,'connect',args); self.deliver(connect)
        self.assertEqual(self.worker.phase,'awaiting_confirmation'); self.assertEqual(args['password'],'')
        self.assertNotIn('synthetic-wifi-password',self.path.read_text())
        self.deliver(command(self.worker,'keep',{'trialId':connect['commandId']}))
        self.assertEqual(self.manager.kept,1); self.assertEqual(self.worker.phase,'applied'); self.assertIsNone(self.worker.journal.data['trial'])
    def test_duplicate_and_restarted_session_never_reexecute_a_command(self):
        args=self.scan(); connect=command(self.worker,'connect',args); self.deliver(connect); self.deliver(connect)
        self.assertEqual(self.manager.created,1)
        restarted=Worker(self.manager,'GPS-001',Journal(self.path,'GPS-001'))
        self.assertEqual(restarted.phase,'recovery_required')
        with self.assertRaises(SetupError): restarted.deliver(connect)
        self.assertEqual(self.manager.created,1)
    def test_local_timeout_rolls_back_without_backend(self):
        args=self.scan(); self.deliver(command(self.worker,'connect',args)); self.worker._rollback()
        self.assertEqual(self.manager.active,self.manager.baseline); self.assertEqual(self.worker.phase,'rolled_back')
        self.assertEqual(self.manager.rolled_back,1); self.assertIsNone(self.worker.journal.data['trial'])
    def test_closing_during_a_trial_restores_the_original_connection(self):
        args=self.scan(); self.deliver(command(self.worker,'connect',args)); self.worker.close()
        self.assertEqual(self.manager.rolled_back,1); self.assertEqual(self.manager.active,self.manager.baseline)
    def test_keep_for_a_different_trial_does_not_commit(self):
        args=self.scan(); self.deliver(command(self.worker,'connect',args)); self.deliver(command(self.worker,'keep',{'trialId':'f'*32}))
        self.assertEqual(self.manager.kept,0); self.assertIsNotNone(self.worker.trial)
    def test_stale_scan_never_activates_a_profile(self):
        args=self.scan(); self.worker.scanned_at-=61; self.deliver(command(self.worker,'connect',args))
        self.assertEqual(self.manager.created,0); self.assertEqual(self.worker.last_result['outcome'],'failed')
    def test_crash_recovery_requires_exact_fallback_and_only_removes_created_profile(self):
        args=self.scan(); self.deliver(command(self.worker,'connect',args))
        restarted=Worker(self.manager,'GPS-001',Journal(self.path,'GPS-001'))
        self.assertFalse(restarted.recover()); self.manager.checkpoint_exists=False
        self.assertFalse(restarted.recover()); self.manager.active=self.manager.baseline
        self.assertTrue(restarted.recover()); self.assertIsNone(restarted.journal.data['trial'])
    def test_wrong_identity_journal_is_rejected(self):
        self.worker.journal.save()
        with self.assertRaises(SetupError): Journal(self.path,'GPS-002')
    def test_readonly_permissions_are_not_reported_as_connect_capability(self):
        self.manager.allow_changes=False; self.assertFalse(self.worker.report()['canChange'])
    def test_prepared_hook_rejects_expiry_before_a_profile_is_created(self):
        args=self.scan()
        item=command(self.worker,'connect',args)
        original=self.manager.begin
        def delayed(*values,**options):
            with patch('agent.time.time',return_value=datetime.fromisoformat(item['expiresAt'].replace('Z','+00:00')).timestamp()+1):
                return original(*values,**options)
        with patch.object(self.manager,'begin',side_effect=delayed):self.deliver(item)
        self.assertEqual(self.manager.created,0)

class ClientTests(unittest.TestCase):
    def test_domain_key_matches_raw_digest_hmac_and_is_separate_from_presence(self):
        expected=hmac.digest(hashlib.sha256(b'synthetic-device-secret-only').digest(),DOMAIN.encode(),'sha256')
        self.assertEqual(derive_key('synthetic-device-secret-only'),expected)
        self.assertNotEqual(expected,hmac.digest(hashlib.sha256(b'synthetic-device-secret-only').digest(),b'centrabus-local-setup-checkin-v1','sha256'))
    def test_real_client_rejects_http_credentials_paths_and_other_device(self):
        key=derive_key('synthetic-device-secret-only')
        for url in ['http://192.168.1.2:5000','https://user:pass@example.test','https://example.test/path','https://example.test?x=1']:
            with self.assertRaises(SetupError):Client(url,'GPS-001',key,lambda:'127.0.0.1')
        with self.assertRaises(SetupError):Client('https://example.test','GPS-003',key,lambda:'127.0.0.1')
    def test_invalid_acknowledgement_never_delivers_a_command(self):
        key=derive_key('synthetic-device-secret-only'); client=Client('https://api.example.test','GPS-001',key,lambda:'127.0.0.1')
        nonce='a'*64; received=datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
        payload=base64.b64encode(json.dumps({'command':None}).encode()).decode()
        ack={'ok':True,'deviceId':'GPS-001','scope':'remote-wifi-control','receivedAt':received,'payload':payload,
             'proof':digest(key,'ack','GPS-001',nonce,received,payload)}
        for changes in [{'deviceId':'GPS-002'},{'proof':'0'*64},{'scope':'setup-presence-only'},
                        {'receivedAt':'2000-01-01T00:00:00.000Z'}]:
            fresh=Client('https://api.example.test','GPS-001',key,lambda:'127.0.0.1')
            with patch.object(fresh,'request',side_effect=[{'ok':True,'deviceId':'GPS-001','challenge':nonce,'expiresInSeconds':30},{**ack,**changes}]):
                with self.assertRaises(SetupError):fresh.poll({})

if __name__=='__main__':unittest.main()
