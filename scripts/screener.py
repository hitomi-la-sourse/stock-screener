#!/usr/bin/env python3
"""
短期急騰株スクリーナー
東証上場銘柄の中から条件を満たす銘柄を抽出してランキングを生成する
毎日14:00 JST (05:00 UTC) に GitHub Actions で実行
"""

import yfinance as yf
import pandas as pd
import json
import os
import requests
import io
from datetime import datetime, timezone, timedelta
import time

JST = timezone(timedelta(hours=9))


def get_tse_stocks():
    """JPX から東証上場銘柄リストを取得"""
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    stocks = {}
    try:
        print("JPX から銘柄リスト取得中...")
        resp = requests.get(url, headers=headers, timeout=60)
        resp.raise_for_status()

        # .xls を読み込む（xlrd が必要）
        try:
            df = pd.read_excel(io.BytesIO(resp.content), engine="xlrd")
        except Exception:
            df = pd.read_excel(io.BytesIO(resp.content))

        print(f"カラム: {df.columns.tolist()[:8]}")

        code_col = None
        name_col = None
        for col in df.columns:
            cs = str(col)
            if "コード" in cs:
                code_col = col
            if "銘柄名" in cs or ("銘柄" in cs and name_col is None):
                name_col = col

        if code_col is None:
            print(f"コードカラムが見つかりません: {df.columns.tolist()}")
            return {}

        for _, row in df.iterrows():
            try:
                raw = str(row[code_col]).strip().split(".")[0]
                if not raw.isdigit() or len(raw) != 4:
                    continue
                code_int = int(raw)
                # ETF/REIT 等を除外
                if 1300 <= code_int <= 1699 or 1800 <= code_int <= 1899:
                    continue
                name = str(row[name_col]).strip() if name_col else raw
                stocks[f"{raw}.T"] = name
            except Exception:
                continue

        print(f"取得銘柄数: {len(stocks)}")
        return stocks

    except Exception as e:
        print(f"JPX からの取得失敗: {e}")
        return {}


def classify_candle(open_p, high, low, close):
    """ローソク足を分類する（陰線は None）"""
    if close <= open_p:
        return None  # 陰線

    body = close - open_p
    total_range = high - low if high > low else 0.01
    upper_shadow = high - close
    body_ratio = body / total_range

    if body_ratio >= 0.7:
        return "大陽線"
    elif upper_shadow >= body * 0.5:
        return "上髭陽線"
    else:
        return "陽線"


def screen_pass1(stocks_dict):
    """
    第1フィルタ: 価格・出来高・ローソク足
    - 株価 200〜700円
    - 本日出来高 >= 直近20営業日平均の2倍
    - 陽線・大陽線・上髭陽線
    """
    tickers = list(stocks_dict.keys())
    candidates = []
    CHUNK = 50
    total = (len(tickers) + CHUNK - 1) // CHUNK

    for idx in range(0, len(tickers), CHUNK):
        chunk = tickers[idx : idx + CHUNK]
        num = idx // CHUNK + 1
        if num % 5 == 0 or num == 1:
            print(f"  第1フィルタ: チャンク {num}/{total} (候補累計: {len(candidates)})")

        try:
            data = yf.download(
                chunk,
                period="3mo",
                interval="1d",
                auto_adjust=True,
                progress=False,
                threads=False,
                group_by="ticker",
            )
            if data.empty:
                continue

            for ticker in chunk:
                try:
                    df = data[ticker] if len(chunk) > 1 else data
                    df = df.dropna(subset=["Close", "Volume"])
                    if len(df) < 22:
                        continue

                    latest = df.iloc[-1]
                    close = float(latest["Close"])
                    open_p = float(latest["Open"])
                    high = float(latest["High"])
                    low = float(latest["Low"])
                    vol_today = float(latest["Volume"])
                    date_str = str(df.index[-1].date())

                    # 条件①: 株価 200〜700 円
                    if not (200 <= close <= 700):
                        continue

                    # 条件⑤: 陽線系のみ
                    candle = classify_candle(open_p, high, low, close)
                    if candle is None:
                        continue

                    # 条件③: 直近 20 営業日平均の 2 倍以上
                    vol_hist = df["Volume"].iloc[-21:-1]
                    if len(vol_hist) < 10:
                        continue
                    avg_vol = float(vol_hist.mean())
                    if avg_vol <= 0:
                        continue
                    vol_ratio = vol_today / avg_vol
                    if vol_ratio < 2.0:
                        continue

                    # 前日比
                    prev_close = float(df.iloc[-2]["Close"])
                    change_pct = round((close - prev_close) / prev_close * 100, 2) if prev_close > 0 else 0

                    candidates.append({
                        "ticker": ticker,
                        "code": ticker.replace(".T", ""),
                        "name": stocks_dict.get(ticker, ticker.replace(".T", "")),
                        "close": round(close, 0),
                        "open": round(open_p, 0),
                        "high": round(high, 0),
                        "low": round(low, 0),
                        "volume": int(vol_today),
                        "avg_volume": int(avg_vol),
                        "vol_ratio": round(vol_ratio, 2),
                        "candle_type": candle,
                        "change_pct": change_pct,
                        "date": date_str,
                    })
                except Exception:
                    continue

        except Exception as e:
            print(f"  チャンクエラー ({num}): {e}")

        time.sleep(1.2)

    print(f"第1フィルタ通過: {len(candidates)} 銘柄")
    return candidates


