import re
import time

from paypal import models


_BR_EMAIL_DOMAINS = (
    "gmail.com",
    "hotmail.com",
    "outlook.com",
    "yahoo.com.br",
    "icloud.com",
    "uol.com.br",
    "bol.com.br",
)

_BR_STATE_NAMES = {
    "SP": "São Paulo",
    "RJ": "Rio de Janeiro",
    "MG": "Minas Gerais",
    "BA": "Bahia",
    "PR": "Paraná",
    "RS": "Rio Grande do Sul",
    "PE": "Pernambuco",
    "CE": "Ceará",
    "DF": "Distrito Federal",
    "SC": "Santa Catarina",
    "GO": "Goiás",
    "PA": "Pará",
    "AM": "Amazonas",
    "ES": "Espírito Santo",
    "MT": "Mato Grosso",
    "MS": "Mato Grosso do Sul",
    "RN": "Rio Grande do Norte",
    "PB": "Paraíba",
    "AL": "Alagoas",
    "SE": "Sergipe",
}


def _cpf_is_valid(value: str) -> bool:
    digits = [int(ch) for ch in value if ch.isdigit()]
    if len(digits) != 11 or all(digit == digits[0] for digit in digits):
        return False
    first_sum = sum(digit * (10 - idx) for idx, digit in enumerate(digits[:9]))
    first = 0 if first_sum % 11 < 2 else 11 - (first_sum % 11)
    second_sum = sum(digit * (11 - idx) for idx, digit in enumerate(digits[:10]))
    second = 0 if second_sum % 11 < 2 else 11 - (second_sum % 11)
    return digits[9:] == [first, second]


def _luhn_is_valid(value: str) -> bool:
    digits = [int(ch) for ch in value if ch.isdigit()]
    total = 0
    alternate = False
    for digit in reversed(digits):
        if alternate:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
        alternate = not alternate
    return total % 10 == 0


def test_generate_user_uses_brazil_profile_shape() -> None:
    user = models.generate_user("+5591980133818")

    assert user.phone_country_code == "+55"
    assert user.phone_local == "91980133818"
    domains = "|".join(re.escape(domain) for domain in _BR_EMAIL_DOMAINS)
    assert re.fullmatch(
        rf"{user.first_name.lower()}\.{user.last_name.lower()}\d{{2,4}}@(?:{domains})",
        user.email,
    )
    assert re.fullmatch(r"\d{2}/\d{2}/\d{4}", user.dob)
    day, month, year = (int(part) for part in user.dob.split("/"))
    assert 1 <= day <= 31
    assert 1 <= month <= 12
    assert 1970 <= year <= 2000
    assert _cpf_is_valid(user.cpf)
    assert 8 <= len(user.password) <= 20
    assert any(ch in "0123456789!@#$%&*" for ch in user.password)


def test_generate_card_uses_oaipay_bins_and_preserves_paypal_product_class() -> None:
    current_year = time.localtime().tm_year

    for _ in range(50):
        card = models.generate_card(proxy_url="http://ignored.invalid:8080")
        assert card.number.startswith(("414709", "516292"))
        assert len(card.number) == 16
        assert _luhn_is_valid(card.number)
        assert card.card_type == "CREDIT"
        assert re.fullmatch(r"\d{2}/\d{4}", card.expiry)
        month, year = (int(part) for part in card.expiry.split("/"))
        assert 1 <= month <= 12
        assert current_year + 2 <= year <= current_year + 5
        assert re.fullmatch(r"\d{3}", card.cvv)
        assert 100 <= int(card.cvv) <= 999


def test_generate_address_uses_oaipay_location_and_street_pools() -> None:
    address = models.generate_address()

    assert address.country == "BR"
    assert address.state in _BR_STATE_NAMES
    assert address.city
    assert re.fullmatch(r"\d{5}-\d{3}", address.postal_code)
    assert address.street
    assert address.district
    assert address.house_number.isdigit()
    assert 12 <= int(address.house_number) <= 4899
    assert address.district != _BR_STATE_NAMES[address.state]
