# Dolmi privacy policy

Dolmi has no account, no telemetry and no analytics. **Speech recognition and translation run on your computer (PC or Mac). Your audio never leaves it.** That includes the microphone, when you switch the audio source to Microphone for in-person meetings: it is only recorded while you are captioning, and stays on this device unless you turn on cloud captions (below).

Dolmi only uses the network in the cases below. You start each one yourself.

## 1. Assistant and summaries — to the AI provider you chose (optional)

Live subtitles never need this. It only happens if you add an API key and then use the Assistant or Summarize, or press **Test** in Settings. Dolmi then sends a request to the provider you picked — Anthropic (Claude), OpenAI, Google (Gemini), NVIDIA or Cloudflare (Workers AI) — using your own key:

- **Assistant:** your question, the last few translated lines of the meeting (English text), your recent questions and answers in that chat, and the notes you wrote in Settings.
- **Summarize:** the English transcript of the meeting you summarize, and your notes.

That provider's own terms and privacy policy apply to this data: [Anthropic](https://www.anthropic.com/legal/privacy) · [OpenAI](https://openai.com/policies/privacy-policy/) · [Google Gemini API](https://ai.google.dev/gemini-api/terms) · [NVIDIA](https://www.nvidia.com/en-us/about-nvidia/privacy-policy/) · [Cloudflare Workers AI](https://developers.cloudflare.com/workers-ai/platform/data-usage/).

**To opt out:** don't add an API key. Dolmi then sends nothing to any AI provider, and subtitles work exactly the same.

## Cloud captions (optional, off by default)

If you set *Settings → Captions by* to Gemini, OpenAI or Cloudflare, Dolmi sends **the audio of what is being said** — every ~0.8 s while someone speaks (for the live line) and each finished sentence — plus your glossary terms and the previous sentence, to that provider with your own API key, and gets back the text (and, for Gemini, the translation). Audio is only sent while you are captioning and someone is speaking. That provider's terms and privacy policy apply. Leave *Captions by* on *This computer* and no audio ever leaves your device.

## 2. Speech and translation models — downloaded from Hugging Face

When you press **Download** in *Settings → This PC & speech models* (*This Mac & speech models* on a Mac), or agree to download when Start needs a model you don't have yet, Dolmi downloads its models from [Hugging Face](https://huggingface.co): the Whisper speech model and the Opus-MT translator for your language. This happens once per model. The download sends no personal data, only what any download sends, such as your IP address.

## 3. GPU add-on (optional, Windows) — downloaded from PyPI

If you tick **GPU speech recognition** in the installer, it downloads NVIDIA's CUDA libraries from [PyPI](https://pypi.org) once, during setup.

## What stays on your computer

| What | Windows | macOS |
|---|---|---|
| Settings, API keys, vocabulary, glossary | `%APPDATA%\Dolmi` (keys encrypted with Windows DPAPI) | `~/Library/Application Support/Dolmi` (keys encrypted with a secret in your login Keychain) |
| Transcripts, summaries, saved lines, Assistant chats | `Documents\Dolmi` (change it in Settings) | `~/Documents/Dolmi` (change it in Settings) |
| Log file | `%LOCALAPPDATA%\Dolmi\dolmi.log` | `~/Library/Logs/Dolmi/dolmi.log` |
| Speech and translation models | `%USERPROFILE%\.cache\huggingface` | `~/.cache/huggingface` |

- To stop saving, turn off **Save transcripts** in Settings.
- To delete old files and chats automatically, use **Delete after**.
- Uninstalling Dolmi (or moving Dolmi.app to the Bin) keeps your data. Delete the folders above to remove it.

**Recording other people:** transcribing a meeting processes the other participants' personal data. Tell them you are using live transcription. *(This is not legal advice.)*

## Contact

Questions about privacy: [open an issue](https://github.com/Zehnix/dolmi/issues).
