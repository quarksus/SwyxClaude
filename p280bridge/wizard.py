"""First-run setup wizard: dependencies, phone access, models, and hardware checks."""
import os
import shutil
import subprocess
import sys
import threading
import time

from . import audio, config, hid

# Must sort before 73-seat-late.rules, which turns the uaccess tag into a per-user ACL.
# GROUP=plugdev is a fallback for sessions without a logind seat.
UDEV_RULE = ('KERNEL=="hidraw*", ATTRS{idVendor}=="2603", ATTRS{idProduct}=="0280", '
             'GROUP="plugdev", MODE="0660", TAG+="uaccess"\n'
             '# raw USB access, used by the direct-USB audio mode\n'
             'SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="2603", ATTR{idProduct}=="0280", '
             'GROUP="plugdev", MODE="0660", TAG+="uaccess"\n')
UDEV_FILE = "/etc/udev/rules.d/70-swyx-p280.rules"
OLD_UDEV_FILE = "/etc/udev/rules.d/99-swyx-p280.rules"  # earlier versions: ran too late to work
TOOLS = {"pactl": "pulseaudio-utils", "pw-play": "pipewire-bin", "parec": "pulseaudio-utils",
         "claude": "Claude Code (https://claude.com/claude-code)"}


def ask(question: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        a = input(f"{question} {hint} ").strip().lower()
        if not a:
            return default
        if a in ("y", "yes", "j", "ja"):
            return True
        if a in ("n", "no", "nein"):
            return False


def step(n, title):
    print(f"\n[{n}] {title}")


def check_tools():
    step(1, "Checking required tools")
    missing = [t for t in TOOLS if not shutil.which(t)]
    for t in TOOLS:
        print(f"  {'ok     ' if t not in missing else 'MISSING'} {t}")
    if missing:
        print("\nInstall the missing tools (Debian/Ubuntu: sudo apt install "
              + " ".join(sorted({TOOLS[t] for t in missing if t != 'claude'})) + ") and run again.")
        sys.exit(1)


def wait_for(cond, seconds, msg):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.5)
    print(msg)
    return False


def detect_phone():
    step(2, "Looking for the Swyx P280")
    while not hid.find_hidraw():
        input("  Phone not found. Plug the Swyx P280 into USB, then press Enter... ")
    print(f"  Found phone at {hid.find_hidraw()}")


def can_open() -> bool:
    try:
        os.close(os.open(hid.find_hidraw(), os.O_RDWR))
        return True
    except OSError:
        return False


def setup_access():
    step(3, "Phone access permissions")
    try:
        up_to_date = open(UDEV_FILE).read() == UDEV_RULE
    except OSError:
        up_to_date = False
    if can_open() and up_to_date:
        print("  Already allowed.")
        return
    print("  Your user may not access the phone's button/ring interface yet.")
    print("  This installs a udev rule (needs sudo):\n   ", UDEV_FILE)
    if not ask("  Install it now?"):
        sys.exit("Cannot continue without access to the phone.")
    subprocess.run(["sudo", "rm", "-f", OLD_UDEV_FILE], check=True)
    subprocess.run(["sudo", "tee", UDEV_FILE], input=UDEV_RULE.encode(), check=True,
                   stdout=subprocess.DEVNULL)
    subprocess.run(["sudo", "udevadm", "control", "--reload"], check=True)
    subprocess.run(["sudo", "udevadm", "trigger"], check=True)
    time.sleep(2)
    if not can_open():
        input("  Unplug the phone, plug it back in, then press Enter... ")
        if not wait_for(lambda: hid.find_hidraw() and can_open(), 15, "  Still no access."):
            dev = hid.find_hidraw()
            subprocess.run(["ls", "-l", dev])
            sys.exit(f"Setup failed: still no permission for {dev}. Is your user in the 'plugdev' "
                     f"group (`groups`)? Check {UDEV_FILE}.")
    print("  Access granted.")


def download_models(cfg):
    step(4, "Downloading speech models (one-time, ~700 MB)")
    config.PIPER_DIR.mkdir(parents=True, exist_ok=True)
    if not all((config.PIPER_DIR / f"{v}.onnx").exists() for v in config.VOICES):
        print("  Text-to-speech voices (English, German)...")
        subprocess.run([sys.executable, "-m", "piper.download_voices", "--data-dir",
                        str(config.PIPER_DIR), *config.VOICES], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"  Speech recognition model '{cfg.whisper_model}'...")
    from .stt import Transcriber
    Transcriber(cfg).load()
    print("  Models ready.")


def test_speaker(cfg):
    step(5, "Speaker test")
    speaker = audio.Speaker(audio.find_node("sinks", cfg.device_match))
    while True:
        print("  Playing 3 beeps (lift the handset if you hear nothing)...")
        speaker.tone([440, 660, 880], seconds=0.4)
        if ask("  Did you hear them on the phone?"):
            return
        if not ask("  Try again?"):
            print("  Continuing, but check your audio setup.")
            return


