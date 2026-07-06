#!/usr/bin/env python3
"""PayPal Billing Agreement approval automation.

Usage:
    python main.py --ba-token BA-xxx --phone +5591980133818
"""
import argparse
import json
import os
import sys
from pathlib import Path
from loguru import logger

from paypal.models import generate_user, generate_card, generate_address
from paypal.flow import PayPalFlow
from paypal.proxy import build_proxy_config
from paypal.session import sanitize_for_log
from paypal.traffic_recorder import close_global_traffic_recorder, reset_global_traffic_recorder


def main():
    parser = argparse.ArgumentParser(
        description="PayPal Billing Agreement Approval Automation"
    )
    parser.add_argument(
        "--ba-token", required=True,
        help="Billing Agreement token (e.g. BA-3AX328361P111131W)"
    )
    parser.add_argument(
        "--phone", required=True,
        help="Phone number with country code (e.g. +5591980133818)"
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Enable debug logging"
    )
    parser.add_argument(
        "--max-card-attempts",
        type=int,
        default=5,
        help="Max SignUpNewMember retries with fresh generated Visa/MasterCard when addCard fails",
    )
    parser.add_argument(
        "--max-flow-attempts",
        type=int,
        default=3,
        help="Max full-flow retries when authorization ends with BUYER_NOT_SET",
    )
    parser.add_argument(
        "--max-authorize-attempts",
        type=int,
        default=3,
        help="Max authorize retries after reloading Hermes/Hagrid review context",
    )
    parser.add_argument(
        "--card-retry-delay",
        type=float,
        default=6.0,
        help="Seconds to wait before generating/submitting the next card after a card rejection",
    )
    parser.add_argument(
        "--card-retry-jitter",
        type=float,
        default=2.0,
        help="Extra random seconds added to card retry delay",
    )
    proxy_group = parser.add_mutually_exclusive_group()
    proxy_group.add_argument(
        "--proxy",
        dest="proxy_enabled",
        action="store_true",
        default=None,
        help="Enable configured 1024proxy outbound proxy for this run",
    )
    proxy_group.add_argument(
        "--no-proxy",
        dest="proxy_enabled",
        action="store_false",
        help="Disable outbound proxy for this run",
    )
    parser.add_argument(
        "--proxy-index",
        type=int,
        default=None,
        help="Use a specific configured proxy index (0-based). Default: random when proxy is enabled",
    )
    parser.add_argument(
        "--proxy-url",
        default=None,
        help="Use a custom/chained proxy URL or host:port:user:pass line for this run",
    )
    parser.add_argument(
        "--record-traffic",
        action="store_true",
        help="Test mode: record all program-side outbound requests/responses for offline diffing",
    )
    parser.add_argument(
        "--traffic-dir",
        default=None,
        help="Output directory for --record-traffic. Default: captures/program-paypal-YYYYMMDD-HHMMSS",
    )
    parser.add_argument(
        "--compare-roxy-capture",
        default=None,
        help="After --record-traffic run, compare program traffic with this Roxy capture dir",
    )
    parser.add_argument(
        "--fingerprint-source",
        choices=["random", "program", "python", "synthetic", "roxy", "browser", "auto"],
        default=None,
        help="Browser fingerprint source: random/program Python generator, roxy RoxyBrowser runtime, or auto",
    )
    parser.add_argument(
        "--datadome-mode",
        choices=["protocol", "edge", "roxy", "browser", "auto", "off"],
        default=None,
        help="DataDome mode: protocol edge simulation, roxy real browser runtime, auto, or off",
    )
    parser.add_argument(
        "--mtr-runtime",
        choices=["python_generated", "python", "protocol", "roxy", "browser", "auto", "block", "off"],
        default=None,
        help="MTR sealedResult source: python_generated protocol template, roxy browser runtime, auto, block, or off",
    )
    parser.add_argument(
        "--risk-signals-mode",
        choices=["protocol", "python", "synthetic", "template", "roxy", "browser", "auto", "off"],
        default=None,
        help="Phase1 risk signal source: protocol templates, roxy browser runtime, auto, or off",
    )

    args = parser.parse_args()

    logger.remove()
    if args.debug:
        logger.add(sys.stderr, level="DEBUG")
    else:
        logger.add(sys.stderr, level="INFO")
    if args.datadome_mode:
        os.environ["PAYPAL_DATADOME_MODE"] = args.datadome_mode
    if args.mtr_runtime:
        os.environ["PAYPAL_MTR_RUNTIME"] = args.mtr_runtime
    if args.risk_signals_mode:
        os.environ["PAYPAL_RISK_SIGNALS_MODE"] = args.risk_signals_mode

    traffic_recorder = None
    if args.record_traffic or args.traffic_dir or args.compare_roxy_capture:
        os.environ["PAYPAL_TRAFFIC_RECORD"] = "1"
        traffic_recorder = reset_global_traffic_recorder(args.traffic_dir)
        logger.info("Program traffic recording enabled: {}", traffic_recorder.root)

    proxy_config = build_proxy_config(
        enabled=args.proxy_enabled,
        index=args.proxy_index,
        proxy_url=args.proxy_url,
    )

    user = generate_user(args.phone)
    card = generate_card(proxy_url=proxy_config.url)
    address = generate_address()

    logger.info(f"User: {user.first_name} {user.last_name}")
    logger.info("Email: {}", sanitize_for_log({"email": user.email})["email"])
    logger.info("Phone: {}", sanitize_for_log({"phone": user.phone})["phone"])
    logger.info("CPF: <redacted>")
    logger.info("DOB: <redacted>")
    logger.info(
        "Card: {} exp={} cvv=<redacted>",
        sanitize_for_log({"cardNumber": card.number})["cardNumber"],
        card.expiry,
    )
    logger.info("Address generated: {}, {}-{}", address.district, address.city, address.state)
    logger.info(f"Proxy: {proxy_config.label}")

    flow = PayPalFlow(
        ba_token=args.ba_token,
        user=user,
        card=card,
        address=address,
        max_card_attempts=args.max_card_attempts,
        max_flow_attempts=args.max_flow_attempts,
        max_authorize_attempts=args.max_authorize_attempts,
        card_retry_delay_seconds=args.card_retry_delay,
        card_retry_jitter_seconds=args.card_retry_jitter,
        proxy_config=proxy_config,
        fingerprint_source=args.fingerprint_source,
        datadome_mode=args.datadome_mode,
        mtr_runtime=args.mtr_runtime,
        risk_signals_mode=args.risk_signals_mode,
    )

    try:
        result = flow.run()
    finally:
        close_global_traffic_recorder()

    if args.compare_roxy_capture and traffic_recorder is not None:
        try:
            from tools.compare_paypal_traffic import compare, write_markdown

            report = compare(
                traffic_recorder.root,
                Path(args.compare_roxy_capture).expanduser().resolve(),
            )
            report_path = traffic_recorder.root / "traffic_diff_report.json"
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            write_markdown(report, report_path.with_suffix(".md"))
            logger.info("Traffic diff report saved: {}", report_path)
            if report.get("findings"):
                logger.warning(
                    "Traffic diff findings: {}",
                    json.dumps(report.get("findings"), ensure_ascii=False, indent=2),
                )
        except Exception as exc:
            logger.warning("Traffic diff failed: {}", exc)

    print("\n" + "=" * 60)
    print("RESULT:")
    print(json.dumps(sanitize_for_log(result), indent=2, ensure_ascii=False))
    print("=" * 60)

    if result.get("status") == "success":
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
