#!/usr/bin/env python3
"""
Global-K Chart 뉴스 수집 + 감성/키워드 분석 스크립트
매일 GitHub Actions에서 자동 실행됩니다.

필요 환경변수:
  ANTHROPIC_API_KEY — Claude API 키 (GitHub Secrets에 등록)
"""

import os
import json
import time
import datetime
import hashlib
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

# ── 설정 ────────────────────────────────────────────────────────────────────
SEARCH_QUERIES = [
    "글로벌 K차트",
    "Global-K Chart",
    "멜론 글로벌차트",
    "Global K Chart melon",
    "멜론 텐센트 라인뮤직 차트",
]

DATA_PATH = Path(__file__).parent.parent / "data" / "analysis.json"
MAX_ARTICLES_PER_QUERY = 10     # RSS 쿼리당 최대 수집 기사
MAX_ARTICLES_TO_ANALYZE = 30    # Claude API로 분석할 최대 기사 수
DAYS_TO_KEEP = 30               # 보관할 기사 최대 일수


# ── RSS 수집 ─────────────────────────────────────────────────────────────────
def fetch_google_news_rss(query: str, lang: str = "ko") -> list[dict]:
    """Google News RSS에서 기사 수집"""
    encoded = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl={lang}&gl=KR&ceid=KR:{lang}"
    articles = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read()
        root = ET.fromstring(content)
        channel = root.find("channel")
        if channel is None:
            return articles
        for item in channel.findall("item")[:MAX_ARTICLES_PER_QUERY]:
            title = item.findtext("title", "").strip()
            link  = item.findtext("link", "").strip()
            pub   = item.findtext("pubDate", "").strip()
            desc  = item.findtext("description", "").strip()
            # 중복 방지용 해시
            uid = hashlib.md5(link.encode()).hexdigest()[:10]
            articles.append({
                "uid": uid,
                "title": title,
                "url": link,
                "published": pub,
                "snippet": desc[:300],
                "source": "google_news",
                "query": query,
            })
    except Exception as e:
        print(f"  [RSS 오류] {query}: {e}")
    return articles


def fetch_naver_news_rss(query: str) -> list[dict]:
    """네이버 뉴스 RSS 수집 (공개 RSS 엔드포인트)"""
    encoded = urllib.parse.quote(query)
    url = f"https://news.naver.com/search/rss.nhn?query={encoded}&sort=1"
    articles = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read()
        root = ET.fromstring(content)
        channel = root.find("channel")
        if channel is None:
            return articles
        for item in channel.findall("item")[:MAX_ARTICLES_PER_QUERY]:
            title = item.findtext("title", "").replace("<b>","").replace("</b>","").strip()
            link  = item.findtext("link", "").strip()
            pub   = item.findtext("pubDate", "").strip()
            desc  = item.findtext("description", "").replace("<b>","").replace("</b>","").strip()
            uid = hashlib.md5(link.encode()).hexdigest()[:10]
            articles.append({
                "uid": uid,
                "title": title,
                "url": link,
                "published": pub,
                "snippet": desc[:300],
                "source": "naver_news",
                "query": query,
            })
    except Exception as e:
        print(f"  [네이버RSS 오류] {query}: {e}")
    return articles


def collect_all_articles() -> list[dict]:
    """전체 쿼리 순회하며 기사 수집 + 중복 제거"""
    seen_uids = set()
    all_articles = []
    for query in SEARCH_QUERIES:
        print(f"  수집 중: {query}")
        for article in fetch_google_news_rss(query):
            if article["uid"] not in seen_uids:
                seen_uids.add(article["uid"])
                all_articles.append(article)
        for article in fetch_naver_news_rss(query):
            if article["uid"] not in seen_uids:
                seen_uids.add(article["uid"])
                all_articles.append(article)
        time.sleep(0.5)
    print(f"  총 수집: {len(all_articles)}건 (중복 제거 후)")
    return all_articles


# ── Claude API 분석 ──────────────────────────────────────────────────────────
def call_claude(prompt: str) -> str:
    """Claude API 호출 (stdlib만 사용)"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY 환경변수가 없습니다")

    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    return data["content"][0]["text"]


def analyze_articles(articles: list[dict]) -> dict:
    """기사 목록을 Claude에 넘겨 감성 + 키워드 분석"""
    to_analyze = articles[:MAX_ARTICLES_TO_ANALYZE]
    if not to_analyze:
        return {"articles_analyzed": [], "keywords": [], "summary": {}}

    articles_text = "\n\n".join(
        f"[{i+1}] 제목: {a['title']}\n출처: {a['source']} | 날짜: {a['published']}\n내용: {a['snippet']}"
        for i, a in enumerate(to_analyze)
    )

    prompt = f"""당신은 K팝 음악 서비스 산업 분석가입니다.
아래는 "Global-K Chart(글로벌 K차트)" 관련 최신 뉴스/기사들입니다.

{articles_text}

다음 형식으로 JSON만 반환하세요 (마크다운 코드블록 없이 순수 JSON):

