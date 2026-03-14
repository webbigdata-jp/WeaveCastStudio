"""
crawler/drission_crawler.py

DrissionPage（ChromiumPage）を使って登録済みソースを巡回し、
スクリーンショット・HTML・テキストを収集してArticleStore用のdictを返す。

DrissionPageはasyncioではなく同期APIのため、
スケジューラからはThreadPoolExecutorで並列化する。
"""

import hashlib
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from DrissionPage import ChromiumPage, ChromiumOptions

logger = logging.getLogger(__name__)

# ページ巡回時に取得する関連記事の最大数
MAX_RELATED_PAGES = 5

# テキスト抽出の最大文字数
MAX_TEXT_LENGTH = 5000

# ニュース記事っぽいURLパスのキーワード
NEWS_PATH_KEYWORDS = [
    "/news/", "/press/", "/article/", "/story/",
    "/release/", "/update/", "/report/", "/blog/",
    "/statement/", "/media/", "/briefing/", "/analysis/",
    "/backgrounders/", "/transcripts/", "/speeches/",
]


def _make_chromium_options() -> ChromiumOptions:
    """ヘッドレスChromiumの起動オプションを生成する"""
    co = ChromiumOptions()
    co.headless()                              # ヘッドレスモード
    co.set_argument("--no-sandbox")
    co.set_argument("--disable-dev-shm-usage")
    co.set_argument("--disable-gpu")
    co.set_user_agent(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    return co


class DrissionCrawler:
    """DrissionPageを使って登録済みソースを巡回し、キャプチャとHTMLを保存する"""

    def __init__(self, output_dir: str = "data/crawl"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ──────────────────────────────────────────
    # 公開インターフェース
    # ──────────────────────────────────────────

    def crawl_source(self, source: dict) -> list[dict]:
        """
        1つのソースを巡回し、記事データのリストを返す。
        DrissionPageは同期APIのため、通常のスレッドで実行する。

        Args:
            source: sources.yamlの1エントリ（dict）
        Returns:
            list[dict]: 各記事のメタデータ
        """
        source_dir = self.output_dir / source["id"]
        source_dir.mkdir(exist_ok=True)

        logger.info(f"[CRAWL] Start: {source['name']} → {source['url']}")

        co = _make_chromium_options()
        page = ChromiumPage(co)

        # ウィンドウサイズ設定（headlessでも適用）
        page.set.window.size(1920, 1080)

        articles: list[dict] = []
        try:
            if source.get("type") == "social_official" and "x.com" in source["url"]:
                article = self._crawl_x_timeline(page, source, source_dir)
                if article:
                    articles.append(article)
            else:
                articles = self._crawl_standard(page, source, source_dir)
        except Exception as e:
            logger.error(
                f"[CRAWL] Fatal error for {source['id']}: {e}", exc_info=True
            )
        finally:
            page.quit()

        logger.info(
            f"[CRAWL] Done: {source['name']} → {len(articles)} articles"
        )
        return articles

    # ──────────────────────────────────────────
    # 標準巡回（政府・メディア・シンクタンク）
    # ──────────────────────────────────────────

    def _crawl_standard(
        self, page: ChromiumPage, source: dict, source_dir: Path
    ) -> list[dict]:
        """TOPページ + 関連記事ページを巡回する"""
        articles: list[dict] = []
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        # ── STEP A: TOPページにアクセス ──
        try:
            page.get(source["url"])
            # JS描画完了まで確実に待機（DrissionPageのpage.get()はDOMロード完了まで
            # 待つが、JS描画が終わっていない場合があるため明示的に追加）
            page.wait.doc_loaded()
            time.sleep(2)
        except Exception as e:
            logger.warning(f"[CRAWL] Failed to load top page: {e}")
            return articles

        # リダイレクト後の実際のURLを取得
        actual_url = page.url or source["url"]
        logger.info(f"[CRAWL] Loaded: {actual_url} (title: {page.title[:50] if page.title else 'N/A'})")

        # ── STEP B: キャプチャ + HTML保存 ──
        top_screenshot = source_dir / f"{ts}_top.png"
        top_html_path = source_dir / f"{ts}_top.html"

        try:
            page.get_screenshot(path=str(top_screenshot), left_top=(0, 0), right_bottom=(1920, 1080))
        except Exception as e:
            logger.warning(f"[CRAWL] Screenshot failed for top page: {e}")

        html_content = page.html
        top_html_path.write_text(html_content, encoding="utf-8")

        text = self._extract_text(page)
        title = page.title

        articles.append({
            "source_id": source["id"],
            "source_name": source["name"],
            "url": actual_url,  # リダイレクト後の実際のURLを保存
            "title": title or f"{source['name']} - Top Page",
            "screenshot_path": str(top_screenshot),
            "html_path": str(top_html_path),
            "text_content": text[:MAX_TEXT_LENGTH],
            "crawled_at": datetime.now(timezone.utc).isoformat(),
            "credibility": source["credibility"],
            "tier": source["tier"],
            "is_top_page": True,
        })

        # ── STEP C: 関連記事URLを抽出 ──
        related_urls = self._extract_article_links(html_content, source, actual_url)
        logger.info(
            f"[CRAWL] {source['id']}: found {len(related_urls)} related URLs"
        )

        # ── STEP D: 各関連記事ページを巡回 ──
        for idx, url in enumerate(related_urls[:MAX_RELATED_PAGES]):
            try:
                article = self._crawl_article_page(
                    page, url, source, source_dir, ts, idx
                )
                if article:
                    articles.append(article)
            except Exception as e:
                logger.warning(f"[CRAWL] Skipping {url}: {e}")
                continue

            # サーバー負荷軽減
            time.sleep(1.0)

        return articles

    def _crawl_article_page(
        self,
        page: ChromiumPage,
        url: str,
        source: dict,
        source_dir: Path,
        ts: str,
        idx: int,
    ) -> dict | None:
        """個別の記事ページをキャプチャ + HTML保存する"""
        try:
            page.get(url)
            page.wait.doc_loaded()
            time.sleep(1.5)
        except Exception as e:
            logger.warning(f"[CRAWL] Failed to load article: {url} — {e}")
            return None

        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        screenshot_path = source_dir / f"{ts}_article_{idx:02d}_{url_hash}.png"
        html_path = source_dir / f"{ts}_article_{idx:02d}_{url_hash}.html"

        # full_page=False: ファーストビューのみ（動画素材として使いやすい）
        try:
            page.get_screenshot(path=str(screenshot_path), left_top=(0, 0), right_bottom=(1920, 1080))
        except Exception as e:
            logger.warning(f"[CRAWL] Screenshot failed: {url} — {e}")

        html = page.html
        html_path.write_text(html, encoding="utf-8")

        title = page.title
        text = self._extract_text(page)

        if not text.strip():
            logger.debug(f"[CRAWL] Empty text, skipping: {url}")
            return None

        return {
            "source_id": source["id"],
            "source_name": source["name"],
            "url": url,
            "title": title or url,
            "screenshot_path": str(screenshot_path),
            "html_path": str(html_path),
            "text_content": text[:MAX_TEXT_LENGTH],
            "crawled_at": datetime.now(timezone.utc).isoformat(),
            "credibility": source["credibility"],
            "tier": source["tier"],
            "is_top_page": False,
        }

    # ──────────────────────────────────────────
    # X (Twitter) 専用処理
    # ──────────────────────────────────────────

    def _crawl_x_timeline(
        self, page: ChromiumPage, source: dict, source_dir: Path
    ) -> dict | None:
        """
        Xタイムラインのキャプチャ（ログインなし版）。
        nitter.net代替フロントエンドを試み、失敗した場合はx.comに直接アクセスする。
        """
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        nitter_url = source["url"].replace("x.com", "nitter.net")

        accessed_url = nitter_url
        success = False

        try:
            page.get(nitter_url)
            time.sleep(3)
            title = page.title or ""
            if "error" in title.lower() or "not found" in title.lower():
                raise ValueError("nitter returned error page")
            success = True
        except Exception:
            logger.info(
                f"[CRAWL] nitter unavailable for {source['id']}, "
                "falling back to x.com"
            )

        if not success:
            accessed_url = source["url"]
            try:
                page.get(source["url"])
                time.sleep(3)
            except Exception as e:
                logger.warning(f"[CRAWL] X access failed: {e}")
                return None

        # スクロールして複数ツイートをロード
        for _ in range(3):
            page.scroll.down(800)
            time.sleep(1.0)

        screenshot_path = source_dir / f"{ts}_x_timeline.png"
        try:
            page.get_screenshot(path=str(screenshot_path), left_top=(0, 0), right_bottom=(1920, 1080))
        except Exception as e:
            logger.warning(f"[CRAWL] Screenshot failed for X: {e}")

        text = self._extract_text(page)

        return {
            "source_id": source["id"],
            "source_name": source["name"],
            "url": accessed_url,
            "title": page.title or f"{source['name']} - Latest Posts",
            "screenshot_path": str(screenshot_path),
            "html_path": None,
            "text_content": text[:MAX_TEXT_LENGTH],
            "crawled_at": datetime.now(timezone.utc).isoformat(),
            "credibility": source["credibility"],
            "tier": source["tier"],
            "is_top_page": True,
        }

    # ──────────────────────────────────────────
    # ユーティリティ
    # ──────────────────────────────────────────

    def _extract_article_links(
        self, html_content: str, source: dict, actual_url: str = None
    ) -> list[str]:
        """
        HTMLから関連ニュース記事のURLを抽出する。
        ソース固有のCSSセレクタがある場合はそれを優先し、
        結果が少ない場合はヒューリスティックな汎用抽出で補完する。

        Args:
            html_content: ページのHTML文字列
            source: sources.yamlの1エントリ
            actual_url: リダイレクト後の実際のURL（Noneの場合はsource["url"]を使用）
        """
        soup = BeautifulSoup(html_content, "html.parser")
        # リダイレクト後の実URLをベースとして使用
        base_url = actual_url or source["url"]
        base_netloc = urlparse(base_url).netloc
        # サブドメイン違いも許容（例: news.un.org → un.org）
        base_domain = ".".join(base_netloc.split(".")[-2:])
        links: set[str] = set()

        # ── ソース固有セレクタによる抽出 ──
        selectors = source.get("selectors") or {}
        selector_str = selectors.get("news_links")
        if selector_str:
            for a_tag in soup.select(selector_str):
                href = a_tag.get("href")
                if href:
                    full_url = urljoin(base_url, href)
                    parsed_netloc = urlparse(full_url).netloc
                    # 同一ドメイン or 同一親ドメイン配下ならOK
                    if base_domain in parsed_netloc:
                        links.add(full_url)

        # ── 結果が不足している場合はヒューリスティック補完 ──
        if len(links) < 3:
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"]
                full_url = urljoin(base_url, href)
                parsed = urlparse(full_url)
                if base_domain in parsed.netloc:
                    if any(kw in href.lower() for kw in NEWS_PATH_KEYWORDS):
                        links.add(full_url)

        # TOPページ自体は除外（元URL・リダイレクト後URL両方）
        for u in [base_url, base_url.rstrip("/"), source["url"], source["url"].rstrip("/")]:
            links.discard(u)

        return list(links)


    def _extract_text(self, page: ChromiumPage) -> str:
        """
        ページからメインテキストを抽出する。
        article / main / [role=main] を優先し、
        なければbody全体からノイズ要素を除いたテキストを返す。
        """
        js = """
            (() => {
                const selectors = [
                    'article', 'main', '[role="main"]',
                    '.article-body', '.story-body',
                    '.post-content', '#content'
                ];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.innerText && el.innerText.trim().length > 200) {
                        return el.innerText;
                    }
                }
                const body = document.body.cloneNode(true);
                body.querySelectorAll(
                    'nav, footer, header, aside, script, style, ' +
                    '.nav, .footer, .header, .sidebar, .menu, .ad'
                ).forEach(el => el.remove());
                return body.innerText;
            })()
        """
        try:
            text = page.run_js(js, as_expr=True)  # ← as_expr=True を追加
            return text.strip() if text else ""
        except Exception as e:
            logger.warning(f"[CRAWL] Text extraction failed: {e}")
            return ""

