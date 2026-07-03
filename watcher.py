import random
import sys
import time
import traceback
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple
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

# 保存 Cookie 后，程序重启也会先尝试复用 eHall/统一身份认证登录态。
# 这个文件等同于登录态凭据，不要提交到 Git。
COOKIE_FILE = Path("nju_grade_cookies.txt")

# 只有 eHall/SSO 续期失败时才会密码登录，并限制密码登录频率，避免账号被冻结。
PASSWORD_LOGIN_COOLDOWN_SECONDS = 60 * 60
SESSION_REFRESH_RETRIES = 3
SESSION_REFRESH_WAIT_SECONDS = 30
# ============================================================


# eHall 登录目标（你之前跑通的那串）
DEST_SERVICE = (
    "https://ehall.nju.edu.cn:443/login?"
    "service=https%3A%2F%2Fehall.nju.edu.cn%2FappShow%3FappId%3D4768574631264620"
)

APPSHOW_URL = "https://ehall.nju.edu.cn/appShow?appId=4768574631264620"
GRADES_API = "https://ehallapp.nju.edu.cn/jwapp/sys/cjcx/modules/cjcx/cxxscjd.do"


class SessionExpired(RuntimeError):
    """成绩应用登录态已失效，可以尝试通过 eHall/SSO 续期。"""


class PasswordLoginThrottled(RuntimeError):
    """密码登录过于频繁，被本地冷却策略拦截。"""


_last_password_login_at: float | None = None


def response_looks_like_login_page(resp: requests.Response) -> bool:
    """判断响应是否已经掉到统一身份认证登录页。"""
    url = resp.url.lower()
    if "authserver.nju.edu.cn" in url and "login" in url:
        return True

    content_type = resp.headers.get("Content-Type", "").lower()
    if "text/html" not in content_type:
        return False

    text = resp.text[:4000]
    login_markers = (
        "统一身份认证",
        "authserver/login",
        "casLoginForm",
        "loginForm",
    )
    return any(marker in text for marker in login_markers)


def json_looks_like_login_expired(data: Dict[str, Any]) -> bool:
    msg = " ".join(
        str(data.get(key, ""))
        for key in ("msg", "message", "errmsg", "error", "errors")
    ).lower()
    markers = ("未登录", "登录", "登陆", "认证", "session", "timeout", "unauthorized")
    return any(marker in msg for marker in markers)


def load_cookie_session() -> requests.Session | None:
    """从本地 Cookie 文件恢复 session；文件不存在或不可用时返回 None。"""
    if not COOKIE_FILE.exists():
        return None

    jar = MozillaCookieJar(str(COOKIE_FILE))
    try:
        jar.load(ignore_discard=True, ignore_expires=False)
    except Exception as e:
        print(f"读取 Cookie 文件失败，将重新建立会话：{e}", file=sys.stderr)
        return None

    session = requests.Session()
    session.cookies = jar
    return session


def save_session_cookies(session: requests.Session) -> None:
    """保存当前 Cookie，降低程序重启后再次密码登录的概率。"""
    jar = MozillaCookieJar(str(COOKIE_FILE))
    for cookie in session.cookies:
        jar.set_cookie(cookie)

    try:
        jar.save(ignore_discard=True, ignore_expires=True)
    except Exception as e:
        print(f"保存 Cookie 文件失败：{e}", file=sys.stderr)


def refresh_app_session(session: requests.Session) -> bool:
    """
    不提交账号密码，只访问 eHall 应用入口。

    如果统一身份认证的 SSO 登录态仍有效，eHall 会自动跳回成绩应用并重新下发
    应用侧 Cookie；如果 SSO 也失效，最终通常会停在统一身份认证登录页。
    """
    resp = session.get(APPSHOW_URL, timeout=20, allow_redirects=True)
    if response_looks_like_login_page(resp):
        return False

    save_session_cookies(session)
    return True


