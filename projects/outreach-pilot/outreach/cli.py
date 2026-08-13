"""CLI 진입점.

사용법:
    python -m outreach research --profile profiles/example.yaml
    python -m outreach export --csv leads.csv
"""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

DEFAULT_DB = "data/leads.db"


def _cmd_research(args: argparse.Namespace) -> int:
    from outreach.core.store import Store
    from outreach.research.engine import ResearchConfig, ResearchEngine
    from outreach.research.profile import load_profile

    profile = load_profile(args.profile)
    config = ResearchConfig(max_searches_per_category=args.max_searches)
    engine = ResearchEngine(config=config)

    print(f"[research] 상품: {profile.product} / 카테고리: {', '.join(profile.categories)}")
    new_count = updated_count = 0
    with Store(args.db) as store:
        for category in profile.categories:
            leads = engine.research_category(profile, category, max_leads=args.max_leads)
            print(f"  - {category}: {len(leads)}건 수집")
            for lead in leads:
                _, created = store.upsert(lead)
                if created:
                    new_count += 1
                else:
                    updated_count += 1
        total = len(store.list_leads(product=profile.product))
    print(f"[research] 완료 — 신규 {new_count}건, 갱신 {updated_count}건 (해당 상품 누적 {total}건)")
    print(f"[research] DB: {args.db}")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    from outreach.core.export import export_csv
    from outreach.core.store import Store

    with Store(args.db) as store:
        leads = store.list_leads(product=args.product, status=args.status)
    count = export_csv(leads, args.csv)
    print(f"[export] {count}건 → {args.csv}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="outreach", description="영업/마케팅 아웃리치 자동화 베타"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_research = sub.add_parser("research", help="상품 프로필로 잠재 파트너 수집")
    p_research.add_argument("--profile", required=True, help="상품 프로필 YAML 경로")
    p_research.add_argument("--db", default=DEFAULT_DB, help=f"SQLite 경로 (기본 {DEFAULT_DB})")
    p_research.add_argument("--max-leads", type=int, default=10,
                            help="카테고리당 최대 수집 건수 (기본 10)")
    p_research.add_argument("--max-searches", type=int, default=5,
                            help="카테고리당 웹서치 상한 (기본 5)")
    p_research.set_defaults(func=_cmd_research)

    p_export = sub.add_parser("export", help="리드를 CSV 로 내보내기")
    p_export.add_argument("--csv", required=True, help="출력 CSV 경로")
    p_export.add_argument("--db", default=DEFAULT_DB, help=f"SQLite 경로 (기본 {DEFAULT_DB})")
    p_export.add_argument("--product", default=None, help="특정 상품만 (기본 전체)")
    p_export.add_argument("--status", default=None, help="특정 상태만 (기본 전체)")
    p_export.set_defaults(func=_cmd_export)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows cp949 콘솔에서 모델 출력(한자·이모지 등)로 print 가 죽지 않게
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    load_dotenv()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