def screen_pass2(candidates):
    """
    第2フィルタ: 時価総額 100億円以下
    候補が少ないので個別取得でも許容範囲
    """
    results = []
    for i, c in enumerate(candidates):
        ticker = c["ticker"]
        try:
            fi = yf.Ticker(ticker).fast_info
            mc = getattr(fi, "market_cap", None)
            if mc is None:
                shares = getattr(fi, "shares", None)
                if shares and shares > 0:
                    mc = c["close"] * shares
            if mc is None or mc <= 0:
                continue
            if mc > 10_000_000_000:  # 100 億円
                continue
            c["market_cap"] = int(mc)
            c["market_cap_str"] = f"{mc / 100_000_000:.1f}億円"
            results.append(c)
        except Exception:
            continue
        time.sleep(0.3)

    print(f"第2フィルタ通過 (時価総額): {len(results)} 銘柄")
    return results


def generate_html(results, updated_at):
    candle_emoji = {"大陽線": "🔥", "上髭陽線": "📈", "陽線": "✅"}

    cards = ""
    if not results:
        cards = """
        <div class="no-results">
          <div class="no-icon">📭</div>
          <p>本日は条件に合致する銘柄がありませんでした。</p>
          <p class="sub">市場休場日や条件が厳しい日には表示されないことがあります。</p>
        </div>"""
    else:
        for r in results:
            emoji = candle_emoji.get(r["candle_type"], "📊")
            vol_w = min(100, int(r["vol_ratio"] / 10 * 100))
            chg_cls = "pos" if r["change_pct"] >= 0 else "neg"
            chg_sign = "+" if r["change_pct"] >= 0 else ""

            cards += f"""
        <div class="card">
          <div class="card-top">
            <span class="rank">#{r['rank']}</span>
            <span class="candle">{emoji} {r['candle_type']}</span>
            <span class="vol-badge">{r['vol_ratio']}倍</span>
          </div>
          <div class="card-body">
            <div class="name-row">
              <span class="code">{r['code']}</span>
              <span class="name">{r['name']}</span>
            </div>
            <div class="price-row">
              <span class="price">¥{r['close']:,.0f}</span>
              <span class="chg {chg_cls}">({chg_sign}{r['change_pct']}%)</span>
            </div>
            <div class="ohlc">O:{r['open']:,.0f} H:{r['high']:,.0f} L:{r['low']:,.0f}</div>
            <div class="vol-section">
              <div class="vol-label">出来高倍率</div>
              <div class="vol-track"><div class="vol-fill" style="width:{vol_w}%"></div></div>
              <div class="vol-num">{r['vol_ratio']}倍 &nbsp;({r['volume']:,}株 / 平均{r['avg_volume']:,}株)</div>
            </div>
            <div class="mktcap">時価総額: {r['market_cap_str']}</div>
            <div class="btns">
              <a href="https://finance.yahoo.co.jp/quote/{r['code']}.T/news" target="_blank" class="btn">📰 ニュース</a>
              <a href="https://kabutan.jp/stock/?code={r['code']}" target="_blank" class="btn">📊 株探</a>
              <a href="https://irbank.net/{r['code']}" target="_blank" class="btn">📋 IR</a>
            </div>
            <div class="date-tag">データ日: {r['date']}</div>
          </div>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="theme-color" content="#0d0d1f">
  <title>急騰株ランキング</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Helvetica Neue',sans-serif;background:#0d0d1f;color:#e0e0e0;min-height:100vh;padding-bottom:40px}}
    .header{{background:linear-gradient(135deg,#12122a,#1a1a3e);padding:14px 16px 12px;position:sticky;top:0;z-index:99;border-bottom:1px solid #252545}}
    .header h1{{font-size:17px;font-weight:800;color:#ff5c5c;letter-spacing:.5px}}
    .header .sub{{font-size:11px;color:#666;margin-top:2px}}
    .updated{{font-size:12px;color:#4caf50;margin-top:3px}}
    .badge{{display:inline-block;background:#ff5c5c;color:#fff;font-size:11px;font-weight:700;padding:2px 9px;border-radius:12px;margin-top:5px}}

    .cond-box{{background:#13132b;margin:12px;padding:11px 13px;border-radius:10px;border:1px solid #22224a}}
    .cond-box h3{{font-size:12px;color:#888;margin-bottom:6px;font-weight:600}}
    .cond-box ul{{list-style:none;font-size:12px;color:#777;line-height:1.9}}
    .cond-box ul li::before{{content:"✓ ";color:#4caf50}}

    .warn{{background:#1e1200;margin:0 12px 12px;padding:10px 12px;border-radius:8px;border-left:3px solid #ff9800;font-size:12px;color:#ffb74d;line-height:1.6}}

    .list{{padding:0 12px}}

    .card{{background:#13132b;border-radius:13px;margin-bottom:12px;border:1px solid #22224a;overflow:hidden}}
    .card-top{{display:flex;align-items:center;gap:8px;padding:9px 13px;background:#0f0f24}}
    .rank{{font-size:21px;font-weight:900;color:#ff5c5c;min-width:42px}}
    .candle{{flex:1;font-size:13px;font-weight:700}}
    .vol-badge{{background:#ff5c5c;color:#fff;font-size:13px;font-weight:800;padding:4px 10px;border-radius:20px;white-space:nowrap}}

    .card-body{{padding:11px 13px}}
    .name-row{{display:flex;align-items:baseline;gap:7px;margin-bottom:5px}}
    .code{{font-size:15px;font-weight:800;color:#5b9cf6}}
    .name{{font-size:13px;color:#bbb;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
    .price-row{{display:flex;align-items:baseline;gap:7px;margin-bottom:3px}}
    .price{{font-size:24px;font-weight:900;color:#4caf50}}
    .chg{{font-size:13px;font-weight:600}}
    .pos{{color:#4caf50}}.neg{{color:#f44336}}
    .ohlc{{font-size:11px;color:#555;margin-bottom:8px}}

    .vol-section{{margin-bottom:7px}}
    .vol-label{{font-size:11px;color:#777;margin-bottom:3px}}
    .vol-track{{height:5px;background:#1e1e3e;border-radius:3px;overflow:hidden;margin-bottom:3px}}
    .vol-fill{{height:100%;background:linear-gradient(90deg,#ff5c5c,#ff9800);border-radius:3px;transition:width .4s}}
    .vol-num{{font-size:11px;color:#888}}

    .mktcap{{font-size:12px;color:#777;margin-bottom:8px}}

    .btns{{display:flex;gap:7px;margin-bottom:6px}}
    .btn{{flex:1;display:block;text-align:center;padding:8px 2px;background:#1a1a3a;color:#9999cc;text-decoration:none;border-radius:8px;font-size:11px;font-weight:600;border:1px solid #252550}}
    .btn:active{{background:#252550}}

    .date-tag{{font-size:10px;color:#444;text-align:right}}

    .no-results{{text-align:center;padding:50px 20px;color:#555}}
    .no-icon{{font-size:48px;margin-bottom:12px}}
    .no-results p{{font-size:14px;margin-bottom:6px}}
    .no-results .sub{{font-size:12px;color:#444}}

    .refresh{{display:block;margin:16px 12px;padding:14px;background:#1a1a3a;color:#888;text-align:center;border-radius:11px;font-size:14px;text-decoration:none;border:1px solid #252550}}
    .footer{{text-align:center;padding:16px;font-size:11px;color:#333;line-height:1.7}}

    /* QR モーダル */
    .qr-fab{{position:fixed;bottom:24px;right:16px;width:52px;height:52px;background:#ff5c5c;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:22px;box-shadow:0 4px 16px rgba(255,92,92,.5);cursor:pointer;z-index:200;border:none;color:#fff}}
    .qr-fab:active{{transform:scale(.94)}}
    .qr-overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:300;align-items:center;justify-content:center}}
    .qr-overlay.open{{display:flex}}
    .qr-modal{{background:#13132b;border-radius:18px;padding:24px 20px 20px;text-align:center;width:280px;border:1px solid #252550}}
    .qr-modal h2{{font-size:15px;font-weight:800;color:#e0e0e0;margin-bottom:4px}}
    .qr-modal p{{font-size:11px;color:#666;margin-bottom:16px}}
    .qr-canvas{{margin:0 auto 12px;display:block}}
    .qr-url{{font-size:10px;color:#555;word-break:break-all;margin-bottom:16px;padding:0 4px}}
    .qr-close{{display:block;width:100%;padding:11px;background:#1a1a3a;color:#aaa;border:1px solid #252550;border-radius:10px;font-size:14px;cursor:pointer}}
    .qr-close:active{{background:#252550}}
  </style>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
</head>
<body>
  <div class="header">
    <h1>🚀 短期急騰株ランキング</h1>
    <div class="sub">東証上場銘柄 スクリーニング</div>
    <div class="updated">最終更新: {updated_at}</div>
    <span class="badge">{len(results)} 銘柄 該当</span>
  </div>

  <div class="cond-box">
    <h3>スクリーニング条件</h3>
    <ul>
      <li>株価 200〜700 円</li>
      <li>時価総額 100 億円以下</li>
      <li>直近 1 ヶ月平均出来高の 2 倍以上（多い順にランキング）</li>
      <li>陽線・大陽線・上髭陽線（陰線を除外）</li>
    </ul>
  </div>

  <div class="warn">
    ⚠️ <strong>ニュース・IR を必ず確認してください</strong><br>
    材料あり銘柄は対象外です。各ボタンからニュース・IRの有無をご確認ください。
  </div>

  <div class="list">
    {cards}
  </div>

  <a href="javascript:location.reload()" class="refresh">🔄 ページを更新</a>

  <div class="footer">
    <p>データ出典: Yahoo Finance（yfinance 経由）</p>
    <p>次回更新: 翌営業日 14:00 JST</p>
    <p style="margin-top:6px;color:#2a2a4a">投資は自己責任でお願いします</p>
  </div>

  <!-- QR フローティングボタン -->
  <button class="qr-fab" onclick="openQR()" title="QRコードを表示">📲</button>

  <!-- QR モーダル -->
  <div class="qr-overlay" id="qrOverlay" onclick="closeQR(event)">
    <div class="qr-modal" onclick="event.stopPropagation()">
      <h2>📲 このページのQRコード</h2>
      <p>スキャンして他のデバイスで開く</p>
      <div id="qrcode" class="qr-canvas"></div>
      <div class="qr-url" id="qrUrl"></div>
      <button class="qr-close" onclick="closeQR()">閉じる</button>
    </div>
  </div>

  <script>
    var qrGenerated = false;
    function openQR() {{
      var url = location.href.split('?')[0].split('#')[0];
      document.getElementById('qrUrl').textContent = url;
      if (!qrGenerated) {{
        new QRCode(document.getElementById('qrcode'), {{
          text: url,
          width: 200,
          height: 200,
          colorDark: '#ffffff',
          colorLight: '#13132b',
          correctLevel: QRCode.CorrectLevel.M
        }});
        qrGenerated = true;
      }}
      document.getElementById('qrOverlay').classList.add('open');
    }}
    function closeQR(e) {{
      if (!e || e.target === document.getElementById('qrOverlay') || !e.target.classList.contains('qr-modal')) {{
        document.getElementById('qrOverlay').classList.remove('open');
      }}
    }}
  </script>
</body>
</html>"""