def password_login_session() -> requests.Session:
    """真正提交统一身份认证账号密码，返回带登录态的 session。失败抛异常。"""
    global _last_password_login_at

    now = time.monotonic()
    if _last_password_login_at is not None:
        elapsed = now - _last_password_login_at
        if elapsed < PASSWORD_LOGIN_COOLDOWN_SECONDS:
            remaining = int(PASSWORD_LOGIN_COOLDOWN_SECONDS - elapsed)
            raise PasswordLoginThrottled(
                f"距离上次密码登录仅 {int(elapsed)} 秒，"
                f"为避免账号冻结，本次不再密码登录；还需等待约 {remaining} 秒"
            )

    _last_password_login_at = now
    login = pwdLogin(USERNAME, PASSWORD)
    session = login.login(DEST_SERVICE)
    if not getattr(login, "available", False):
        raise RuntimeError("NJUlogin 登录失败：login.available=False")

    # 访问一次应用入口，帮助 eHall/成绩应用侧 cookie/跳转链更稳定。
    if not refresh_app_session(session):
        raise RuntimeError("统一身份认证登录后仍无法进入成绩应用入口")

    save_session_cookies(session)
    return session


def fetch_grade_rows(session: requests.Session) -> List[Dict[str, Any]]:
    """拉取成绩 rows；失败抛异常。"""
    headers = {
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": APPSHOW_URL,
    }

    # 你抓包是这个接口；大多数情况下 POST 空表单即可
    resp = session.post(GRADES_API, data={}, headers=headers, timeout=20, allow_redirects=True)

    if response_looks_like_login_page(resp):
        raise SessionExpired("成绩接口跳转到统一身份认证登录页")

    # 常见掉线：会返回 HTML 登录页/重定向页，而不是 JSON
    try:
        data = resp.json()
    except Exception:
        ct = resp.headers.get("Content-Type", "")
        raise SessionExpired(
            f"成绩接口未返回 JSON（status={resp.status_code}, content-type={ct}），可能登录态失效"
        )

    if str(data.get("code")) != "0":
        if json_looks_like_login_expired(data):
            raise SessionExpired(f"成绩接口提示登录态失效：code={data.get('code')}")
        raise RuntimeError(f"成绩接口返回 code != 0：code={data.get('code')}")

    rows = (
        data.get("datas", {})
        .get("cxxscjd", {})
        .get("rows", None)
    )
    if not isinstance(rows, list):
        raise RuntimeError("成绩 JSON 结构异常：找不到 datas.cxxscjd.rows（或 rows 不是 list）")

    return rows


def fetch_with_session_recovery(
    session: requests.Session | None,
    max_refresh_failures: int = SESSION_REFRESH_RETRIES,
    wait_seconds: int = SESSION_REFRESH_WAIT_SECONDS,
) -> Tuple[requests.Session, List[Dict[str, Any]]]:
    """
    获取成绩，按风险从低到高恢复登录态：
    1. 直接复用当前 session；
    2. 访问 eHall 应用入口，借仍有效的 SSO 会话刷新成绩应用 Cookie；
    3. 只有前两步都确认是登录态问题时，才按冷却策略密码登录统一身份认证。
    """
    last_exc: Exception | None = None
    may_need_password_login = session is None

    if session is not None:
        try:
            rows = fetch_grade_rows(session)
            save_session_cookies(session)
            return session, rows
        except SessionExpired as e:
            last_exc = e
            may_need_password_login = True
        except Exception as e:
            last_exc = e

    if session is not None:
        for attempt in range(1, max_refresh_failures + 1):
            try:
                print(f"尝试通过 eHall/SSO 刷新成绩应用登录态（{attempt}/{max_refresh_failures}）...")
                if not refresh_app_session(session):
                    raise SessionExpired("SSO 登录态也已失效，需要密码登录")

                rows = fetch_grade_rows(session)
                save_session_cookies(session)
                return session, rows
            except SessionExpired as e:
                last_exc = e
                may_need_password_login = True
            except Exception as e:
                last_exc = e

            if attempt < max_refresh_failures:
                time.sleep(wait_seconds)

    if may_need_password_login:
        try:
            print("SSO 续期不可用，按冷却策略尝试一次统一身份认证密码登录...")
            new_session = password_login_session()
            rows = fetch_grade_rows(new_session)
            save_session_cookies(new_session)
            return new_session, rows
        except Exception as e:
            if last_exc is not None:
                raise RuntimeError(f"会话恢复失败；最后一次错误：{last_exc}；密码登录错误：{e}") from e
            raise

    raise RuntimeError(f"获取成绩失败，未进行密码登录（非登录态问题）：{last_exc}")


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


def main() -> int:
    # 启动时先拿一遍 rows，并把当时存在的 KCH 全部加入集合（不推送）
    session = load_cookie_session()
    try:
        session, rows = fetch_with_session_recovery(session)
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
            session, rows = fetch_with_session_recovery(session)
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
