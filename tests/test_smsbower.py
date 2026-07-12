import importlib
import tempfile
import time
import unittest
from pathlib import Path
from typing import final
from unittest.mock import patch

from paypal.models import BillingAddress, CardInfo, UserInfo
from paypal.flow import PayPalFlow
from web import JOBS, WebJob, WebPayPalFlow, create_job

smsbower_module = importlib.import_module("paypal.smsbower")
SMSBowerActivationStore = getattr(smsbower_module, "SMSBowerActivationStore")
SMSBowerOtpProvider = getattr(smsbower_module, "SMSBowerOtpProvider")


@final
class FakeSMSBowerClient:
    def __init__(self) -> None:
        self.prices: list[dict[str, object]] = [
            {"provider_id": "10", "price": 0.35, "count": 4},
            {"provider_id": "20", "price": 0.52, "count": 9},
        ]
        self.next_activation_id: int = 1000
        self.status_by_activation: dict[str, list[str]] = {}
        self.status_changes: list[tuple[str, int]] = []
        self.number_requests: list[str] = []
        self.last_price_request: tuple[str, str] = ("", "")

    def get_provider_prices(self, service: str, country: str) -> list[dict[str, object]]:
        self.last_price_request = (service, country)
        return list(self.prices)

    def get_number_v2(self, *, service: str, country: str, provider_id: str, max_price: float) -> dict[str, object]:
        _ = service
        self.number_requests.append(provider_id)
        self.next_activation_id += 1
        activation_id = str(self.next_activation_id)
        phone = "+5511999" + activation_id[-4:]
        self.status_by_activation[activation_id] = ["STATUS_OK:654321"]
        return {
            "activationId": activation_id,
            "phoneNumber": phone,
            "activationCost": max_price,
            "countryCode": country,
            "activationOperator": provider_id,
            "canGetAnotherSms": True,
            "activationTime": int(time.time()),
        }

    def get_status(self, activation_id: str) -> str:
        values = self.status_by_activation.get(str(activation_id), [])
        if values:
            return values.pop(0)
        return "STATUS_WAIT_CODE"

    def set_status(self, activation_id: str, status: int) -> str:
        self.status_changes.append((str(activation_id), int(status)))
        return "ACCESS_READY"


class SMSBowerProviderTest(unittest.TestCase):
    def make_provider(self, client: FakeSMSBowerClient, cache_path: Path):
        return SMSBowerOtpProvider(
            client=client,
            store=SMSBowerActivationStore(cache_path),
            wait_seconds=0.01,
            poll_interval_seconds=0.01,
            max_channel_failures=3,
            activation_ttl_seconds=1200,
        )

    def test_reused_successful_number_times_out_then_new_number_is_acquired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "smsbower.json"
            store = SMSBowerActivationStore(cache_path)
            store.remember_success(
                activation_id="old-1",
                phone_number="+5511987654321",
                provider_id="10",
                price=0.35,
                expires_at=time.time() + 600,
            )
            client = FakeSMSBowerClient()
            client.status_by_activation["old-1"] = ["STATUS_WAIT_CODE", "STATUS_WAIT_CODE"]
            provider = self.make_provider(client, cache_path)

            reused = provider.reserve_number()
            code = provider.wait_for_code(reused)
            if code is None:
                provider.abandon(reused, "timeout")
            fresh = provider.reserve_number()

            self.assertTrue(reused.reused)
            self.assertIsNone(code)
            self.assertEqual(fresh.provider_id, "10")
            self.assertFalse(fresh.reused)
            self.assertIn(("old-1", 3), client.status_changes)
            self.assertIn(("old-1", 8), client.status_changes)

    def test_cheapest_failed_channel_is_skipped_after_three_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "smsbower.json"
            store = SMSBowerActivationStore(cache_path)
            for _ in range(3):
                store.record_failure("10")
            client = FakeSMSBowerClient()
            provider = self.make_provider(client, cache_path)

            activation = provider.reserve_number()

            self.assertEqual(activation.provider_id, "20")
            self.assertEqual(client.number_requests, ["20"])

    def test_no_reuse_always_reserves_a_new_number_and_skips_cache_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "smsbower.json"
            store = SMSBowerActivationStore(cache_path)
            store.remember_success(
                activation_id="old-1",
                phone_number="+5511987654321",
                provider_id="10",
                price=0.35,
                expires_at=time.time() + 600,
            )
            client = FakeSMSBowerClient()
            provider = SMSBowerOtpProvider(
                client=client,
                store=store,
                wait_seconds=0.01,
                poll_interval_seconds=0.01,
                activation_ttl_seconds=1200,
                reuse_numbers=False,
            )

            activation = provider.reserve_number()
            code = provider.wait_for_code(activation)
            provider.register_confirmation_result(activation, confirmed=True)
            cached_rows = store.load()["activations"]

            self.assertFalse(activation.reused)
            self.assertNotEqual(activation.activation_id, "old-1")
            self.assertEqual(client.number_requests, ["10"])
            self.assertNotIn(("old-1", 3), client.status_changes)
            self.assertEqual(code, "654321")
            self.assertIsInstance(cached_rows, list)
            self.assertEqual(
                [str(row.get("activation_id")) for row in cached_rows if isinstance(row, dict)],
                ["old-1"],
            )


