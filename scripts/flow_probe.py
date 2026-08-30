import time
import os
from pathlib import Path

import uiautomator2 as u2

from android_runner.adb import ADBClient

SERIAL = os.environ.get("ANDROID_RUNNER_SERIAL", "DEVICE_SERIAL")
ADB = os.environ.get("ANDROID_RUNNER_ADB", "adb")

adb = ADBClient(ADB, SERIAL)
adb.run("shell", "am", "force-stop", "com.tencent.wework")
adb.run("shell", "monkey", "-p", "com.tencent.wework", "1")
time.sleep(4)
d = u2.connect(SERIAL)
print("current", d.app_current())
for label in ["工作台", "智慧体育", "校园跑", "开始校园跑"]:
    element = d(text=label)
    print(label, "exists=", element.exists())
    if not element.exists():
        p = Path("logs") / f"flow-{label}"
        d.screenshot(str(p.with_suffix(".png")))
        p.with_suffix(".xml").write_text(d.dump_hierarchy(), encoding="utf-8")
        break
    element.click()
    time.sleep(3)
    print("after", label, d.app_current())
