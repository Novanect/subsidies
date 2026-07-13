#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
⑦ サイト生成（本番版）
=======================
入力:
  data/jnet21_latest.json  … スクレイパーが毎朝取得する最新データ
  data/ai_cache.json       … 人間が承認済みのAI概要（記事IDをキーに保存）

出力:
  site/index.html

【設計方針】
  ・補助金名・実施機関・締切・URL = 公的機関が公表する事実 → 毎朝自動で更新
  ・概要（AI生成文）              = ai_cache.json にあるものだけ表示
    → 未検証のAI文は絶対に出さない。人間が承認したものだけが載る。
  ・新しい補助金は翌朝すぐ一覧に載る（名前・締切・リンクは事実なので安全）
"""
import json
import html
import os
import re
from datetime import datetime, date, timezone, timedelta

JST = timezone(timedelta(hours=9))
TODAY = datetime.now(JST).date()

REGION_ORDER = [("zenkoku", "全国"), ("tokyo", "東京都"), ("osaka", "大阪府")]

BANDS = [
    ("今週中",     0,   7,  "urgent"),
    ("今月中",     8,  31,  "soon"),
    ("2か月以内", 32,  62,  "mid"),
    ("それ以降",  63, 999,  "later"),
]

TAIL = [
    r'の公募(を開始します|開始について|について|のお知らせ)?',
    r'の(二次|一次|第[０-９0-9]+次)?公募(を開始します|開始について|について)?',
    r'の実施について', r'のご案内', r'のお知らせ', r'について（ご案内）', r'について',
    r'の募集(を開始します|開始|について)?', r'を開始します', r'を実施します',
    r'の受付(を開始します|について)?', r'交付申請受付のお知らせ',
    r'に係る補助事業者の公募', r'に係る実施事業者の公募', r'の申請受付',
    r'の二次募集を行います', r'に関する事前協議を受け付けています',
]


def esc(s):
    return html.escape(s or "")


def official_name(raw):
    """正式名称を抽出。補助金名は公的機関が公表する事実情報のため、加工せずそのまま用いる。"""
    t = re.sub(r'^【.+?】', '', raw)
    t = re.sub(r'^(補助金・助成金|融資・貸付|支援情報)\s*[：:]\s*', '', t)
    t = re.sub(r'^【公募開始】|^【公募のお知らせ】|^【事業者向け】', '', t).strip()

    start = 0 if t[:1] in '「『' else t.find('「')
    if start != -1:
        depth, end = 0, None
        for i in range(start, len(t)):
            if t[i] in '「『':
                depth += 1
            elif t[i] in '」』':
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end:
            t = t[start + 1:end]

    t = re.split(r'\s*～', t)[0]
    for pat in TAIL:
        t = re.sub(pat + r'$', '', t).strip()
    return t.strip('　 「」『』') or raw


def parse_deadline(period):
    m = re.search(r'～\s*(\d{4})年(\d{1,2})月(\d{1,2})日', period or "")
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def band_of(days):
    for label, lo, hi, cls in BANDS:
        if lo <= days <= hi:
            return label, cls
    return "それ以降", "later"


def load():
    with open("data/jnet21_latest.json", encoding="utf-8") as f:
        raw = json.load(f)

    ai = {}
    if os.path.exists("data/ai_cache.json"):
        with open("data/ai_cache.json", encoding="utf-8") as f:
            ai = json.load(f)

    rows = []
    for key, v in raw.items():
        for it in v["items"]:
            dl = parse_deadline(it["period"])
            a = ai.get(it["id"], {})
            rows.append({
                "region": v["region"],
                "id": it["id"],
                "url": it["url"],
                "org": it["org"],
                "official_name": a.get("official_name") or official_name(it["title"]),
                "deadline": dl.isoformat() if dl else "",
                "days_left": (dl - TODAY).days if dl else None,
                "summary": a.get("summary", ""),
                "target": a.get("target", ""),
                "note": a.get("note", ""),
            })
    return rows


def build(rows):
    now = datetime.now(JST)
    stamp = f"{now.year}年{now.month}月{now.day}日 {now.hour:02d}:{now.minute:02d}"

    tabs, panels = [], []
    for key, label in REGION_ORDER:
        items = [r for r in rows if r["region"] == label]
        dated = sorted([r for r in items if r["days_left"] is not None and r["days_left"] >= 0],
                       key=lambda r: r["deadline"])
        undated = [r for r in items if r["days_left"] is None]

        tabs.append(
            f'<button class="tab" data-region="{key}" role="tab" aria-selected="false" '
            f'aria-controls="panel-{key}">{label}<span class="tab-n">{len(dated)}</span></button>'
        )

        blocks, cur = [], None
        for r in dated:
            b_label, b_cls = band_of(r["days_left"])
            if b_label != cur:
                cur = b_label
                n = sum(1 for x in dated if band_of(x["days_left"])[0] == b_label)
                blocks.append(f'<h2 class="band band--{b_cls}">'
                              f'<span class="band-name">{b_label}</span>'
                              f'<span class="band-n">{n}</span></h2>')

            d = r["days_left"]
            dd = date.fromisoformat(r["deadline"])
            summary = f'<p class="sum">{esc(r["summary"])}</p>' if r["summary"] else ""
            chip = f'<span class="chip">{esc(r["target"])}</span>' if r["target"] else ""
            note = (f'<div class="notewrap"><span class="note">{esc(r["note"])}</span></div>'
                    if r["note"] else "")

            blocks.append(f'''
<a class="row row--{b_cls}" href="{esc(r["url"])}" target="_blank" rel="noopener">
  <div class="mark">
    <span class="dot"></span>
    <span class="days"><b>{d}</b><i>日</i></span>
    <span class="dl">{dd.month}/{dd.day}</span>
  </div>
  <div class="body">
    <h3 class="name">{esc(r["official_name"])}</h3>
    {summary}
    <div class="meta">{chip}<span class="org">{esc(r["org"])}</span></div>
    {note}
  </div>
</a>''')

        und = ""
        if undated:
            lis = "".join(
                f'<a class="mini" href="{esc(r["url"])}" target="_blank" rel="noopener">'
                f'<span class="mini-name">{esc(r["official_name"])}</span>'
                f'<span class="mini-org">{esc(r["org"])}</span></a>' for r in undated
            )
            und = (f'<details class="always"><summary>'
                   f'<span>通年・随時受付（締切の記載なし）</span><b>{len(undated)}</b>'
                   f'</summary><div class="mini-list">{lis}</div></details>')

        body = "".join(blocks) if blocks else '<p class="empty">締切が設定された公募は現在ありません。</p>'
        panels.append(f'<section class="panel" id="panel-{key}" role="tabpanel" hidden>{body}{und}</section>')

    return TEMPLATE.format(now=stamp, tabs="".join(tabs), panels="".join(panels))


TEMPLATE = '''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0f1f3d">
<title>現在公募中の補助金｜Novanect</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Roboto+Mono:wght@500;700&display=swap" rel="stylesheet">
<style>
:root{{
  --navy:#0f1f3d;--navy-2:#1c3157;--amber:#c8891a;--shu:#b7282e;
  --paper:#eef1f5;--card:#fff;--ink:#16203a;--muted:#6c7789;--line:#d8dfe8;
  --mono:'Roboto Mono',ui-monospace,monospace;
  --jp:-apple-system,'Hiragino Kaku Gothic ProN','Noto Sans JP','Yu Gothic',sans-serif;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
html{{-webkit-text-size-adjust:100%}}
body{{font-family:var(--jp);background:var(--paper);color:var(--ink);line-height:1.65;
  -webkit-font-smoothing:antialiased;padding-bottom:env(safe-area-inset-bottom)}}
.wrap{{max-width:680px;margin:0 auto}}
header{{background:var(--navy);color:#fff;padding:18px 18px 0;position:sticky;top:0;z-index:20;
  box-shadow:0 2px 14px rgba(15,31,61,.22)}}
.brand{{font-family:var(--mono);font-size:10px;letter-spacing:.22em;color:var(--amber);
  text-transform:uppercase;margin-bottom:5px}}
h1{{font-size:19px;font-weight:700;letter-spacing:.02em}}
.updated{{font-family:var(--mono);font-size:11px;color:#93a3ba;margin-top:3px;letter-spacing:.04em}}
.tabrow{{display:flex;align-items:flex-end;margin-top:14px}}
.axis-label{{flex:0 0 62px;display:flex;flex-direction:column;align-items:center;
  justify-content:flex-end;padding-bottom:11px;border-bottom:3px solid transparent;line-height:1.25}}
.axis-l1{{font-size:10px;font-weight:700;color:var(--amber);letter-spacing:.04em}}
.axis-l2{{font-size:9px;color:#8fa0b8}}
.tabs{{display:flex;gap:2px;flex:1}}
.tab{{flex:1;background:transparent;border:0;cursor:pointer;color:#8fa0b8;font-family:var(--jp);
  font-size:14px;font-weight:600;padding:11px 4px 12px;border-bottom:3px solid transparent;
  transition:color .18s,border-color .18s;display:flex;align-items:center;justify-content:center;gap:5px}}
.tab-n{{font-family:var(--mono);font-size:11px;font-weight:500;background:rgba(255,255,255,.1);
  padding:1px 6px;border-radius:9px}}
.tab[aria-selected="true"]{{color:#fff;border-bottom-color:var(--amber)}}
.tab[aria-selected="true"] .tab-n{{background:var(--amber);color:var(--navy);font-weight:700}}
.tab:focus-visible{{outline:2px solid var(--amber);outline-offset:-2px}}
.band{{position:sticky;top:108px;z-index:10;display:flex;align-items:center;justify-content:space-between;
  font-size:12px;font-weight:700;letter-spacing:.08em;padding:7px 18px;background:var(--navy-2);color:#fff}}
.band--urgent{{background:var(--shu)}}
.band--soon{{background:var(--amber);color:var(--navy)}}
.band-n{{font-family:var(--mono);font-size:11px;opacity:.82}}
.panel{{padding:0 0 28px}}
.row{{display:flex;text-decoration:none;color:inherit;background:var(--card);
  border-bottom:1px solid var(--line);position:relative}}
.row:active{{background:#f7f9fb}}
.mark{{flex:0 0 62px;position:relative;display:flex;flex-direction:column;align-items:center;
  padding:15px 0;background:#f4f7fa;border-right:1px solid var(--line)}}
.mark::before{{content:"";position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--line);
  transform:translateX(-.5px)}}
.dot{{width:9px;height:9px;border-radius:50%;background:var(--muted);border:2px solid var(--card);
  box-shadow:0 0 0 1px var(--line);position:relative;z-index:1;margin-bottom:7px}}
.row--urgent .dot{{background:var(--shu);box-shadow:0 0 0 1px var(--shu),0 0 0 4px rgba(183,40,46,.14)}}
.row--soon .dot{{background:var(--amber);box-shadow:0 0 0 1px var(--amber)}}
.days{{font-family:var(--mono);line-height:1;position:relative;z-index:1;background:#f4f7fa;padding:2px 0}}
.days b{{font-size:22px;font-weight:700;letter-spacing:-.03em}}
.days i{{font-style:normal;font-size:10px;margin-left:1px;color:var(--muted)}}
.row--urgent .days b{{color:var(--shu)}}
.row--soon .days b{{color:#96660f}}
.dl{{font-family:var(--mono);font-size:10px;color:var(--muted);margin-top:5px;background:#f4f7fa;
  padding:1px 0;position:relative;z-index:1}}
.body{{flex:1;min-width:0;padding:14px 16px 15px}}
.name{{font-size:15px;font-weight:700;line-height:1.45;color:var(--navy);letter-spacing:.01em}}
.sum{{font-size:13px;color:#43506a;margin-top:5px;line-height:1.6}}
.meta{{display:flex;flex-wrap:wrap;align-items:center;gap:7px;margin-top:9px}}
.chip{{font-size:11px;font-weight:600;color:var(--navy);background:#e4eaf2;border-radius:3px;padding:2px 7px}}
.org{{font-size:11px;color:var(--muted)}}
.notewrap{{margin-top:7px}}
.note{{font-family:var(--mono);font-size:11px;color:var(--shu);font-weight:500}}
.empty{{padding:44px 18px;text-align:center;color:var(--muted);font-size:14px}}
.always{{margin:22px 14px 0;background:var(--card);border:1px solid var(--line);border-radius:6px}}
.always summary{{cursor:pointer;list-style:none;padding:13px 15px;display:flex;justify-content:space-between;
  align-items:center;font-size:13px;font-weight:600;color:var(--navy)}}
.always summary::-webkit-details-marker{{display:none}}
.always summary b{{font-family:var(--mono);font-size:11px;color:var(--muted);font-weight:500}}
.always summary::after{{content:"＋";color:var(--amber);font-weight:700;margin-left:9px}}
.always[open] summary::after{{content:"−"}}
.mini-list{{border-top:1px solid var(--line)}}
.mini{{display:block;padding:11px 15px;text-decoration:none;border-bottom:1px solid #eef1f5}}
.mini:last-child{{border-bottom:0}}
.mini-name{{display:block;font-size:13px;font-weight:600;color:var(--navy);line-height:1.5}}
.mini-org{{display:block;font-size:11px;color:var(--muted);margin-top:2px}}
.cta{{margin:22px 14px 4px;padding:20px 18px;background:var(--navy);color:#fff;border-radius:6px;
  border-bottom:3px solid var(--amber)}}
.cta-lead{{font-size:15px;font-weight:700;margin-bottom:9px}}
.cta-body{{font-size:13px;line-height:1.75;color:#c9d4e2}}
.cta-body b{{color:var(--amber);font-weight:700}}
.cta-note{{font-family:var(--mono);font-size:10px;letter-spacing:.04em;color:#8fa0b8;margin-top:11px;
  padding-top:10px;border-top:1px solid rgba(255,255,255,.12)}}
footer{{padding:26px 20px 34px;text-align:center;font-size:11px;color:var(--muted);line-height:1.9}}
.src{{font-family:var(--mono);font-size:10px;letter-spacing:.04em;color:#8b96a8;margin-top:8px}}
@media (prefers-reduced-motion:no-preference){{
  .row{{animation:in .34s ease-out both}}
  .row:nth-child(-n+8){{animation-delay:calc(var(--i,0) * 24ms)}}
  @keyframes in{{from{{opacity:0;transform:translateY(5px)}}to{{opacity:1;transform:none}}}}
}}
@media (min-width:681px){{
  .wrap{{box-shadow:0 0 40px rgba(15,31,61,.09);min-height:100vh}}
}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="brand">Novanect</div>
  <h1>現在公募中の補助金</h1>
  <div class="updated">{now} 更新</div>
  <div class="tabrow">
    <div class="axis-label">
      <span class="axis-l1">締切まで</span>
      <span class="axis-l2">の残り日数</span>
    </div>
    <div class="tabs" role="tablist">{tabs}</div>
  </div>
</header>

{panels}

<div class="cta">
  <p class="cta-lead">気になる補助金はありましたか？</p>
  <p class="cta-body">この画面を閉じて、メニューの<b>「無料相談」</b>を押してください。<br>補助金名をお知らせいただければ、対象になるかを確認してご返信します。</p>
  <p class="cta-note">入力はいりません。ボタンを押すだけです。</p>
</div>

<footer>
  補助金名・実施機関・締切は公的機関の公表情報です。<br>
  概要は当社が整理したものです。最新かつ正確な内容は<br>
  各補助金の公式情報で必ずご確認ください。
  <div class="src">出典：中小機構 J-Net21</div>
</footer>
</div>

<script>
(function(){{
  var tabs=document.querySelectorAll('.tab'),panels=document.querySelectorAll('.panel');
  function show(k,push){{
    tabs.forEach(function(t){{t.setAttribute('aria-selected',String(t.dataset.region===k));}});
    panels.forEach(function(p){{p.hidden=(p.id!=='panel-'+k);}});
    if(push)history.replaceState(null,'','#'+k);
    window.scrollTo(0,0);
  }}
  tabs.forEach(function(t){{t.addEventListener('click',function(){{show(t.dataset.region,true);}});}});
  var v=['zenkoku','tokyo','osaka'],h=(location.hash||'').replace('#','');
  show(v.indexOf(h)>=0?h:'zenkoku',false);
  panels.forEach(function(p){{
    p.querySelectorAll('.row').forEach(function(r,i){{r.style.setProperty('--i',i);}});
  }});
}})();
</script>
</body>
</html>'''


if __name__ == "__main__":
    rows = load()
    os.makedirs("site", exist_ok=True)
    with open("site/index.html", "w", encoding="utf-8") as f:
        f.write(build(rows))

    n_sum = sum(1 for r in rows if r["summary"])
    print("サイトを生成しました: site/index.html")
    for key, label in REGION_ORDER:
        items = [r for r in rows if r["region"] == label]
        dated = [r for r in items if r["days_left"] is not None and r["days_left"] >= 0]
        print(f"  {label}: 締切あり {len(dated)}件 / 通年 {len(items)-len(dated)}件")
    print(f"  概要つき（人間が承認済み）: {n_sum}件 / 全{len(rows)}件")
