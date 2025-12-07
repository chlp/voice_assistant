# Voice Assistant for Orange Pi (Bluetooth + Whisper + Llama.cpp + Piper)

This repository contains configuration files and a Python-based voice assistant designed to run on **Orange Pi** (or similar ARM boards).  
The assistant records audio via Bluetooth, performs speech-to-text using **faster-whisper**, sends queries to **Llama.cpp**, and replies via **Piper TTS**.

---

## 1. Install the Python script

Create the directory:

```bash
mkdir -p /home/orangepi/voice_assistant
```

Place the main script here:
```bash
/home/orangepi/voice_assistant/voice_assistant.py
```

Install dependencies (TODO)

You must install all libraries imported in the script, including:
```bash
evdev
requests
numpy
faster-whisper
webrtcvad (if used)
sox
aplay (ALSA)
```

A full dependency list will be added later.

⸻

2. Install the systemd services

Move the provided service files:
```bash
llama.service
voice-assistant.service
```

into:
```
sudo mv llama.service /etc/systemd/system/
sudo mv voice-assistant.service /etc/systemd/system/
```

⸻

3. Reload systemd

```bash
sudo systemctl daemon-reload
```

⸻

4. Start the Llama server manually

```bash
sudo systemctl start llama.service
sudo systemctl start voice-assistant.service
```

Check the status:

```bash
sudo systemctl status llama.service
sudo systemctl status voice-assistant.service
```

⸻

5. Enable autostart at boot

```bash
sudo systemctl enable llama.service
sudo systemctl enable voice-assistant.service
```

⸻

6. View logs

Follow logs in realtime:

```bash
journalctl -u llama.service -f
journalctl -u voice-assistant.service -f
```

⸻

Notes
	•	Ensure audio devices, Bluetooth profiles, and Python environment are configured correctly.
	•	Llama.cpp must be built manually and located at the paths referenced in llama.service.
	•	Piper TTS and the selected voice model must be available at the paths used in the Python script.
