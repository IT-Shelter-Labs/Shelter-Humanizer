# Sources and runtime notices

Shelter Humanizer's application code is original. Its editorial instructions were
refined with contributed editing briefs and feedback. We studied
these MIT-licensed projects as references for editorial and product principles;
their source code and complete skill texts are not bundled or copied:

- [blader/humanizer](https://github.com/blader/humanizer), © 2025 Siqi Chen;
  [license at reviewed commit](https://github.com/blader/humanizer/blob/225a6f39ac85f76ee48dbad772ea4abe4ed6c9d8/LICENSE).
- [ilyautov/humanizer-ru](https://github.com/ilyautov/humanizer-ru), © 2026 Ilya Utov;
  [license at reviewed commit](https://github.com/ilyautov/humanizer-ru/blob/1e035ad6b64c56c6e770ed28d076e2cbda3d1aed/LICENSE).
- [rudra496/StealthHumanizer](https://github.com/rudra496/StealthHumanizer),
  © 2025–2026 Rudra Sarker;
  [license at reviewed commit](https://github.com/rudra496/StealthHumanizer/blob/3b307af7ae9f533afe9238940d7ced8a20c23b5a/LICENSE).

Detailed adopt/reject decisions: [research](docs/RESEARCH.md).
These authors do not endorse or maintain Shelter Humanizer.

The IT Shelter shield in `src/shelter_humanizer/assets/it-shelter-logo.png` is a
brand asset supplied by IT Shelter for this application. It is included unchanged.
The MIT software license does not grant rights to impersonate IT Shelter or imply endorsement.
The `.ico` asset is a resized format conversion of the same shield for the Windows executable.

The optional local setup lists these separately downloaded models:

- DeepSeek-R1-0528-Qwen3-8B: MIT according to the
  [official DeepSeek model card](https://huggingface.co/deepseek-ai/DeepSeek-R1-0528-Qwen3-8B).
  Its base model is Qwen3 8B, not Qwen 2.5. We use the
  [Ollama Q4_K_M tag](https://ollama.com/library/deepseek-r1:8b-0528-qwen3-q4_K_M).
- Qwen 3.5 4B and 9B: Apache 2.0 according to the official
  [4B](https://huggingface.co/Qwen/Qwen3.5-4B) and [9B](https://huggingface.co/Qwen/Qwen3.5-9B) cards.
- Qwen 3 1.7B: Apache 2.0 according to the
  [official model card](https://huggingface.co/Qwen/Qwen3-1.7B).
Model weights and Ollama are not bundled in our executable. The Windows wizard downloads
the official [Ollama v0.35.1 portable archive](https://github.com/ollama/ollama/releases/tag/v0.35.1)
after explicit confirmation, verifies its pinned SHA256 and keeps its accompanying notices.
[Ollama is MIT licensed](https://github.com/ollama/ollama/blob/v0.35.1/LICENSE);
libraries in its official archive retain their own licenses. The owned Ollama instance
downloads the selected model; model licenses remain applicable.

The source application uses Python's standard library and Tk. Windows ZIPs include
the Python runtime (PSF license), Tcl/Tk (their permissive licenses), and the
PyInstaller bootloader (GPL with the distribution exception). Their complete
license files accompany the executable in `licenses/`. The application itself
remains MIT licensed. PyInstaller and Pillow are development tools; Pillow is not
a runtime dependency and is not included in the application.
