from dataclasses import dataclass, field
from typing import Optional
import httpx
import random
import string
import time
import uuid
from paypal.traffic_recorder import get_global_traffic_recorder


@dataclass
class UserInfo:
    first_name: str
    last_name: str
    email: str
    phone: str
    phone_local: str
    phone_country_code: str
    password: str
    dob: str  # DD/MM/YYYY
    cpf: str  # XXX.XXX.XXX-XX


@dataclass
class CardInfo:
    number: str
    expiry: str  # MM/YYYY
    cvv: str
    card_type: str = "CREDIT"


@dataclass
class BillingAddress:
    street: str
    house_number: str
    district: str
    city: str
    state: str
    postal_code: str
    country: str = "BR"


@dataclass
class SessionState:
    ba_token: str = ""
    ec_token: str = ""
    ssrt: str = ""
    ctx_id: str = ""
    nsid: str = ""
    d_id: str = ""
    user_id: str = ""
    datadome_cookie: str = ""
    datadome_clientid: str = ""
    tltsid: str = ""
    tltdid: str = ""
    tealeaf_serial_number: int = 0
    tealeaf_page_id: str = field(default_factory=lambda: f"P.{uuid.uuid4().hex[:24].upper()}")
    tealeaf_tab_id: str = field(default_factory=lambda: f"Y{random.randint(100, 999)}")
    tealeaf_start_time_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    paypal_client_metadata_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    euat_token: str = ""
    return_url: str = ""
    content_hash: str = ""
    content_identifier: str = ""
    content_manifest_url: str = ""
    content_manifest_key: str = ""
    signup_url: str = ""
    paypal_captcha_solved: bool = False
    show_create_account_action_id: str = ""
    create_user_action_id: str = ""
    submit_public_credential_action_id: str = ""
    fetch_device_fingerprint_action_id: str = ""
    modxo_country_action_id: str = ""
    modxo_country_action_bound: str = ""
    modxo_country_selected: bool = False
    modxo_pay_page_url: str = ""
    passkey_challenge: str = ""
    rp_id: str = ""
    login_phone_country_code: str = ""
    modxo_deployment_id: str = ""
    signup_fallback_reason: str = ""
    mtr_channel: str = ""
    mtr_client_metadata_id: str = ""
    mtr_api_key: str = ""
    mtr_is_qa: bool = False
    mtr_dfp_script_url: str = ""
    mtr_get_status: int = 0
    mtr_post_status: int = 0
    mtr_request_id: str = ""
    mtr_sealed_result: str = ""
    mtr_runtime_source: str = ""
    mtr_visitor_token: str = ""
    mtr_completed: bool = False
    mtr_completed_cmid: str = ""
    mtr_browser_result: dict = field(default_factory=dict)
    captcha_synthetic_used: bool = False
    datadome_header_injected: bool = False
    fingerprint_source: str = ""
    roxy_browser: dict = field(default_factory=dict)
    datadome_browser_solved: bool = False
    datadome_browser_result: dict = field(default_factory=dict)
    risk_signals_runtime_source: str = ""
    risk_signals_browser_result: dict = field(default_factory=dict)
    browser_profile: dict = field(default_factory=dict)
    screen: dict = field(default_factory=dict)
    viewport: dict = field(default_factory=dict)
    device_fingerprint: dict = field(default_factory=dict)
    pxp_guid: str = field(default_factory=lambda: uuid.uuid4().hex)
    page_start_time_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    fpti_calc: str = field(default_factory=lambda: uuid.uuid4().hex[:13])
    datadog_session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    datadog_view_ids: dict = field(default_factory=dict)

    def update_from_cookies(self, cookies: dict):
        if "nsid" in cookies:
            self.nsid = cookies["nsid"]
        if "d_id" in cookies:
            self.d_id = cookies["d_id"]
        if "datadome" in cookies:
            self.datadome_cookie = cookies["datadome"]
        if "TLTSID" in cookies:
            self.tltsid = cookies["TLTSID"]
        if "TLTDID" in cookies:
            self.tltdid = cookies["TLTDID"]
        euat_key = "AV894Kt2TSumQQrJwe-8mzmyREO"
        if euat_key in cookies:
            self.euat_token = cookies[euat_key]


def generate_random_email() -> str:
    chars = string.ascii_lowercase + string.digits
    user = "".join(random.choice(chars) for _ in range(12))
    return f"{user}@gmail.com"


def generate_eteid() -> list:
    return [
        random.randint(-10000000000, 20000000000),
        random.randint(-10000000000, 20000000000),
        random.randint(-10000000000, 20000000000),
        random.randint(-10000000000, 20000000000),
        random.randint(-10000000000, 20000000000),
        random.randint(-10000000000, 20000000000),
        None,
        None,
    ]


