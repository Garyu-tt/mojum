import unittest
from mojum.agent_key_lifecycle import KeyLifecycleError, build_keyring, signing_key_id, verification_key_id

def k(kid, created, active, retire=None, until=None, revoked=None):
    d={"key_id":kid,"created_at":created,"activates_at":active}
    if retire is not None: d["retires_at"]=retire
    if until is not None: d["verify_until"]=until
    if revoked is not None: d["revoked_at"]=revoked
    return d

class KeyLifecycleTests(unittest.TestCase):
    def test_rotation_and_historical_verification(self):
        ring=build_keyring([k("old",1,2,10,30),k("new",9,10)])
        self.assertEqual(signing_key_id(ring,9),"old")
        self.assertEqual(signing_key_id(ring,10),"new")
        self.assertEqual(verification_key_id(ring,"old",9,20),"old")
    def test_retired_key_cannot_sign(self):
        with self.assertRaises(KeyLifecycleError): signing_key_id(build_keyring([k("old",1,2,10,30)]),10)
    def test_revocation_blocks_historical_verification(self):
        ring=build_keyring([k("revoked",1,2,10,30,15)])
        with self.assertRaises(KeyLifecycleError): verification_key_id(ring,"revoked",9,12)
    def test_retention_expiry_blocks_verification(self):
        ring=build_keyring([k("old",1,2,10,30),k("new",9,10)])
        with self.assertRaises(KeyLifecycleError): verification_key_id(ring,"old",9,31)
    def test_unknown_and_future_events_rejected(self):
        ring=build_keyring([k("one",1,2)])
        with self.assertRaises(KeyLifecycleError): verification_key_id(ring,"missing",2,3)
        with self.assertRaises(KeyLifecycleError): verification_key_id(ring,"one",4,3)
    def test_overlapping_windows_rejected(self):
        with self.assertRaises(KeyLifecycleError): build_keyring([k("a",1,2,12),k("b",8,10)])
    def test_duplicate_ids_and_algorithm_rejected(self):
        with self.assertRaises(KeyLifecycleError): build_keyring([k("a",1,2),k("a",2,3)])
        bad=k("b",1,2); bad["algorithm"]="SHA1"
        with self.assertRaises(KeyLifecycleError): build_keyring([bad])
    def test_bool_and_oversized_timestamp_rejected(self):
        with self.assertRaises(KeyLifecycleError): build_keyring([k("a",True,2)])
        with self.assertRaises(KeyLifecycleError): build_keyring([k("a",1,2**80)])
    def test_unknown_fields_rejected(self):
        bad=k("a",1,2); bad["secret"]="never"
        with self.assertRaises(KeyLifecycleError): build_keyring([bad])
    def test_verify_until_requires_retirement(self):
        with self.assertRaises(KeyLifecycleError): build_keyring([k("a",1,2,until=10)])
    def test_digest_stable_and_detached(self):
        item=k("a",1,2); first=build_keyring([item]); item["key_id"]="changed"
        self.assertEqual(first.digest,build_keyring([k("a",1,2)]).digest)
        self.assertEqual(first.keys[0].key_id,"a")
        self.assertEqual(len(first.digest),64)

if __name__=="__main__": unittest.main()