def main():
    now = datetime.now(JST)
    print(f"=== 短期急騰株スクリーナー 開始: {now.strftime('%Y-%m-%d %H:%M:%S JST')} ===")

    stocks = get_tse_stocks()
    if not stocks:
        print("銘柄リストの取得に失敗しました。エラーページを生成します。")
        html = generate_html([], f"{now.strftime('%Y年%m月%d日 %H:%M JST')} (取得エラー)")
        os.makedirs("docs", exist_ok=True)
        with open("docs/index.html", "w", encoding="utf-8") as f:
            f.write(html)
        return

    candidates = screen_pass1(stocks)
    results = screen_pass2(candidates)

    results.sort(key=lambda x: x["vol_ratio"], reverse=True)
    for i, r in enumerate(results):
        r["rank"] = i + 1

    updated_at = now.strftime("%Y年%m月%d日 %H:%M JST")
    os.makedirs("docs", exist_ok=True)

    with open("docs/data.json", "w", encoding="utf-8") as f:
        json.dump({"updated_at": updated_at, "count": len(results), "stocks": results},
                  f, ensure_ascii=False, indent=2)

    html = generate_html(results, updated_at)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print(f"=== 完了: {len(results)} 銘柄 → docs/index.html 生成 ===")


if __name__ == "__main__":
    main()
