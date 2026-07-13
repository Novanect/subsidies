#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
J-Net21 補助金情報スクレイパー（本番版）
==========================================
前回の失敗（3か月古いデータを取得）の原因対策を組み込んだ版。

【前回の失敗原因】
  J-Net21のresults.phpはPHPセッション(Cookie)に検索条件とページ位置を保持する。
  Cookieを持たずにURLだけで叩くと、サーバー側の別の状態が返ってくる。
  実際に page=1 を要求して page=129 が返却され、件数も144/183/202/3864/5515と
  リクエストごとにバラバラだった。

【本スクリプトの対策】
  対策1: requests.Session() でCookieを維持し、ブラウザと同じ順序でアクセス
  対策2: 要求ページ番号 == 実際に返ってきたページ番号 を毎回検証
  対策3: 全ページで「検索結果◯件」が一致するかを検証
  対策4: 最新掲載日が FRESHNESS_DAYS 以内かを検証（古いデータを掴んだら停止）
  対策5: いずれかの検証に失敗したら、データを保存せず異常終了する

【使い方】
  pip install requests beautifulsoup4
  python jnet21_scraper.py
"""

import requests
from bs4 import BeautifulSoup
import time
import re
import json
import sys
import os
from datetime import datetime, timedelta, timezone

# ───────────────────────── 設定 ─────────────────────────
JST = timezone(timedelta(hours=9))
TOP_URL    = "https://j-net21.smrj.go.jp/snavi2/"
RESULT_URL = "https://j-net21.smrj.go.jp/snavi2/results.php"

REGIONS = {
    # fresh_days: 最新掲載日がこの日数以内でなければ異常とみなす。
    # 地域ごとに更新頻度が違うため個別に設定する。
    # （大阪は案件数が少なく更新間隔が空くため、許容を広く取る）
    "zenkoku": {"code": "00", "label": "全国",   "fresh_days": 14},
    "tokyo":   {"code": "13", "label": "東京都", "fresh_days": 21},
    "osaka":   {"code": "27", "label": "大阪府", "fresh_days": 35},
}

# ガードレール設定
MAX_PAGES      = 60   # 暴走防止の上限
SLEEP_SEC      = 2.0  # サーバー負荷への配慮（必ず1秒以上あけること）

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9",
    "Cache-Control": "no-cache",   # 中間キャッシュを避ける
    "Pragma": "no-cache",
}


class ScrapeError(Exception):
    """検証失敗時に投げる。データは保存せず処理を止める。"""
    pass


# ───────────────────── セッション構築 ─────────────────────
def new_session():
    """対策1: Cookieを保持するセッションを作り、まずトップページを踏む。
    これでサーバー側にセッションが確立され、以降の検索条件が正しく反映される。"""
    s = requests.Session()
    s.headers.update(HEADERS)
    r = s.get(TOP_URL, timeout=30)
    r.raise_for_status()
    time.sleep(SLEEP_SEC)
    return s


def fetch(session, pref_code, page):
    """1ページ取得。セッションを引き継いで叩く。"""
    params = {
        "category": "2",                 # 補助金・助成金・融資
        "prefecture[]": pref_code,
        "sort": "publish_date_default",
        "displaysort": "DESC",           # 掲載日の新しい順
        "displaycount": "30",
        "period": "1",
        "page": str(page),
        "navitype": "is-number",
    }
    r = session.get(RESULT_URL, params=params, timeout=30)
    r.raise_for_status()
    r.encoding = r.apparent_encoding
    return r.text


# ───────────────────── 検証（ガードレール） ─────────────────────
def to_text(html):
    """HTMLタグを剥がして、素のテキストにする。
    「検索結果：<span>144</span>件」のようにタグが挟まっていても読めるようにするため。
    （前回、総件数がNoneになった原因の対策）"""
    soup = BeautifulSoup(html, "html.parser")
    txt = soup.get_text(" ", strip=False)
    return re.sub(r'\s+', ' ', txt)   # 改行・連続空白を半角スペース1つに畳む


def get_total_count(html):
    """「検索結果： ◯◯件」から総件数を読む（タグ・改行に強い版）"""
    t = to_text(html)
    m = re.search(r'検索結果\s*[：:]?\s*([\d,]+)\s*件', t)
    if m:
        return int(m.group(1).replace(",", ""))
    # 予備手段: 「◯-◯件表示」の後ろにある総件数表記を探す
    m = re.search(r'([\d,]+)\s*件\s*[\d,]+\s*-\s*[\d,]+\s*件表示', t)
    return int(m.group(1).replace(",", "")) if m else None


def get_current_page(html):
    """対策2: 実際にサーバーが返したページ番号を読む。
    「◯-◯件表示」から算出する（1ページ30件前提）。"""
    t = to_text(html)
    m = re.search(r'([\d,]+)\s*-\s*([\d,]+)\s*件表示', t)
    if not m:
        return None
    start = int(m.group(1).replace(",", ""))
    return (start - 1) // 30 + 1


def get_last_page(html):
    """ページ送りリンクから最終ページ番号を得る"""
    pages = [int(x) for x in re.findall(r'page=(\d+)', html)]
    return max(pages) if pages else 1


# ───────────────────── 抽出 ─────────────────────
def parse_items(html):
    soup = BeautifulSoup(html, "html.parser")
    items = []
    # リンクは "articles/187226" のような相対パスで書かれている。
    # 先頭スラッシュ有無の両方に対応するため、"articles/" を含むものを広く拾う。
    for a in soup.select("a[href*='articles/']"):
        title = a.get_text(strip=True)
        href = a.get("href", "")
        if not title or not href:
            continue

        aid_m = re.search(r'articles/(\d+)', href)
        if not aid_m:
            continue
        aid = aid_m.group(1)
        url = f"https://j-net21.smrj.go.jp/snavi2/articles/{aid}"

        # 【重要】この記事1件だけを含むブロックを正確に特定する。
        # 無条件に親を遡ると、記事一覧全体の箱まで到達し、
        # 他の記事の日付や実施機関を誤って拾ってしまう。
        # そこで「記事リンクを1つしか含まない最大の祖先」を探す。
        block = a
        node = a
        for _ in range(8):
            node = node.parent
            if node is None:
                break
            # この祖先が含む記事リンクの数を数える
            n_links = len(node.select("a[href*='articles/']"))
            if n_links > 1:
                break        # 他の記事も入ってしまったので、1つ前で確定
            block = node     # まだ1件だけ → ここまでは安全

        text = block.get_text("\n", strip=True)

        d = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', text)
        pub = f"{d.group(1)}-{int(d.group(2)):02d}-{int(d.group(3)):02d}" if d else ""

        org = ""
        if "実施機関" in text:
            org = text.split("実施機関", 1)[1].lstrip("：: \n").split("\n")[0].strip()

        period = ""
        if "募集期間" in text:
            period = text.split("募集期間", 1)[1].lstrip("：: \n").split("\n")[0].strip()

        items.append({
            "id": aid,
            "title": title,
            "url": url,
            "pub_date": pub,
            "org": org,
            "period": period,
        })

    # 同一ページ内の重複を除去
    seen, out = set(), []
    for it in items:
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        out.append(it)
    return out


# ───────────────────── 地域単位の取得 ─────────────────────
def scrape_region(key, code, label, fresh_days):
    print(f"\n=== [{label}] 取得開始 ===")
    session = new_session()          # 対策1: 地域ごとに新しいセッション

    html = fetch(session, code, 1)

    # --- 検証A: 1ページ目が本当に1ページ目か（前回はここで129が返っていた） ---
    served = get_current_page(html)
    if served is not None and served != 1:
        raise ScrapeError(
            f"[{label}] 1ページ目を要求したが {served} ページ目が返却された。"
            f"セッション不整合。処理を中止する。"
        )

    total = get_total_count(html)
    last_page = min(get_last_page(html), MAX_PAGES)
    shown = f"{total}件" if total is not None else "不明（表示形式が変わった可能性）"
    print(f"  総件数: {shown} / 全{last_page}ページ")
    # 総件数はあくまで補助指標。読めなくても停止はしない（鮮度チェックが本命の砦）。
    if total is None:
        print("  ※注意: 総件数を読み取れず。件数の整合性チェックはスキップします。")

    all_items = parse_items(html)

    # 診断表示: 1ページ目の抽出結果をすぐ見せる（問題の切り分けを速くするため）
    print(f"  1ページ目から {len(all_items)} 件を抽出")
    if all_items:
        d0 = [i["pub_date"] for i in all_items if i["pub_date"]]
        if d0:
            print(f"  1ページ目の最新掲載日: {max(d0)}  ← ここが直近の日付なら成功")
        print(f"  抽出例: {all_items[0]['title'][:40]}")
    else:
        raise ScrapeError(f"[{label}] 1ページ目から1件も抽出できなかった。ページ構造が変わった可能性。")

    for page in range(2, last_page + 1):
        time.sleep(SLEEP_SEC)
        html = fetch(session, code, page)

        # --- 検証B: 要求ページ == 返却ページ ---
        served = get_current_page(html)
        if served is not None and served != page:
            raise ScrapeError(
                f"[{label}] {page}ページ目を要求したが {served} ページ目が返却された。中止。"
            )

        # --- 検証C: 総件数が全ページで一致するか（前回は144/183/202と揺れた） ---
        # 総件数が読めた場合のみ実行する
        t = get_total_count(html)
        if total is not None and t is not None and t != total:
            raise ScrapeError(
                f"[{label}] 総件数が変動した（1ページ目:{total}件 → {page}ページ目:{t}件）。"
                f"取得中にデータが更新されたか、セッションが切り替わった。中止して再実行すること。"
            )

        all_items.extend(parse_items(html))
        print(f"  {page}/{last_page} ページ完了")

    # 全ページ通しの重複除去
    seen, items = set(), []
    for it in all_items:
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        items.append(it)

    # --- 検証D: 鮮度チェック（今回の事故を直接検知する要） ---
    dates = [i["pub_date"] for i in items if i["pub_date"]]
    if not dates:
        raise ScrapeError(f"[{label}] 掲載日を1件も抽出できなかった。ページ構造が変わった可能性。")

    newest = max(dates)
    today = datetime.now(JST).date()
    newest_d = datetime.strptime(newest, "%Y-%m-%d").date()
    age = (today - newest_d).days
    print(f"  最新掲載日: {newest}（{age}日前）")

    if age > fresh_days:
        raise ScrapeError(
            f"[{label}] 最新の掲載日が {newest}（{age}日前）で古すぎる。"
            f"許容は{fresh_days}日以内。キャッシュまたはセッション不整合の疑い。中止。"
        )

    print(f"  取得完了: {items and len(items)}件（重複除去後）")
    return {
        "region": label,
        "total_on_site": total,
        "fetched": len(items),
        "newest_pub_date": newest,
        "scraped_at": datetime.now(JST).isoformat(),
        "items": items,
    }


# ───────────────────── メイン ─────────────────────
def main():
    os.makedirs("data", exist_ok=True)
    results = {}
    errors = []

    for key, info in REGIONS.items():
        try:
            results[key] = scrape_region(key, info["code"], info["label"], info["fresh_days"])
        except ScrapeError as e:
            print(f"\n!!! 検証失敗: {e}", file=sys.stderr)
            errors.append(str(e))
        except Exception as e:
            print(f"\n!!! 想定外のエラー [{info['label']}]: {e}", file=sys.stderr)
            errors.append(f"{info['label']}: {e}")

    if errors:
        # 対策5: 1つでも失敗したら保存せず異常終了
        # （古い/壊れたデータでスプレッドシートを上書きしないため）
        print("\n" + "=" * 55, file=sys.stderr)
        print("検証に失敗したため、データを保存せず終了します。", file=sys.stderr)
        print("既存のスプレッドシート/サイトは前回の内容を維持します。", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        print("=" * 55, file=sys.stderr)
        sys.exit(1)   # GitHub Actionsならここで失敗通知が飛ぶ

    out = "data/jnet21_latest.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 55)
    print("全地域の取得と検証に成功しました。")
    for k, v in results.items():
        print(f"  {v['region']}: {v['fetched']}件 / 最新 {v['newest_pub_date']}")
    print(f"→ {out} に保存")
    print("=" * 55)


if __name__ == "__main__":
    main()
