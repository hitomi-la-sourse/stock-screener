# 短期急騰株ランキング

東証上場銘柄から短期急騰候補を自動スクリーニングし、毎日14:00 JSTにGitHub Pagesで公開するWebアプリです。

## スクリーニング条件

1. 株価 200〜700円
2. 時価総額 100億円以下
3. 直近1ヶ月平均出来高の**2倍以上**（倍率が高い順にランキング）
4. ニュース・IR確認（ランキングページのボタンから手動確認）
5. 陽線・大陽線・上髭陽線（陰線を除外）

## セットアップ手順

### 1. GitHubリポジトリを作成

1. [GitHub](https://github.com) にログイン
2. 右上の「+」→「New repository」をクリック
3. リポジトリ名を入力（例: `stock-screener`）
4. **Public** を選択
5. 「Create repository」をクリック

### 2. このコードをアップロード

```bash
cd C:\Users\yuyaw\stock-screener
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/あなたのユーザー名/stock-screener.git
git push -u origin main
```

### 3. GitHub Pages を有効化

1. リポジトリの「Settings」タブを開く
2. 左メニュー「Pages」をクリック
3. Branch: `main` / Folder: `/docs` を選択して「Save」

### 4. 初回手動実行

1. 「Actions」タブを開く
2. 「短期急騰株ランキング更新」をクリック
3. 「Run workflow」→「Run workflow」をクリック

約30〜60分後にランキングが生成されます。

### 5. アクセスURL

```
https://あなたのユーザー名.github.io/stock-screener/
```

このURLをiOSのSafariで開き、「ホーム画面に追加」すればアプリのように使えます。

## 自動更新スケジュール

毎週月〜金の 14:00 JST（取引時間中）に自動更新されます。
祝日は東証が休場のため、前営業日のデータが表示されます。

## 注意事項

- 条件④（ニュース・IRなし）は**手動確認**が必要です。各銘柄の「ニュース」「株探」「IR」ボタンから必ずご確認ください。
- 投資は自己責任でお願いします。