def test_hook(cfg) -> hid.Phone:
    step(6, "Hook switch test")
    events = []
    phone = hid.Phone(lambda off: events.append(off), invert_hook=False)
    phone.start()
    input("  Put the handset on the cradle, then press Enter... ")
    del events[:]
    print("  Now LIFT the handset...")
    if not wait_for(lambda: events, 30, "  No event seen."):
        sys.exit("The hook switch was not detected. Run `p280-bridge monitor` to debug.")
    invert = events[0] is False
    if invert:
        print("  Your phone reports the hook state inverted; compensating.")
    config.save(invert_hook=invert)
    phone.invert_hook = invert
    phone.offhook = True
    print("  Hook switch works.")
    return phone


def listen_once(cfg, phone, source, stt):
    deadline = time.time() + 20
    stats, chunks = {}, []
    audio.capture_session(
        source, cfg, lambda: bool(chunks) or time.time() > deadline or not phone.offhook,
        chunks.append, stats=stats)
    if not chunks:
        why = "the handset was put down" if not phone.offhook and time.time() < deadline else "timed out"
        print(f"  Heard nothing ({why}). Loudest sound: {stats.get('peak', 0):.0f}, "
              f"needed above: {stats.get('threshold', 0):.0f}.")
        return None
    text, lang = stt.transcribe(chunks[0])
    print(f"  I understood ({lang}): \"{text}\"")
    return text


def choose_other_microphone(cfg, phone, stt):
    sources = audio.list_sources()
    print("  The phone's microphone delivered no audio. Available microphones:")
    for i, name in enumerate(sources, 1):
        print(f"   {i}. {name}")
    while True:
        choice = input("  Pick a number to use instead (Enter to skip): ").strip()
        if not choice:
            return
        try:
            name = sources[int(choice) - 1]
        except (ValueError, IndexError):
            continue
        print("  Say a short sentence...")
        if listen_once(cfg, phone, name, stt):
            config.save(mic_match=name)
            print("  Saved. The phone still handles the hook, ring and speaker.")
            return
        print("  Nothing heard from that microphone either.")


def try_usb_mode(cfg, phone, stt) -> bool:
    """Test the phone's mic + speaker through direct USB; save audio_backend = "usb" if it works."""
    print("  Trying the phone's microphone directly over USB (the Linux sound driver cannot read it)...")
    cfg.audio_backend = "usb"
    try:
        io = audio.open_audio(cfg)
    except Exception as e:
        print(f"  Direct USB mode failed: {e}")
        cfg.audio_backend = "pipewire"
        return False
    ok = False
    try:
        print("  Say a short sentence (handset lifted)...")
        if listen_once(cfg, phone, io.mic, stt) is not None:
            print("  Now a beep on the phone speaker...")
            io.speaker.tone([440, 660, 880], seconds=0.4)
            ok = ask("  Did you hear it?")
    finally:
        io.close()
    if ok:
        config.save(audio_backend="usb", mic_match="")
        print("  Saved: the bridge will use the phone directly over USB.")
    else:
        cfg.audio_backend = "pipewire"
    return ok


def test_microphone(cfg, phone):
    step(7, "Microphone test (keep the handset lifted)")
    from .stt import Transcriber
    stt = Transcriber(cfg)
    cfg.mic_match = ""  # test the phone's own microphone first
    if cfg.audio_backend == "usb":
        io = audio.open_audio(cfg)
        try:
            print("  Say a short sentence, e.g. \"Hello Claude, can you hear me?\"")
            if listen_once(cfg, phone, io.mic, stt) is not None:
                return
        finally:
            io.close()
        cfg.audio_backend = "pipewire"
    else:
        source = audio.find_node("sources", cfg.device_match)
        print("  Say a short sentence, e.g. \"Hello Claude, can you hear me?\"")
        text = listen_once(cfg, phone, source, stt)
        if text is not None:
            config.save(mic_match="")
            if not ask("  Is that about right?"):
                print("  Tip: set whisper_model = \"small\" in", config.CONFIG_FILE)
            return
    if try_usb_mode(cfg, phone, stt):
        return
    choose_other_microphone(cfg, phone, stt)


def test_ring(cfg, phone):
    step(8, "Ring test")
    print("  Put the handset back on the cradle.")
    wait_for(lambda: not phone.offhook, 30, "  (still lifted — continuing)")
    input("  Press Enter and the phone should ring once... ")
    try:
        phone.ring(cfg.ring_seconds)
        rang = ask("  Did the phone ring?")
    except OSError as e:
        print(f"  Ring command failed: {e}")
        rang = False
    if rang:
        config.save(ring_method="hid")
    else:
        print("  Using a ring tone through the phone speaker instead.")
        config.save(ring_method="tone")


def run():
    print("=== Swyx P280 + Claude Code: first-time setup ===")
    if not sys.stdin.isatty():
        sys.exit("Setup is interactive; run `p280-bridge init` in a terminal.")
    check_tools()
    detect_phone()
    setup_access()
    cfg = config.load()
    download_models(cfg)
    test_speaker(cfg)
    phone = test_hook(cfg)
    cfg = config.load()
    try:
        test_microphone(cfg, phone)
        test_ring(cfg, phone)
    finally:
        phone.close()
    config.INIT_MARKER.parent.mkdir(parents=True, exist_ok=True)
    config.INIT_MARKER.write_text(str(time.time()))
    print("\nSetup complete! Start with:  p280-bridge run")
