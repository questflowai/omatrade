"""The desktop side: notifications, links, and nudging the bar.

Every subprocess gets an argument list, never a shell string built from
data; the one `sh -c` below is a fixed script that reads the URL and signal
id from the environment.
"""

import os
import shutil
import subprocess
from pathlib import Path

from . import policy, urls
from .config import PLUGIN_ID

ICON = Path(__file__).resolve().parent.parent.parent / "assets" / "omatrade.svg"
CLI = Path(__file__).resolve().parent.parent.parent / "bin" / "omatrade"


def notification_command(signal, icon=ICON):
    tier = signal.get("tier", "info")
    urgency = policy.URGENCY.get(tier, "low")
    symbols = " · ".join(signal.get("symbols", [])[:3])
    source = (signal.get("source") or {}).get("name") or "Questflow"
    title = (symbols + "  " if symbols else "") + source
    body = signal.get("title", "")
    if tier == "critical":
        title = "⚠ " + title
    cmd = ["notify-send", "--app-name=Omatrade", f"--urgency={urgency}",
           "--category=omatrade." + tier]
    # Critical stays until dismissed; the others use the notifier's default.
    if tier == "critical":
        cmd.append("--expire-time=0")
    if signal.get("url"):
        cmd.append("--action=open=Open")
    cmd.append("--wait")
    if icon and Path(icon).exists():
        cmd.append(f"--icon={icon}")
    # `--` so a title starting with "-" is never read as an option.
    cmd += ["--", title, body]
    return cmd


# Waits for the click, opens the link, and acknowledges the signal so other
# devices stop showing it as unread. Data only ever arrives via environment.
CLICK_SCRIPT = ('choice=$("$@"); if [ "$choice" = open ]; then '
                '[ -n "$OMATRADE_URL" ] && xdg-open "$OMATRADE_URL" >/dev/null 2>&1; '
                '"$OMATRADE_CLI" ack -- "$OMATRADE_SIGNAL_ID" >/dev/null 2>&1; fi')


def send_notification(signal, popen=subprocess.Popen, which=shutil.which):
    if not which("notify-send"):
        return False
    cmd = notification_command(signal)
    env = dict(os.environ, OMATRADE_URL=urls.safe_url(signal.get("url")),
               OMATRADE_SIGNAL_ID=signal.get("id", ""), OMATRADE_CLI=str(CLI))
    # Detached: --wait holds until the notification is clicked or dismissed.
    popen(["sh", "-c", CLICK_SCRIPT, "notify"] + cmd, env=env,
          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
          stderr=subprocess.DEVNULL, start_new_session=True)
    return True


def poke_bar():
    if not shutil.which("omarchy-shell"):
        return
    try:
        subprocess.run(["omarchy-shell", "-q", PLUGIN_ID, "refresh"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
    except (OSError, subprocess.SubprocessError):
        pass


def open_url(url, popen=subprocess.Popen):
    url = urls.safe_url(url)
    if not url:
        return False
    popen(["xdg-open", url], stdout=subprocess.DEVNULL,
          stderr=subprocess.DEVNULL, start_new_session=True)
    return True