# --- Random generators for Brazil ---

_BR_FIRST_NAMES = [
    "Lucas", "Gabriel", "Miguel", "Arthur", "Matheus", "Pedro", "Rafael",
    "Gustavo", "Felipe", "Bernardo", "Henrique", "Daniel", "Leonardo",
    "Ana", "Maria", "Julia", "Beatriz", "Larissa", "Fernanda", "Camila",
    "Leticia", "Amanda", "Carolina", "Bruna", "Mariana", "Isabela",
]

_BR_LAST_NAMES = [
    "Silva", "Santos", "Oliveira", "Souza", "Rodrigues", "Ferreira",
    "Almeida", "Nascimento", "Lima", "Araujo", "Pereira", "Carvalho",
    "Ribeiro", "Gomes", "Martins", "Costa", "Barbosa", "Moreira",
    "Mendes", "Cardoso", "Teixeira", "Vieira", "Correia", "Nunes",
]

_BR_STREETS = [
    "Rua das Flores", "Rua da Paz", "Rua São Paulo", "Rua dos Andradas",
    "Rua Voluntários da Pátria", "Rua General Osório", "Rua Sete de Setembro",
    "Rua Marechal Deodoro", "Rua Quinze de Novembro", "Rua Dom Pedro II",
    "Avenida Brasil", "Avenida Independência", "Avenida Getúlio Vargas",
    "Rua Tiradentes", "Rua Santos Dumont", "Rua Bento Gonçalves",
]

_BR_DISTRICTS = [
    "Centro", "Centro Histórico", "Bela Vista", "Jardim América",
    "Vila Mariana", "Pinheiros", "Consolação", "Liberdade",
    "Santa Cecília", "Moema", "Itaim Bibi", "Perdizes",
]

_KNOWN_BR_ADDRESSES = [
    ("Avenida Cristóvão Colombo", "287", "Savassi", "Belo Horizonte", "MG", "30140-140"),
    ("Rua Siqueira Campos", "1001", "Centro Histórico", "Porto Alegre", "RS", "90010-001"),
    ("Avenida Paulista", "1000", "Bela Vista", "São Paulo", "SP", "01310-100"),
    ("Rua da Assembléia", "10", "Centro", "Rio de Janeiro", "RJ", "20011-901"),
    ("Rua XV de Novembro", "100", "Centro", "Curitiba", "PR", "80020-310"),
    ("Avenida Sete de Setembro", "1555", "Vitória", "Salvador", "BA", "40080-002"),
    ("Avenida Doutor Antônio Gouveia", "4662", "Pajuçara", "Maceió", "AL", "57030-170"),
    ("Avenida Fernandes Lima", "1497", "Farol", "Maceió", "AL", "57055-000"),
]


def _luhn_checksum(partial: str) -> int:
    """Calculate the Luhn check digit for a partial card number (without the check digit)."""
    digits = [int(d) for d in partial]
    # Process from right to left: double every other digit starting from the rightmost
    for i in range(len(digits) - 1, -1, -2):
        digits[i] *= 2
        if digits[i] > 9:
            digits[i] -= 9
    total = sum(digits)
    return (10 - (total % 10)) % 10


SUIJIDAQUAN_CARD_API = "https://api2.suijidaquan.com/api/v2/random-credit-card"
SUIJIDAQUAN_CARD_REFERER = "https://www.suijidaquan.com/credit-card-generator"


def _normalize_suijidaquan_card_type(value: str) -> str:
    normalized = (value or "").strip().lower().replace(" ", "")
    if normalized == "visa":
        return "VISA"
    if normalized in {"mastercard", "master"}:
        return "MASTER_CARD"
    return ""


