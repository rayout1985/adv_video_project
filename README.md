# ADV Video Project (WSL向け・Filmora対応)

このテンプレートは、**台本(JSON)** を元に VOICEVOX で音声を生成し、MoviePyで **ADV形式の合成動画(mp4)** と **字幕(srt)** を作ります。最終編集はFilmoraで行えます。

## フォルダ構成

```
adv_video_project/
  adv_maker.py
  setup_project.py
  requirements.txt
  config/
    config.py
  scripts/
    schema.py
    sample_script.json
    voicevox_client.py
    srt_utils.py
    actions.py
  assets/
    bg/
    chars/
    eyecatch/
  voices/
  output/
```

## セットアップ（WSL）

1. Windows側で VOICEVOX Engine を起動  
   `run.exe --host 0.0.0.0 --port 50021 --cors_policy_mode all`

2. WSLでこのプロジェクトに移動して実行  
   ```bash
   python3 setup_project.py
   ```

3. 画像を配置  
   - 背景: `assets/bg/bg.png`（サンプル名に合わせるか、JSONのパスを変更）  
   - キャラ: `assets/chars/char_left.png`, `assets/chars/char_right.png`  
   - アイキャッチ: `assets/eyecatch/eyecatch.png`（任意）

4. サンプル実行  
   ```bash
   ./.venv/bin/python adv_maker.py --script scripts/sample_script.json --out output/output.mp4 --srt output/subtitles.srt
   ```

5. Filmoraへインポート  
   - `output/output.mp4` と `output/subtitles.srt` を読み込み

## 台本スキーマ（拡張可能アクション）

- ルートは **Scene配列**（`scripts/sample_script.json` は簡略形としてScene配列のみを書いています）
- Scene:
  - `bg`: 背景画像パス（Scene内で省略時は前Sceneを継承）
  - `lines`: Line配列

- Line:
  - `character`: キャラ画像パス（PNG推奨・透過OK）
  - `position`: `left` | `right`（`config.LAYOUT`で位置やスケールを調整）
  - `voice`: VOICEVOXスピーカーID
  - `text`: セリフ
  - `action`: 文字列 / 1個のdict / dict配列（拡張可）
    - 共通キー：`type`, `start`, `duration`, `strength`, `x`, `y`, `mode`, `easing`

### 既定アクション
- `fadein`, `fadeout` : 不透明度を補間
- `jump` : 簡易ジャンプ（サイン波）
- `zoomin`, `zoomout` : クリップのスケールを変更
- `move` : 相対/絶対移動

新しいアクションは `scripts/actions.py` に追記すればOKです。

## よくあるQ&A

- **字幕は焼き込み？**
  - いいえ、`.srt` を別出力。Filmoraで字幕トラックとして読み込んでください。

- **OpenShotを使いたい**
  - MoviePyベースですが、OpenShotのPython APIへ移植もしやすい構造です（`actions.py` の座標/不透明度/スケール制御をOpenShot APIに置換）。

- **音声タイミングはどう決めてる？**
  - 行ごとにTTS生成し、実際のwav長 + 少しの余白でタイミングを連結。

- **左右以外の配置は？**
  - `config.LAYOUT` にプリセットを追加して `position` に名前を書くだけで増やせます。

## ライセンス
MIT（必要に応じて変更してください）

bash run_project.sh

./.venv/bin/python scripts/timeline_to_projects.py   --dsl projects/Test/scripts/timeline.txt   --project projects/Test   --adv-out projects/Test/scripts/script.json   --osp-out projects/Test/openshot/project.osp   --fps 30 --width 1920 --height 1080   --path-mode winabs   --win-root "D:\src\adv_video_project\projects\Test"   --assets-layout categorized   --subtitle-font "/mnt/c/Windows/Fonts/meiryo.ttc"   --subtitle-font-size 60   --subtitle-bottom 94   --plate-image "assets/ui/plate.png"