#!/usr/bin/env python3
import subprocess
import time
import json
import requests
import webrtcvad
import wave
import struct
from evdev import InputDevice, categorize, ecodes

# -----------------------------
# CONFIG
# -----------------------------

# button AVRCP
EVENT_DEVICE = "/dev/input/event6"
PLAY_SCANCODES = {200, 201}

# bluetooth card
BT_CARD = "bluez_card.00_22_BB_A1_9F_19"

# piper voices
PIPER_BIN = "/home/orangepi/piper/piper"
TTS_VOICE = "/opt/piper/voices/ru_RU-irina-medium.onnx"

# file paths
RECORD_WAV = "/tmp/query.wav"
STT_TEXT = "/tmp/query.txt"
TTS_OUT = "/tmp/answer.wav"

# whisper
WHISPER_BIN = "/home/orangepi/whisper.cpp/build/bin/whisper-cli"
# WHISPER_MODEL = "/home/orangepi/whisper-models/ggml-small-q5_1.bin" # biggest
WHISPER_MODEL = "/home/orangepi/whisper-models/ggml-base-q5_1.bin" # medium
# WHISPER_MODEL = "/home/orangepi/whisper-models/ggml-tiny-q8_0.bin" # smallest

# llama server endpoint
LLAMA_URL = "http://127.0.0.1:8080/v1/chat/completions"


# -----------------------------
# HELPERS
# -----------------------------

def bt_set_profile(profile):
    print(f"[BT] Set profile: {profile}")
    subprocess.run([
        "pactl", "set-card-profile", BT_CARD, profile
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def start_recording():
    print("[REC] Start")
    return subprocess.Popen([
        "parec",
        "--device=bluez_source.00_22_BB_A1_9F_19.handsfree_head_unit",
        "--rate=16000",
        "--format=s16le",
        "--channels=1"
    ], stdout=open(RECORD_WAV, "wb"))


def do_stt():
    print("[STT] Whisper...")
    r = subprocess.run([
        WHISPER_BIN,
        "-m", WHISPER_MODEL,
        "-f", RECORD_WAV,
        "-l", "auto",
        "--threads", "8",
        "-otxt"
    ], text=True, capture_output=True)
    print("[WHISPER STDERR]", r.stderr)
    print("[WHISPER STDOUT]", r.stdout)
    
    # whisper produces RECORD_WAV.txt
    with open(RECORD_WAV + ".txt", "r") as f:
        text = f.read().strip()
    with open(STT_TEXT, "w") as f:
        f.write(text)
    print("[STT] Text:", text)
    return text


def ask_llama(prompt: str) -> str:
    print("[LLM] Sending prompt...")

    data = {
        "model": "qwen2.5-1.5b-instruct",
        "messages": [
            {
                "role": "system",
                "content": "You are a multilingual assistant. Respond briefly and to the point, without describing your reasoning."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "max_tokens": 200,
        "temperature": 0.1,
    }

    r = requests.post(LLAMA_URL, json=data, timeout=60)
    if r.status_code != 200:
        print("[LLM] HTTP error:", r.status_code, r.text)
        return ""

    j = r.json()

    try:
        text = j["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print("[LLM] Bad response format:", e, j)
        text = ""

    print("[LLM] Answer:", text)
    return text


def tts_speak(text):
    print("[TTS] Piper synth...")
    p = subprocess.Popen([
        PIPER_BIN,
        "--model", TTS_VOICE,
        "--output_file", TTS_OUT
    ], stdin=subprocess.PIPE, text=True)
    p.communicate(text)
    print("[TTS] Play...")
    subprocess.run(["aplay", TTS_OUT])

def record_until_silence():
    print("[REC] Start with VAD...")

    # Configure VAD
    vad = webrtcvad.Vad()
    vad.set_mode(2)

    # 16 kHz mono 16bit
    sample_rate = 16000
    frame_duration = 30  # ms
    frame_size = int(sample_rate * frame_duration / 1000) * 2  # bytes

    # Start parec
    p = subprocess.Popen([
        "parec",
        "--device=bluez_source.00_22_BB_A1_9F_19.handsfree_head_unit",
        "--rate=16000",
        "--format=s16le",
        "--channels=1"
    ], stdout=subprocess.PIPE)

    audio_data = bytearray()
    silence_start = time.time()

    while True:
        frame = p.stdout.read(frame_size)
        if not frame:
            break

        audio_data.extend(frame)

        # VAD expects bytes of 16-bit samples
        is_speech = vad.is_speech(frame, sample_rate)

        if is_speech:
            silence_start = time.time()
        else:
            if time.time() - silence_start > 1.0:
                print("[REC] Silence detected -> stopping")
                break

    p.terminate()

    # Save WAV
    with wave.open(RECORD_WAV, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(audio_data))

    print("[REC] File saved:", RECORD_WAV)

# -----------------------------
# MAIN LOOP
# -----------------------------

def main():
    print("[INFO] Starting voice assistant...")

    # ------------------------------
    # FORCE A2DP AT STARTUP
    # ------------------------------
    print("[BT] Forcing SPACE into A2DP mode...")

    subprocess.run(["pactl", "set-card-profile", BT_CARD, "off"])
    time.sleep(0.4)

    subprocess.run(["pactl", "set-card-profile", BT_CARD, "a2dp_sink"])
    time.sleep(1.0)

    print("[BT] Current profile is:")
    subprocess.run(["pactl", "get-card-profile", BT_CARD])

    # ------------------------------
    # PREPARE INPUT DEVICE
    # ------------------------------
    dev = InputDevice(EVENT_DEVICE)
    print("[DEBUG] Using input device:", dev)

    recording_proc = None
    is_recording = False

    print("[INFO] Waiting for button presses...")

    last_press_time = 0

    for event in dev.read_loop():
        if event.type == ecodes.EV_KEY:
            key = categorize(event)
            print("DEBUG:", key.keycode, key.scancode, key.keystate)

            # PRESS detected
            if key.scancode in PLAY_SCANCODES and key.keystate == 1:
                print("[BTN] Press ? start recording")

                bt_set_profile("handsfree_head_unit")
                time.sleep(0.8)

                record_until_silence()

                text = do_stt()
                answer = ask_llama(text)

                bt_set_profile("a2dp_sink")
                time.sleep(0.7)

                tts_speak(answer)
                print("[INFO] Ready for next request")

        # ------------------------------------------
        # HOLD-TO-TALK RELEASE DETECTED BY TIMEOUT
        # ------------------------------------------
        if is_recording:
            if time.time() - last_press_time > 0.5:
                print("[BTN] HOLD ended ? stopping recording")

                is_recording = False
                if recording_proc:
                    recording_proc.terminate()
                    recording_proc = None

                print("[REC] Processing STT...")
                text = do_stt()
                print("[REC] STT OK:", text)

                print("[LLM] Processing LLM...")
                answer = ask_llama(text)

                print("[BT] Switching back to A2DP for playback...")
                bt_set_profile("a2dp_sink")
                time.sleep(0.7)

                print("[TTS] Speaking answer...")
                tts_speak(answer)

                print("[INFO] Ready for next press")

if __name__ == "__main__":
    main()
