import unittest

from maintenance.loon_audit import config_summary


class LoonAuditTests(unittest.TestCase):
    def test_redacts_subscription_and_certificate(self):
        result = config_summary('fixture.conf', b'''[Plugin]
https://example.invalid/JD_remove_ads_v2.lpx?token=PRIVATE, enabled=true
[Mitm]
enable=true
ca-p12=SECRET_CERT
ca-passphrase=SECRET_PASS
''')
        self.assertEqual(result['plugins'], [{'name': 'JD_remove_ads_v2.lpx', 'enabled_in_file': True}])
        self.assertTrue(result['mitm_config_fields']['ca-p12_present'])
        self.assertFalse(result['certificate_trust_verified'])
        self.assertFalse(result['active_config_verified'])
        self.assertNotIn('PRIVATE', str(result))
        self.assertNotIn('SECRET', str(result))