class SMSBowerFlowTest(unittest.TestCase):
    def test_flow_uses_smsbower_code_without_manual_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeSMSBowerClient()
            provider = SMSBowerOtpProvider(
                client=client,
                store=SMSBowerActivationStore(Path(tmp) / "smsbower.json"),
                wait_seconds=0.01,
                poll_interval_seconds=0.01,
            )
            flow = AutoSmsFlow(provider)

            flow._confirm_phone_with_retry("EC-TEST123", "https://www.paypal.com/checkoutweb/signup")

            self.assertEqual(flow.confirmed_codes, ["654321"])
            self.assertTrue(provider.store.reusable_activation(time.time()))

    def test_web_flow_smsbower_mode_does_not_wait_for_manual_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeSMSBowerClient()
            provider = SMSBowerOtpProvider(
                client=client,
                store=SMSBowerActivationStore(Path(tmp) / "smsbower.json"),
                wait_seconds=0.01,
                poll_interval_seconds=0.01,
            )
            job = FakeWebJob()
            flow = AutoSmsWebFlow(provider, job)

            flow._confirm_phone_with_retry("EC-TEST123", "https://www.paypal.com/checkoutweb/signup")

            self.assertEqual(flow.confirmed_codes, ["654321"])
            self.assertFalse(job.waited_for_input)

    def test_web_job_carries_no_reuse_sms_setting(self) -> None:
        class NoopThread:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def start(self) -> None:
                pass

        with patch("web.threading.Thread", NoopThread):
            job = create_job(
                owner_device_id="a" * 32,
                ba_token="BA-TESTTOKEN123",
                phone="+5511987654321",
                debug=False,
                max_card_attempts=1,
                sms_provider="manual",
                sms_reuse_numbers=False,
            )
        try:
            self.assertFalse(job.sms_reuse_numbers)
            self.assertFalse(job.to_dict(include_logs=False)["sms_reuse_numbers"])
        finally:
            JOBS.pop(job.id, None)


class AutoSmsFlow(PayPalFlow):
    def __init__(self, provider) -> None:
        self.confirmed_codes: list[str] = []
        super().__init__(
            ba_token="BA-TESTTOKEN123",
            user=UserInfo(
                first_name="Ana",
                last_name="Silva",
                email="ana@example.com",
                phone="+5500000000000",
                phone_local="00000000000",
                phone_country_code="+55",
                password="Password123!",
                dob="01/01/1990",
                cpf="123.456.789-09",
            ),
            card=CardInfo("4111111111111111", "11/2029", "123", "CREDIT"),
            address=BillingAddress(
                street="Rua XV de Novembro",
                house_number="100",
                district="Centro",
                city="Curitiba",
                state="PR",
                postal_code="80020-310",
            ),
            proxy_enabled=False,
            sms_provider=provider,
        )

    def _initiate_2fa_phone_confirmation(self, token: str, signup_url: str) -> tuple[str, str]:
        _ = (token, signup_url)
        if self.user.phone != "+55119991001":
            raise AssertionError(self.user.phone)
        return "auth-1", "challenge-1"

    def _confirm_2fa_phone_confirmation(
        self,
        token: str,
        signup_url: str,
        auth_id: str,
        challenge_id: str,
        otp: str,
    ) -> bool:
        _ = (token, signup_url, auth_id, challenge_id)
        self.confirmed_codes.append(otp)
        return True


@final
class FakeWebJob(WebJob):
    def __init__(self) -> None:
        super().__init__(
            id="test-job",
            owner_device_id="test-device",
            ba_token="BA-TESTTOKEN123",
            phone="",
            sms_provider="smsbower",
        )
        self.waited_for_input = False

    def set_status(self, status: str, stage: str | None = None) -> None:
        _ = (status, stage)

    def set_generated(self, generated: dict[str, object]) -> None:
        _ = generated

    def wait_for_input(self, prompt: str) -> str:
        _ = prompt
        self.waited_for_input = True
        raise AssertionError("SMSBower web mode should not wait for manual OTP input")


class AutoSmsWebFlow(WebPayPalFlow):
    def __init__(self, provider, job: FakeWebJob) -> None:
        self.confirmed_codes: list[str] = []
        super().__init__(
            ba_token="BA-TESTTOKEN123",
            user=UserInfo(
                first_name="Ana",
                last_name="Silva",
                email="ana@example.com",
                phone="+5500000000000",
                phone_local="00000000000",
                phone_country_code="+55",
                password="Password123!",
                dob="01/01/1990",
                cpf="123.456.789-09",
            ),
            card=CardInfo("4111111111111111", "11/2029", "123", "CREDIT"),
            address=BillingAddress(
                street="Rua XV de Novembro",
                house_number="100",
                district="Centro",
                city="Curitiba",
                state="PR",
                postal_code="80020-310",
            ),
            proxy_enabled=False,
            sms_provider=provider,
            job=job,
        )

    def _initiate_2fa_phone_confirmation(self, token: str, signup_url: str) -> tuple[str, str]:
        _ = (token, signup_url)
        if self.user.phone != "+55119991001":
            raise AssertionError(self.user.phone)
        return "auth-1", "challenge-1"

    def _confirm_2fa_phone_confirmation(
        self,
        token: str,
        signup_url: str,
        auth_id: str,
        challenge_id: str,
        otp: str,
    ) -> bool:
        _ = (token, signup_url, auth_id, challenge_id)
        self.confirmed_codes.append(otp)
        return True


if __name__ == "__main__":
    _ = unittest.main()
