import unittest

from app.security.otp import hash_otp, verify_otp


class OtpHashTests(unittest.TestCase):
    def test_hashes_use_unique_salts_and_verify_the_original_code(self):
        code = "123456"
        first = hash_otp(code)
        second = hash_otp(code)

        self.assertNotEqual(first, second)
        self.assertTrue(verify_otp(code, first))
        self.assertTrue(verify_otp(code, second))
        self.assertFalse(verify_otp("654321", first))

    def test_malformed_hashes_fail_closed(self):
        for value in ("", "plaintext", "pbkdf2_sha256$1$bad$bad", None):
            with self.subTest(value=value):
                self.assertFalse(verify_otp("123456", value))
