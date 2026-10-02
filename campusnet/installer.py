# -*- coding: utf-8 -*-
"""安装/卸载系统级守护（需要管理员权限）。

注册方式按可靠性依次尝试三种，任意一种成功即可（不同 Windows 版本对计划任务
XML 的校验严格程度不一样）：
  1. PowerShell ScheduledTasks 模块
  2. schtasks /Create /XML
  3. schtasks /Create /RU SYSTEM /SC ONSTART
"""
from __future__ import annotations

import os
import shutil
import sys
import time

from .config import (BOOT_CONFIG, BOOT_DIR, BOOT_LOG, BOOT_STOP, BOOT_TASK,
                     INSTALL_LOG, build_boot_config, heartbeat_state,
                     load_boot_config, load_config, save_boot_config)
from .util import is_admin, read_tail, run_cmd


def _pythonw() -> str:
    exe = sys.executable or ""
    cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return cand if os.path.isfile(cand) else exe


def _entry_script() -> str:
    """项目入口（CampusNetAssistant.pyw 或 -m campusnet）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    pyw = os.path.join(root, "CampusNetAssistant.pyw")
    return pyw if os.path.isfile(pyw) else ""


def _task_xml(command: str, args: str, workdir: str) -> str:
    import time as _t
    return """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>CampusNetAssistant 系统级守护：开机/锁屏/未登录时保持校园网在线。</Description>
    <URI>\\%s</URI>
  </RegistrationInfo>
  <Triggers>
    <BootTrigger><Enabled>true</Enabled></BootTrigger>
    <TimeTrigger>
      <StartBoundary>%sT00:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition><Interval>PT5M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>S-1-5-18</UserId>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>5</Priority>
    <RestartOnFailure><Interval>PT1M</Interval><Count>3</Count></RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>%s</Command>
      <Arguments>%s</Arguments>
      <WorkingDirectory>%s</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
