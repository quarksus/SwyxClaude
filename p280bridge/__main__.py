"""CLI: `p280bridge run [claude args]`, `monitor`, `probe-ring`, `devices`, `notify`."""
import json
import logging
import os
import shutil
import sys
import time

from . import config, notify


def setup_logging():
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=config.STATE_DIR / "bridge.log", level=logging.DEBUG if os.environ.get("BRIDGE_DEBUG") else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def cmd_run(args):
    from .bridge import Bridge
    from .ptyhost import PtyHost
    cfg = config.load()
    setup_logging()
    claude = shutil.which("claude")
    if not claude:
        sys.exit("claude CLI not found in PATH")
    config.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    settings = config.RUNTIME_DIR / "claude-settings.json"
    hook_cmd = f"PYTHONPATH={config.PROJECT_DIR} {sys.executable} -m p280bridge notify"
    settings.write_text(json.dumps(notify.hooks_settings(hook_cmd)))
    host = PtyHost([claude, "--settings", str(settings), *args])
    try:
        bridge = Bridge(cfg, host)
        bridge.phone.start()  # fail early, before taking over the terminal
    except RuntimeError as e:
        sys.exit(str(e))
    import threading
    t = threading.Thread(target=bridge.run_forever, daemon=True, name="bridge")
    t.start()
    try:
        return host.run()
    finally:
        bridge.close()


def cmd_monitor(_):
    from .hid import Phone
    p = Phone(lambda off: print("OFF-HOOK" if off else "on-hook", flush=True),
              lambda b: print("button:", b, flush=True))
    p.start()
    print(f"offhook={p.offhook}; press buttons / lift handset (Ctrl+C to quit)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        p.close()


def cmd_probe_ring(_):
    """Pulse each LED bit of the output report so you can see/hear which one rings."""
    from .hid import Phone
    p = Phone(lambda o: None)
    p.start()
    for bit, name in enumerate(["ring", "microphone", "speaker", "headset"]):
        print(f"bit {bit} ({name}) ON for 2 s ...", flush=True)
        p.set_leds(1 << bit)
        time.sleep(2)
        p.set_leds(0)
        time.sleep(0.5)
    p.close()
    print("If the phone never rang, set ring_method = \"tone\" in", config.CONFIG_FILE)


def cmd_devices(_):
    from . import audio, hid
    print("hidraw:", hid.find_hidraw())
    cfg = config.load()
    print("source:", audio.find_node("sources", cfg.device_match))
    print("sink:  ", audio.find_node("sinks", cfg.device_match))


COMMANDS = {"run": cmd_run, "monitor": cmd_monitor, "probe-ring": cmd_probe_ring, "devices": cmd_devices}


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] == "notify":
        return notify.client_main()
    if argv[0] not in COMMANDS:
        print(__doc__)
        return 2
    return COMMANDS[argv[0]](argv[1:]) or 0


if __name__ == "__main__":
    sys.exit(main())
