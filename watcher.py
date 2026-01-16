import random
import sys
import time
import traceback
from typing import Any, Dict, List, Set
import requests
from NJUlogin import pwdLogin
from serverchan_sdk import sc_send


# ===================== 你需要自定义的配置 =====================
USERNAME = "学号"
PASSWORD = "密码"

# Server酱（留给你自定义）
SENDKEY = "填写SendKey"
TITLE_TEMPLATE = "新成绩：{KCM}"   # 你可以改成固定标题，比如 "成绩更新"
OPTIONS = {"tags": "成绩"}        # 也可以改成 {} 或 None
# ============================================================


# eHall 登录目标（你之前跑通的那串）
DEST_SERVICE = (
    "https://ehall.nju.edu.cn:443/login?"
    "service=https%3A%2F%2Fehall.nju.edu.cn%2FappShow%3FappId%3D4768574631264620"
)

APPSHOW_URL = "https://ehall.nju.edu.cn/appShow?appId=4768574631264620"
GRADES_API = "https://ehallapp.nju.edu.cn/jwapp/sys/cjcx/modules/cjcx/cxxscjd.do"


def login_session() -> requests.Session:
    """按你之前那套方式登录，返回带登录态的 session。失败抛异常。"""
    login = pwdLogin(USERNAME, PASSWORD)
    session = login.login(DEST_SERVICE)
    if not getattr(login, "available", False):
        raise RuntimeError("NJUlogin 登录失败：login.available=False")

    # 访问一次应用入口，帮助 eHall 侧 cookie/跳转链更稳定
    session.get(APPSHOW_URL, timeout=20)
    return session


def fetch_grade_rows(session: requests.Session) -> List[Dict[str, Any]]:
    """拉取成绩 rows；失败抛异常。"""
    headers = {
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": APPSHOW_URL,
    }

    # 你抓包是这个接口；大多数情况下 POST 空表单即可
    resp = session.post(GRADES_API, data={}, headers=headers, timeout=20)

    # 常见掉线：会返回 HTML 登录页/重定向页，而不是 JSON
    try:
        data = resp.json()
    except Exception:
        ct = resp.headers.get("Content-Type", "")
        raise RuntimeError(
            f"成绩接口未返回 JSON（status={resp.status_code}, content-type={ct}），可能登录态失效"
        )

    if str(data.get("code")) != "0":
        raise RuntimeError(f"成绩接口返回 code != 0：code={data.get('code')}")

    rows = (
        data.get("datas", {})
        .get("cxxscjd", {})
        .get("rows", None)
    )
    if not isinstance(rows, list):
        raise RuntimeError("成绩 JSON 结构异常：找不到 datas.cxxscjd.rows（或 rows 不是 list）")

    return rows


def format_desp(row: Dict[str, Any]) -> str:
    """desp 固定为你要求的格式。"""
    kcm = str(row.get("KCM", "")).strip()
    xf = str(row.get("XF", "")).strip()
    zcj = str(row.get("ZCJ", "")).strip()
    return "科目：" + kcm + "\n学分：" + xf + "\n分数：" + zcj


def push_new_course(row: Dict[str, Any]) -> None:
    """发现新课程号就推送。"""
    title = TITLE_TEMPLATE.format(**row)
    desp = format_desp(row)
    sc_send(SENDKEY, title, desp, OPTIONS)


def push_fatal_error(err_msg: str) -> None:
    """连续失败3次：推送“程序出错”并退出。"""
    desp = f"程序连续失败 3 次，已退出。\n\n最后一次错误：\n{err_msg}"
    try:
        sc_send(SENDKEY, "程序出错", desp, OPTIONS)
    except Exception:
        # 推送也失败就算了，至少要退出
        pass


def relogin_and_fetch_with_retry(max_failures: int = 3, wait_seconds: int = 30) -> (requests.Session, List[Dict[str, Any]]):
    """
    获取失败就重新登录；失败等 30 秒重试；
    连续失败 max_failures 次则抛出异常（由上层负责推送“程序出错”并退出）。
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_failures + 1):
        try:
            session = login_session()
            rows = fetch_grade_rows(session)
            return session, rows
        except Exception as e:
            last_exc = e
            if attempt < max_failures:
                time.sleep(wait_seconds)

    raise RuntimeError(f"连续失败 {max_failures} 次：{last_exc}")


def main() -> int:
    # 启动时先拿一遍 rows，并把当时存在的 KCH 全部加入集合（不推送）
    try:
        session, rows = relogin_and_fetch_with_retry(max_failures=3, wait_seconds=30)
    except Exception as e:
        push_fatal_error(str(e))
        return 1

    seen_kch: Set[str] = set()
    for r in rows:
        kch = r.get("KCH")
        if kch:
            seen_kch.add(str(kch))

    print(f"程序启动：已记录 {len(seen_kch)} 个 KCH，不推送历史成绩。")

    # 轮询：每次 10秒~2分钟 随机间隔检查
    while True:
        time.sleep(random.uniform(10, 120))

        try:
            # 先用当前 session 试一次
            rows = fetch_grade_rows(session)
        except Exception:
            # 拉取失败 -> 走“重登 + 30s 重试”，最多 3 次
            try:
                session, rows = relogin_and_fetch_with_retry(max_failures=3, wait_seconds=30)
            except Exception as e:
                push_fatal_error(str(e))
                return 2

        # 检测新 KCH
        new_rows = []
        for r in rows:
            kch = r.get("KCH")
            if not kch:
                continue
            kch = str(kch)
            if kch not in seen_kch:
                new_rows.append(r)
                seen_kch.add(kch)

        if new_rows:
            print(f"发现 {len(new_rows)} 个新 KCH，开始推送…")
            for r in new_rows:
                try:
                    push_new_course(r)
                except Exception as e:
                    # 推送失败不算“获取失败”，这里只打印即可（你也可以改成算失败）
                    print(f"推送失败：{e}", file=sys.stderr)
        else:
            print("无新 KCH。")

    # unreachable
    # return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("已手动停止。")