""" % (BOOT_TASK, _t.strftime("%Y-%m-%d"), command, args, workdir)


def _ps_register(command: str, args: str, workdir: str) -> str:
    return ("$ErrorActionPreference = 'Stop'\n"
            "$a = New-ScheduledTaskAction -Execute '%s' -Argument '%s' -WorkingDirectory '%s'\n"
            "$t1 = New-ScheduledTaskTrigger -AtStartup\n"
            "$t2 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1)"
            " -RepetitionInterval (New-TimeSpan -Minutes 5)\n"
            "$p = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount"
            " -RunLevel Highest\n"
            "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries"
            " -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew"
            " -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3"
            " -RestartInterval (New-TimeSpan -Minutes 1)\n"
            "Register-ScheduledTask -TaskName '%s' -Action $a -Trigger @($t1, $t2)"
            " -Principal $p -Settings $s -Force | Out-Null\n"
            "Write-Host 'ok'\n") % (command, args, workdir, BOOT_TASK)


def task_registered() -> bool:
    code, _ = run_cmd(["schtasks", "/query", "/tn", BOOT_TASK], timeout=30)
    return code == 0


def register_task(log=print):
    """返回 (是否成功, 说明)。"""
    pyw = _pythonw()
    script = _entry_script()
    if script:
        command, args = pyw, '"%s" --boot' % script
        workdir = os.path.dirname(script)
    else:
        command = pyw
        args = "-m campusnet --boot"
        workdir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log("  运行方式：%s %s" % (command, args))

    # 1) PowerShell 模块
    psfile = os.path.join(BOOT_DIR, "register_task.ps1")
    try:
        os.makedirs(BOOT_DIR, exist_ok=True)
        with open(psfile, "w", encoding="utf-8-sig") as fh:
            fh.write(_ps_register(command, args, workdir))
        code, out = run_cmd(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                             "-File", psfile], timeout=180)
        log("[1/3] PowerShell 模块 退出码=%s %s" % (code, (out or "").replace("\n", " ")[:160]))
        if code == 0 and task_registered():
            return True, "PowerShell 模块"
    except Exception as exc:  # noqa: BLE001
        log("[1/3] PowerShell 模块 异常：%s" % exc)

    # 2) schtasks /XML
    try:
        xmlfile = os.path.join(BOOT_DIR, "task.xml")
        with open(xmlfile, "w", encoding="utf-16") as fh:
            fh.write(_task_xml(command, args, workdir))
        code, out = run_cmd(["schtasks", "/Create", "/TN", BOOT_TASK, "/XML", xmlfile, "/F"],
                            timeout=90)
        log("[2/3] schtasks /XML 退出码=%s %s" % (code, (out or "").replace("\n", " ")[:160]))
        if code == 0:
            return True, "schtasks /XML"
    except Exception as exc:  # noqa: BLE001
        log("[2/3] schtasks /XML 异常：%s" % exc)

    # 3) schtasks 命令行
    try:
        tr = '"%s" %s' % (command, args)
        code, out = run_cmd(["schtasks", "/Create", "/TN", BOOT_TASK, "/TR", tr,
                             "/SC", "ONSTART", "/RU", "SYSTEM", "/RL", "HIGHEST", "/F"],
                            timeout=90)
        log("[3/3] schtasks /RU SYSTEM 退出码=%s %s" % (code, (out or "").replace("\n", " ")[:160]))
        if code == 0:
            return True, "schtasks /RU SYSTEM"
    except Exception as exc:  # noqa: BLE001
        log("[3/3] schtasks /RU SYSTEM 异常：%s" % exc)

    if task_registered():
        return True, "复查确认任务已存在"
    return False, "三种方式都失败"


def install_boot(cfg=None, log=print) -> int:
    log("=" * 62)
    log(" 安装系统级守护（开机 / 锁屏 / 未登录也保持校园网在线）")
    log("=" * 62)
    if not is_admin():
        log("× 需要管理员权限：请右键“以管理员身份运行”，或用 scripts/install-boot.cmd")
        return 1

    cfg = cfg or load_config()
    boot_cfg = build_boot_config(cfg)
    if not boot_cfg["password_machine"] and (boot_cfg.get("account") or ""):
        log("× 机器范围加密自检失败，取消安装。")
        return 1

    os.makedirs(BOOT_DIR, exist_ok=True)
    save_boot_config(boot_cfg)
    log("√ 系统级配置已写入：", BOOT_CONFIG)
    log("  接入方式：%s　拨号连接：%s　无线：%s" %
        (boot_cfg.get("mode"), boot_cfg.get("connection") or "(未配置)",
         boot_cfg.get("wifi_ssid") or "(未配置)"))
    run_cmd(["icacls", BOOT_DIR, "/grant", "*S-1-5-32-545:(OI)(CI)RX", "/T", "/C"], timeout=60)

    ok, how = register_task(log)
    if not ok:
        log("× 计划任务注册失败，请把上面的内容和 %s 一起反馈。" % INSTALL_LOG)
        return 1
    log("√ 计划任务已注册（%s）：%s" % (how, BOOT_TASK))

    # 先让旧实例退出，避免新旧两份代码同时跑
    try:
        with open(BOOT_STOP, "w", encoding="utf-8") as fh:
            fh.write("stop")
    except Exception:
        pass
    run_cmd(["schtasks", "/End", "/TN", BOOT_TASK], timeout=30)
    time.sleep(3)
    try:
        os.remove(BOOT_STOP)
    except OSError:
        pass

    run_cmd(["schtasks", "/Run", "/TN", BOOT_TASK], timeout=60)
    log("… 已启动系统级守护，等它写心跳（最多 60 秒）")
    alive, desc = False, ""
    deadline = time.time() + 60
    while time.time() < deadline:
        time.sleep(3)
        alive, desc = heartbeat_state()
        if alive:
            break
    if alive:
        log("√ 系统级守护已在运行：", desc)
    else:
        log("! 没等到心跳，请看日志：", BOOT_LOG)
        log(read_tail(BOOT_LOG, 15))
        return 1
    log("")
    log("完成：开机、锁屏、甚至还没登录时，它都会保持校园网在线。")
    return 0


def uninstall_boot(purge=False, log=print) -> int:
    log("=" * 62)
    log(" 卸载系统级守护")
    log("=" * 62)
    if not is_admin():
        log("× 需要管理员权限。")
        return 1
    try:
        os.makedirs(BOOT_DIR, exist_ok=True)
        with open(BOOT_STOP, "w", encoding="utf-8") as fh:
            fh.write("stop")
    except Exception:
        pass
    run_cmd(["schtasks", "/End", "/TN", BOOT_TASK], timeout=30)
    code, out = run_cmd(["schtasks", "/Delete", "/TN", BOOT_TASK, "/F"], timeout=30)
    log("√ 计划任务已删除" if code == 0 else "  计划任务不存在或删除失败：%s" % out)
    if purge:
        time.sleep(2)
        shutil.rmtree(BOOT_DIR, ignore_errors=True)
        log("√ 已删除系统级配置目录：", BOOT_DIR)
    return 0
