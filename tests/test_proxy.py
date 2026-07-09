import unittest
from unittest.mock import patch

from paypal.proxy import build_proxy_config, load_proxy_pool


class ProxyConfigTest(unittest.TestCase):
    def patch_proxy_env(self, values: dict[str, str]):
        return patch("paypal.proxy._load_dotenv_value", side_effect=lambda name: values.get(name, ""))

    def test_proxy_url_env_is_used_when_enabled(self) -> None:
        with self.patch_proxy_env({"PAYPAL_PROXY_URL": "http://user:pass@proxy.local:8080"}):
            proxy = build_proxy_config(enabled=True)

        self.assertTrue(proxy.enabled)
        self.assertEqual(proxy.url, "http://user:pass@proxy.local:8080")
        self.assertEqual(proxy.label, "http://***:***@proxy.local:8080")

    def test_proxy_pool_env_can_select_fixed_index(self) -> None:
        with self.patch_proxy_env(
            {
                "PAYPAL_PROXY_POOL": "host1:1111:user1:pass1,host2:2222:user2:pass2",
            }
        ):
            proxy = build_proxy_config(enabled=True, index=1)

        self.assertEqual(proxy.url, "http://user2:pass2@host2:2222")

    def test_custom_proxy_url_enables_without_env_toggle(self) -> None:
        with self.patch_proxy_env({}):
            proxy = build_proxy_config(proxy_url="http://custom:secret@proxy.local:8080")

        self.assertTrue(proxy.enabled)
        self.assertEqual(proxy.url, "http://custom:secret@proxy.local:8080")

    def test_1024proxy_env_is_ignored(self) -> None:
        with self.patch_proxy_env(
            {
                "PAYPAL_1024_PROXY_HOST": "1024.proxy.local",
                "PAYPAL_1024_PROXY_ACCOUNT": "account",
                "PAYPAL_1024_PROXY_PASSWORD": "password",
                "PAYPAL_1024_PROXY_PORT": "3000",
            }
        ):
            self.assertEqual(load_proxy_pool(), [])
            with self.assertRaises(ValueError):
                build_proxy_config(enabled=True)


if __name__ == "__main__":
    unittest.main()