def _fetch_suijidaquan_card(count: int = 20, proxy_url: str | None = None) -> Optional[CardInfo]:
    """Fetch one Visa/MasterCard entry from suijidaquan's public generator API."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Origin": "https://www.suijidaquan.com",
        "Referer": SUIJIDAQUAN_CARD_REFERER,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
    }
    payload = {"count": count, "method": "random_credit_card"}

    client_kwargs = {
        "timeout": httpx.Timeout(15.0),
        "headers": headers,
        "trust_env": False,
    }
    if proxy_url:
        client_kwargs["proxy"] = proxy_url

    with httpx.Client(**client_kwargs) as client:
        recorder = get_global_traffic_recorder()
        req_id = None
        if recorder is not None:
            req_id = recorder.record_request(
                "POST",
                SUIJIDAQUAN_CARD_API,
                {"json": payload},
                headers=headers,
                note="generate_card",
            )
        try:
            resp = client.post(SUIJIDAQUAN_CARD_API, json=payload)
            if recorder is not None and req_id is not None:
                recorder.record_response(req_id, "POST", SUIJIDAQUAN_CARD_API, resp)
        except Exception as exc:
            if recorder is not None and req_id is not None:
                recorder.record_response(
                    req_id,
                    "POST",
                    SUIJIDAQUAN_CARD_API,
                    None,
                    error=str(exc),
                )
            raise
        resp.raise_for_status()
        result = resp.json()

    if result.get("status") != "ok" or not isinstance(result.get("data"), list):
        raise RuntimeError(f"Unexpected suijidaquan response: {result!r}")

    candidates: list[CardInfo] = []
    for item in result["data"]:
        if not isinstance(item, dict):
            continue
        issuer = _normalize_suijidaquan_card_type(item.get("Credit_Card_Type", ""))
        if issuer not in {"VISA", "MASTER_CARD"}:
            continue
        number = "".join(ch for ch in str(item.get("Credit_Card_Number", "")) if ch.isdigit())
        expiry = str(item.get("Expires", "")).strip()
        cvv = "".join(ch for ch in str(item.get("CVV2", "")) if ch.isdigit())
        if len(number) < 13 or "/" not in expiry or len(cvv) < 3:
            continue
        candidates.append(CardInfo(number=number, expiry=expiry, cvv=cvv[:4], card_type="CREDIT"))

    if not candidates:
        raise RuntimeError("suijidaquan returned no Visa/MasterCard candidates")
    return random.choice(candidates)


# Local fallback only if suijidaquan is unavailable.
_FALLBACK_CARD_PREFIXES = [
    ("4", "VISA"),
    ("51", "MASTER_CARD"),
    ("52", "MASTER_CARD"),
    ("53", "MASTER_CARD"),
    ("54", "MASTER_CARD"),
    ("55", "MASTER_CARD"),
]


def generate_card(proxy_url: str | None = None) -> CardInfo:
    for _ in range(3):
        try:
            card = _fetch_suijidaquan_card(proxy_url=proxy_url)
            if card:
                return card
        except Exception:
            pass

    prefix, _issuer = random.choice(_FALLBACK_CARD_PREFIXES)
    remaining = 16 - len(prefix) - 1
    body = prefix + "".join(str(random.randint(0, 9)) for _ in range(remaining))
    check = _luhn_checksum(body)
    number = body + str(check)

    month = random.randint(1, 12)
    year = random.randint(2027, 2031)
    expiry = f"{month:02d}/{year}"

    cvv = f"{random.randint(0, 999):03d}"

    return CardInfo(number=number, expiry=expiry, cvv=cvv, card_type="CREDIT")


def generate_cpf() -> str:
    digits = [random.randint(0, 9) for _ in range(9)]

    # first check digit
    s = sum(d * w for d, w in zip(digits, range(10, 1, -1)))
    r = s % 11
    d1 = 0 if r < 2 else 11 - r
    digits.append(d1)

    # second check digit
    s = sum(d * w for d, w in zip(digits, range(11, 1, -1)))
    r = s % 11
    d2 = 0 if r < 2 else 11 - r
    digits.append(d2)

    d = digits
    return f"{d[0]}{d[1]}{d[2]}.{d[3]}{d[4]}{d[5]}.{d[6]}{d[7]}{d[8]}-{d[9]}{d[10]}"


def generate_dob() -> str:
    day = random.randint(1, 28)
    month = random.randint(1, 12)
    year = random.randint(1980, 2000)
    return f"{day:02d}/{month:02d}/{year}"


def generate_password() -> str:
    lower = string.ascii_lowercase
    upper = string.ascii_uppercase
    digits_chars = string.digits
    symbols = "!@#$%^"
    pwd = [
        random.choice(lower) for _ in range(6)
    ] + [
        random.choice(upper) for _ in range(3)
    ] + [
        random.choice(digits_chars) for _ in range(3)
    ] + [
        random.choice(symbols) for _ in range(2)
    ]
    random.shuffle(pwd)
    return "".join(pwd)


def generate_user(phone: str) -> UserInfo:
    first = random.choice(_BR_FIRST_NAMES)
    last = random.choice(_BR_LAST_NAMES)

    phone_local = phone.lstrip("+")
    phone_country_code = "+55"
    if phone_local.startswith("55"):
        phone_local = phone_local[2:]

    return UserInfo(
        first_name=first,
        last_name=last,
        email=generate_random_email(),
        phone=phone,
        phone_local=phone_local,
        phone_country_code=phone_country_code,
        password=generate_password(),
        dob=generate_dob(),
        cpf=generate_cpf(),
    )


def generate_address() -> BillingAddress:
    street, house_number, district, city, state, postal_code = random.choice(_KNOWN_BR_ADDRESSES)

    return BillingAddress(
        street=street,
        house_number=house_number,
        district=district,
        city=city,
        state=state,
        postal_code=postal_code,
        country="BR",
    )