{{
  "articles": [
    {{
      "idx": 1,
      "sentiment": "positive" | "neutral" | "negative",
      "sentiment_score": 0.0 ~ 1.0,
      "sentiment_reason": "한 줄 이유 (한국어)",
      "key_topics": ["토픽1", "토픽2"]
    }}
  ],
  "top_keywords": [
    {{"keyword": "단어", "count": 숫자, "sentiment_bias": "positive"|"neutral"|"negative"}}
  ],
  "overall_summary": {{
    "positive_count": 숫자,
    "neutral_count": 숫자,
    "negative_count": 숫자,
    "main_positive_themes": ["테마1", "테마2"],
    "main_negative_themes": ["테마1", "테마2"],
    "key_insight": "전체를 관통하는 핵심 인사이트 2~3문장 (한국어)"
  }}
}}"""

    print("  Claude API 분석 요청 중...")
    raw = call_claude(prompt)

    # JSON 파싱
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        # 마크다운 펜스가 끼어있을 경우 제거
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[1:])
        if cleaned.endswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[:-1])
        result = json.loads(cleaned)

    # 원본 기사 정보 병합
    merged_articles = []
    for art_analysis in result.get("articles", []):
        idx = art_analysis.get("idx", 1) - 1
        if 0 <= idx < len(to_analyze):
            original = to_analyze[idx]
            merged_articles.append({
                **original,
                "sentiment": art_analysis.get("sentiment", "neutral"),
                "sentiment_score": art_analysis.get("sentiment_score", 0.5),
                "sentiment_reason": art_analysis.get("sentiment_reason", ""),
                "key_topics": art_analysis.get("key_topics", []),
            })

    return {
        "articles_analyzed": merged_articles,
        "keywords": result.get("top_keywords", []),
        "overall_summary": result.get("overall_summary", {}),
    }


# ── 히스토리 누적 ────────────────────────────────────────────────────────────
def load_existing_data() -> dict:
    if DATA_PATH.exists():
        try:
            return json.loads(DATA_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"articles": [], "sentiment_trend": []}


def merge_data(existing: dict, new_articles: list[dict]) -> list[dict]:
    """기존 기사 + 신규 기사 병합, DAYS_TO_KEEP일 초과분 제거"""
    seen = {a["uid"] for a in new_articles}
    old = [a for a in existing.get("articles", []) if a["uid"] not in seen]
    merged = new_articles + old

    # 날짜 기준 정렬 후 오래된 것 제거
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=DAYS_TO_KEEP)
    def is_recent(article):
        try:
            # pubDate 파싱 시도
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(article.get("published", ""))
            return dt > cutoff
        except Exception:
            return True  # 파싱 실패 시 보관

    return [a for a in merged if is_recent(a)]


def build_sentiment_trend(articles: list[dict]) -> list[dict]:
    """날짜별 감성 집계 (최근 14일)"""
    by_date: dict[str, dict] = {}
    for a in articles:
        if "sentiment" not in a:
            continue
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(a.get("published", ""))
            date_str = dt.strftime("%m/%d")
        except Exception:
            continue
        if date_str not in by_date:
            by_date[date_str] = {"date": date_str, "positive": 0, "neutral": 0, "negative": 0, "total": 0}
        by_date[date_str][a["sentiment"]] += 1
        by_date[date_str]["total"] += 1

    # 최근 14일만 반환
    return sorted(by_date.values(), key=lambda x: x["date"])[-14:]


# ── 메인 ─────────────────────────────────────────────────────────────────────
def main():
    now_kst = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    print(f"\n=== Global-K Chart 분석 시작 [{now_kst.strftime('%Y-%m-%d %H:%M KST')}] ===")

    # 1. 기사 수집
    print("\n[1] RSS 뉴스 수집")
    new_articles = collect_all_articles()

    # 2. Claude 분석
    print("\n[2] Claude API 감성/키워드 분석")
    analysis = analyze_articles(new_articles)

    analyzed_new = analysis.get("articles_analyzed", [])
    keywords = analysis.get("keywords", [])
    overall = analysis.get("overall_summary", {})

    # 3. 기존 데이터와 병합
    print("\n[3] 데이터 병합 및 저장")
    existing = load_existing_data()
    all_articles = merge_data(existing, analyzed_new)

    # 4. 감성 집계
    pos = sum(1 for a in all_articles if a.get("sentiment") == "positive")
    neu = sum(1 for a in all_articles if a.get("sentiment") == "neutral")
    neg = sum(1 for a in all_articles if a.get("sentiment") == "negative")
    total = max(pos + neu + neg, 1)

    sentiment_trend = build_sentiment_trend(all_articles)

    # 5. JSON 저장
    output = {
        "last_updated": now_kst.isoformat(),
        "summary": {
            "total_articles": len(all_articles),
            "positive": pos,
            "neutral": neu,
            "negative": neg,
            "positive_pct": round(pos / total * 100),
            "neutral_pct": round(neu / total * 100),
            "negative_pct": round(neg / total * 100),
        },
        "keywords": keywords[:30],
        "articles": all_articles[:100],   # 최신 100건만 저장
        "sentiment_trend": sentiment_trend,
        "key_insight": overall.get("key_insight", ""),
        "main_positive_themes": overall.get("main_positive_themes", []),
        "main_negative_themes": overall.get("main_negative_themes", []),
    }

    DATA_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  저장 완료 → {DATA_PATH}")
    print(f"  수집: {len(all_articles)}건 | 긍정 {pos} / 중립 {neu} / 부정 {neg}")
    print(f"  키워드: {len(keywords)}개")
    print("\n=== 완료 ===\n")


if __name__ == "__main__":
    main()
