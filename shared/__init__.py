# shared/ — Common modules for M1 and M3
#
# Originally located at compe_M1/agents/; moved to the project root.
# Imported by both M1 and M3.
#
# Modules:
#   source_collector.py  — Phase 1: Information collection via Gemini Search + nodriver
#   summarizer.py        — Phase 1: Structured summarization
#   script_writer.py     — Phase 2: Narration script generation
#   image_generator.py   — Phase 2: Infographic image generation
#   narrator.py          — Phase 3: TTS audio generation
#   video_composer.py    — Phase 4: Video composition via ffmpeg
#   language_utils.py    — Shared: BCP-47 language config loader
