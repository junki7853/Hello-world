"""명령줄 진입점: URL 리스트(csv 또는 인자) → 수집 → SQLite 저장.

사용 예:
    python -m crawler.cli --csv targets.csv
    python -m crawler.cli --url ctrip=https://m.ctrip.com/webapp/you/community/detail?articleId=266207894
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from crawler.adapters.base import Adapter
from crawler.adapters.ctrip import CtripAdapter
from crawler.core.browser import BrowserSession
from crawler.core.ratelimit import delay_range_from_env, polite_sleep
from crawler.core.store import Store

logger = logging.getLogger("crawler.cli")

DEFAULT_DB_PATH = "data/crawler.db"

# 플랫폼 이름 → 어댑터 클래스. Phase 2+ 에서 여기에 추가한다.
ADAPTER_REGISTRY: dict[str, type[Adapter]] = {
    "ctrip": CtripAdapter,
}


def load_targets_from_csv(path: str | Path) -> list[tuple[str, str]]:
    """(platform, url) 튜플 목록을 CSV 에서 읽는다. 헤더: platform,url."""
    targets: list[tuple[str, str]] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or "platform" not in reader.fieldnames or "url" not in reader.fieldnames:
            raise ValueError("CSV 헤더는 'platform,url' 이어야 합니다")
        for row in reader:
            platform = (row.get("platform") or "").strip()
            url = (row.get("url") or "").strip()
            if platform and url:
                targets.append((platform, url))
    return targets


def parse_inline_target(spec: str) -> tuple[str, str]:
    """'platform=url' 형태의 인자를 (platform, url) 로 파싱한다."""
    if "=" not in spec:
        raise ValueError(f"--url 은 'platform=URL' 형식이어야 합니다: {spec}")
    platform, url = spec.split("=", 1)
    platform, url = platform.strip(), url.strip()
    if not platform or not url:
        raise ValueError(f"--url 의 platform/URL 이 비어 있습니다: {spec}")
    return platform, url


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cn-crawler",
        description="중국 플랫폼 참여지표 크롤러 (Playwright 헤드리스)",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv", help="targets CSV 경로 (헤더: platform,url)")
    source.add_argument(
        "--url",
        action="append",
        metavar="PLATFORM=URL",
        help="인라인 타깃 (반복 가능)",
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("CRAWLER_DB") or DEFAULT_DB_PATH,
        help=f"SQLite DB 경로 (기본: {DEFAULT_DB_PATH})",
    )
    parser.add_argument("--headed", action="store_true", help="브라우저 창을 띄운다(디버깅용)")
    parser.add_argument("-v", "--verbose", action="store_true", help="디버그 로그")
    return parser


def resolve_targets(args: argparse.Namespace) -> list[tuple[str, str]]:
    if args.csv:
        return load_targets_from_csv(args.csv)
    return [parse_inline_target(spec) for spec in args.url]


def collect_targets(
    targets: list[tuple[str, str]], db_path: str, headless: bool
) -> int:
    """타깃들을 순차 수집해 저장한다. 저장된 행 수를 반환한다."""
    delay_low, delay_high = delay_range_from_env()
    saved = 0
    with Store(db_path) as store, BrowserSession(headless=headless) as session:
        for index, (platform, url) in enumerate(targets):
            adapter_cls = ADAPTER_REGISTRY.get(platform)
            if adapter_cls is None:
                logger.warning("지원하지 않는 플랫폼 건너뜀: %s (%s)", platform, url)
                continue
            # 첫 요청을 뺀 매 요청 앞에 지연을 둔다. 성공/실패와 무관하게 적용돼
            # 연속 실패 시에도 간격 없이 재요청해 차단이 악화되는 것을 막는다.
            if index > 0:
                polite_sleep(delay_low, delay_high)
            adapter = adapter_cls(session)
            try:
                metrics = adapter.collect(url)
            except Exception:
                logger.exception("수집 실패: %s", url)
                continue
            store.append(metrics)
            saved += 1
            logger.info(
                "저장 [%s] %s → likes=%s collects=%s comments=%s views=%s",
                platform,
                metrics.article_id,
                metrics.likes,
                metrics.collects,
                metrics.comments,
                metrics.views,
            )
    return saved


def main(argv: list[str] | None = None) -> int:
    load_dotenv()  # .env 의 쿠키/프록시/DB 설정을 환경변수로 로드
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        targets = resolve_targets(args)
    except (ValueError, FileNotFoundError) as exc:
        logger.error("%s", exc)
        return 2
    if not targets:
        logger.error("수집할 타깃이 없습니다")
        return 2
    saved = collect_targets(targets, db_path=args.db, headless=not args.headed)
    logger.info("완료: %d개 저장 (DB: %s)", saved, args.db)
    return 0 if saved > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
