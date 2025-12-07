#!/usr/bin/env python3
import subprocess
import time
import requests
import wave
from datetime import datetime

from evdev import InputDevice, categorize, ecodes
from faster_whisper import WhisperModel
import numpy as np

# -----------------------------
# LOGGING
# -----------------------------


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


# -----------------------------
# CONFIG
# -----------------------------

# AVRCP button
EVENT_DEVICE = "/dev/input/event6"
PLAY_SCANCODES = {200, 201}

# Bluetooth card
BT_CARD = "bluez_card.00_22_BB_A1_9F_19"
BT_SOURCE = "bluez_source.00_22_BB_A1_9F_19.handsfree_head_unit"

# Piper TTS
PIPER_BIN = "/home/orangepi/piper/piper"
TTS_VOICE = "/opt/piper/voices/en_US-amy-low.onnx"
# TTS_VOICE = "/opt/piper/voices/ru_RU-irina-medium.onnx"

# File paths
RECORD_WAV = "/home/orangepi/tmp/query.wav"
TTS_OUT = "/home/orangepi/tmp/answer.wav"

# Llama server endpoint
LLAMA_URL = "http://127.0.0.1:8080/v1/chat/completions"

# -----------------------------
# FASTER-WHISPER CONFIG
# -----------------------------

# Can be tiny/small/medium/large-v3 etc.
WHISPER_MODEL_ID = "Systran/faster-whisper-small"

# device="cpu" — if no GPU; compute_type="int8" / "int8_float16" saves resources
log("[STT] Loading Whisper model...")
whisper_model = WhisperModel(
    WHISPER_MODEL_ID,
    device="cpu",
    compute_type="int8",
)


# -----------------------------
# HELPERS
# -----------------------------


def bt_set_profile(profile: str) -> None:
    log(f"[BT] Set profile: {profile}")
    subprocess.run(
        ["pactl", "set-card-profile", BT_CARD, profile],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def record_until_silence() -> None:
    cmd = [
        "sox",
        "-t",
        "pulseaudio",
        BT_SOURCE,
        "-r",
        "16000",
        "-c",
        "1",
        "-b",
        "16",
        "-e",
        "signed-integer",
        RECORD_WAV,
        "silence",
        "1",
        "0.1",
        "3%",
        "1",
        "1.5",
        "3%",
    ]
    log("[REC] Running: " + " ".join(cmd))
    subprocess.run(cmd)
    log("[REC] Done.")


def do_stt() -> str:
    """
    Simple sequential speech recognition from the recorded WAV file.
    """
    log("[STT] faster-whisper...")

    # Read full WAV file
    with wave.open(RECORD_WAV, "rb") as wf:
        n_channels = wf.getnchannels()
        rate = wf.getframerate()
        frames = wf.getnframes()

        audio_sec = frames / float(rate) if rate > 0 else 0
        log(f"[STT] Audio: {audio_sec:.2f}s, {rate} Hz, {n_channels} ch")

        raw = wf.readframes(frames)

    # Convert int16 -> float32 in range [-1, 1]
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

    # If stereo, take only left channel
    if n_channels == 2:
        audio = audio[0::2]

    # Transcribe
    t0 = time.time()
    segments, info = whisper_model.transcribe(
        audio,
        beam_size=1,
        language="en",
        vad_filter=True,
    )

    text = "".join(seg.text for seg in segments).strip()
    dt = time.time() - t0

    log(f"[STT] Done in {dt:.2f}s: {text!r}")
    return text


def ask_llama(prompt: str) -> str:
    log("[LLM] Sending prompt...")

    data = {
        "model": "qwen2.5-1.5b-instruct",
        "messages": [
            {
                "role": "system",
                "content": "Answer with short messages in english",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "max_tokens": 200,
        "temperature": 0.1,
    }

    try:
        r = requests.post(LLAMA_URL, json=data, timeout=60)
    except requests.RequestException as e:
        log(f"[LLM] Request error: {e}")
        return ""

    if r.status_code != 200:
        log(f"[LLM] HTTP error: {r.status_code} {r.text}")
        return ""

    try:
        j = r.json()
        text = j["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log(f"[LLM] Bad response format: {e} {j}")
        text = ""

    log("[LLM] Answer: " + text)
    return text


def tts_speak(text: str) -> None:
    log("[TTS] Piper synth...")
    p = subprocess.Popen(
        [
            PIPER_BIN,
            "--model",
            TTS_VOICE,
            "--output_file",
            TTS_OUT,
        ],
        stdin=subprocess.PIPE,
        text=True,
    )
    p.communicate(text)
    log("[TTS] Play...")
    subprocess.run(["aplay", TTS_OUT])


# -----------------------------
# MAIN LOOP
# -----------------------------


def main() -> None:
    log("[INFO] Starting voice assistant...")

    # Force A2DP at startup
    log("[BT] Forcing device into A2DP mode...")

    bt_set_profile("off")
    time.sleep(0.4)

    bt_set_profile("a2dp_sink")
    time.sleep(1.0)

    log("[BT] Current profile is:")
    subprocess.run(["pactl", "get-card-profile", BT_CARD])

    # Prepare input device
    dev = InputDevice(EVENT_DEVICE)
    log(f"[INFO] Using input device: {dev}")
    log("[INFO] Waiting for button presses...")

    for event in dev.read_loop():
        if event.type != ecodes.EV_KEY:
            continue

        key = categorize(event)
        log(f"[DEBUG] {key.keycode} {key.scancode} {key.keystate}")

        if key.scancode in PLAY_SCANCODES and key.keystate == 1:
            log("[BTN] Press → start recording")

            bt_set_profile("handsfree_head_unit")
            time.sleep(0.8)

            # 1) Record new audio
            record_until_silence()

            # 2) Transcribe recorded audio
            text = do_stt()
            if not text:
                log("[STT] Empty text, skipping LLM")
                continue

            # 3) Ask LLM
            answer = ask_llama(text)
            if not answer:
                log("[LLM] Empty answer, skipping TTS")
                continue

            bt_set_profile("a2dp_sink")
            time.sleep(0.7)

            # 4) Speak answer
            tts_speak(answer)
            log("[INFO] Ready for next request")


if __name__ == "__main__":
    main()
