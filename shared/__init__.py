# shared/ — M1・M3 共通モジュール
#
# 元々 compe_M1/agents/ にあったファイルをプロジェクトルートに移動したもの。
# M1・M3 両方から import して使用する。
#
# 含まれるモジュール:
#   source_collector.py  — Phase 1: Gemini Search + nodriver による情報収集
#   summarizer.py        — Phase 1: 構造化要約
#   script_writer.py     — Phase 2: ナレーション原稿生成
#   image_generator.py   — Phase 2: インフォグラフィック画像生成
#   narrator.py          — Phase 3: TTS 音声生成
#   video_composer.py    — Phase 4: ffmpeg 動画合成
